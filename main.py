"""
Government Agent API - v1.0

A unified Saudi government services API simulating Absher / Tawakkalna / Muqeem
combined functionality for a WhatsApp-based government services agent demo.

7 endpoints (6 demo + /health + root):
  GET /                      - root (API info)
  GET /health                - status + data load counts
  GET /citizen               - workhorse: full citizen package
                               (profile, documents, dependents, vehicles, recent violations,
                                appointments, notifications, transactions, active service requests)
  GET /violations            - filtered violations history (status / vehicle / date range)
  GET /service               - service catalog lookup (by ID or category)
  GET /appointment-slots     - available appointment slots at offices
  GET /office                - office locator (by city, type, service)
  GET /fee                   - fee schedule lookup (by service ID, with SADAD codes)
"""
import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Government Agent API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

citizens = []
documents = []
dependents = []
vehicles = []
violations = []
appointments = []
notifications = []
transactions = []
services_catalog = []
offices_catalog = []
fees_catalog = []
service_requests = []


def load(filename):
    path = os.path.join(DATA_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def load_all_data():
    global citizens, documents, dependents, vehicles, violations
    global appointments, notifications, transactions
    global services_catalog, offices_catalog, fees_catalog, service_requests

    citizens = load("citizens.json")
    documents = load("documents.json")
    dependents = load("dependents.json")
    vehicles = load("vehicles.json")
    violations = load("violations.json")
    appointments = load("appointments.json")
    notifications = load("notifications.json")
    transactions = load("transactions.json")
    services_catalog = load("services_catalog.json")
    offices_catalog = load("offices_catalog.json")
    fees_catalog = load("fees_catalog.json")
    service_requests = load("service_requests.json")


@app.on_event("startup")
def startup():
    load_all_data()


# ============================================================
# Lookup helpers
# ============================================================

def find_citizen(cid):
    return next((c for c in citizens if c["Citizen ID"] == cid), None)


def find_citizen_by_phone(phone):
    return next((c for c in citizens if c["Phone"] == phone), None)


def find_citizen_by_nid(nid):
    return next((c for c in citizens if c["National ID / Iqama"] == nid), None)


def find_office(office_id):
    return next((o for o in offices_catalog if o["Office ID"] == office_id), None)


def find_service(service_id):
    return next((s for s in services_catalog if s["Service ID"] == service_id), None)


def find_vehicle(vehicle_id):
    return next((v for v in vehicles if v["Vehicle ID"] == vehicle_id), None)


def find_fee(service_id):
    return next((f for f in fees_catalog if f["Service ID"] == service_id), None)


# ============================================================
# Temporal recompute helpers
#
# Several JSON files store pre-computed time-derived fields:
#   documents.json:        Days Until Expiry, Status
#   vehicles.json:         Days Until Registration Expiry, Registration Status,
#                          Insurance Status, Periodic Inspection Status
#   service_requests.json: Reference Data["Days Awaiting Pickup"]
#
# These were computed at data-generation time and rot as days pass. The actual
# date strings (Expiry Date, Registration Expiry Date, Insurance Expiry, etc.)
# are the source of truth — we recompute the derived fields on every /citizen
# call so a citizen never sees stale "30 days remaining" when reality is 26 days.
# ============================================================

_MONTH_ABBREV = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_STEP_DATE_PATTERN = re.compile(r"\(([A-Z][a-z]{2}) (\d{1,2})\)")


def _parse_iso(date_str):
    """Parse 'YYYY-MM-DD' to date. Returns None on bad input."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _extract_latest_step_date(steps, fallback_year):
    """
    Pull the latest '(Mon DD)' date from a Steps Completed (EN) list.
    Used to recompute 'Days Awaiting Pickup' from the most recent
    'Document ready for collection' step rather than trusting a stale field.
    """
    latest = None
    for step in steps or []:
        for match in _STEP_DATE_PATTERN.finditer(step):
            mon_abbrev, day_str = match.group(1), match.group(2)
            mon_num = _MONTH_ABBREV.get(mon_abbrev)
            if not mon_num:
                continue
            try:
                d = date(fallback_year, mon_num, int(day_str))
            except ValueError:
                continue
            if latest is None or d > latest:
                latest = d
    return latest


def _recompute_temporal_fields(docs, vehs, active_reqs):
    """
    Mutates docs/vehs/active_reqs in-place to refresh time-derived fields
    against today's date. Source of truth is each record's date strings.

    Status vocabulary preserved:
      Documents:    Active / Expiring Soon / Expired   ('Pending Pickup' is preserved as-is)
      Registration: Active / Expiring Soon / Expired
      Insurance:    Active / Expiring Soon / Expired
      Inspection:   Valid / Required

    Thresholds: <0 days => Expired/Required;
                0-30 days => Expiring Soon (vehicles), 0-60 days => Expiring Soon (documents);
                else => Active/Valid.
    """
    today = datetime.now().date()

    for d in docs:
        exp_date = _parse_iso(d.get("Expiry Date"))
        if exp_date is None:
            continue
        days = (exp_date - today).days
        d["Days Until Expiry"] = days
        # 'Pending Pickup' is a workflow state, not a time state — preserve it.
        if d.get("Status") == "Pending Pickup":
            continue
        if days < 0:
            d["Status"] = "Expired"
        elif days <= 60:
            d["Status"] = "Expiring Soon"
        else:
            d["Status"] = "Active"

    for v in vehs:
        reg_date = _parse_iso(v.get("Registration Expiry Date"))
        if reg_date is not None:
            days = (reg_date - today).days
            v["Days Until Registration Expiry"] = days
            if days < 0:
                v["Registration Status"] = "Expired"
            elif days <= 30:
                v["Registration Status"] = "Expiring Soon"
            else:
                v["Registration Status"] = "Active"

        ins_date = _parse_iso(v.get("Insurance Expiry"))
        if ins_date is not None:
            days = (ins_date - today).days
            if days < 0:
                v["Insurance Status"] = "Expired"
            elif days <= 30:
                v["Insurance Status"] = "Expiring Soon"
            else:
                v["Insurance Status"] = "Active"

        insp_date = _parse_iso(v.get("Periodic Inspection Expiry"))
        if insp_date is not None:
            days = (insp_date - today).days
            v["Periodic Inspection Status"] = "Required" if days < 0 else "Valid"

    for r in active_reqs:
        if r.get("Status") != "Awaiting Pickup":
            continue
        ref = r.get("Reference Data") or {}
        if "Days Awaiting Pickup" not in ref:
            continue
        # Try to find the most recent step date (e.g. "Document ready for collection (Apr 25)").
        # Fallback: use Started Date.
        started = _parse_iso(r.get("Started Date"))
        fallback_year = started.year if started else today.year
        latest_step = _extract_latest_step_date(
            r.get("Steps Completed (EN)"), fallback_year
        )
        if latest_step is None:
            latest_step = started
        if latest_step is not None:
            ref["Days Awaiting Pickup"] = max(0, (today - latest_step).days)


# ============================================================
# Root + Health
# ============================================================

@app.get("/")
def root():
    return {
        "name": "Government Agent API",
        "version": "1.0",
        "description": "Unified Saudi government services API for the WhatsApp government agent demo",
        "endpoints": [
            "GET /health",
            "GET /citizen?citizen_id=CIT-001 | ?phone=+966... | ?national_id=...",
            "GET /violations?citizen_id=CIT-003&status=Unpaid",
            "GET /service?service_id=SVC-001 | ?category=Identity Documents",
            "GET /appointment-slots?service_id=SVC-005&city=Riyadh&date_from=2026-05-05",
            "GET /office?city=Riyadh&office_type=Jawazat",
            "GET /fee?service_id=SVC-005",
        ],
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "1.0",
        "data_loaded": {
            "citizens": len(citizens),
            "documents": len(documents),
            "dependents": len(dependents),
            "vehicles": len(vehicles),
            "violations": len(violations),
            "appointments": len(appointments),
            "notifications": len(notifications),
            "transactions": len(transactions),
            "services_catalog": len(services_catalog),
            "offices_catalog": len(offices_catalog),
            "fees_catalog": len(fees_catalog),
            "service_requests": len(service_requests),
        },
    }


# ============================================================
# /citizen — workhorse endpoint
# Returns everything about a citizen in one call
# ============================================================

@app.get("/citizen")
def get_citizen(
    citizen_id: Optional[str] = Query(None, description="Citizen ID, e.g., CIT-001"),
    phone: Optional[str] = Query(None, description="Phone number in international format"),
    national_id: Optional[str] = Query(None, description="National ID or Iqama number"),
    include_history: bool = Query(False, description="Include all transactions/violations history (default: recent only)"),
):
    """
    Returns a full citizen package: profile + documents + dependents + vehicles
    + recent violations + appointments + notifications + recent transactions
    + active service requests.

    Lookup precedence: citizen_id > phone > national_id (one is required).
    """
    citizen = None
    if citizen_id:
        citizen = find_citizen(citizen_id)
    elif phone:
        citizen = find_citizen_by_phone(phone)
    elif national_id:
        citizen = find_citizen_by_nid(national_id)
    else:
        raise HTTPException(
            status_code=400,
            detail="One of citizen_id, phone, or national_id is required.",
        )

    if not citizen:
        raise HTTPException(status_code=404, detail="Citizen not found.")

    cid = citizen["Citizen ID"]

    # All documents for this citizen, sorted by expiry (closest first; nulls last)
    docs = [d for d in documents if d["Citizen ID"] == cid]
    docs.sort(key=lambda d: (d.get("Expiry Date") is None, d.get("Expiry Date") or ""))

    # All dependents
    deps = [d for d in dependents if d["Citizen ID"] == cid]

    # All vehicles, with computed urgency flags
    vehs = [v for v in vehicles if v["Citizen ID"] == cid]

    # All violations for this citizen — sort newest first
    all_vios = [v for v in violations if v["Citizen ID"] == cid]
    all_vios.sort(key=lambda v: v["Date"], reverse=True)
    recent_vios = all_vios if include_history else all_vios[:10]

    # Counts and totals for unpaid violations (super useful for the agent)
    unpaid = [v for v in all_vios if v["Status"] == "Unpaid"]
    disputed = [v for v in all_vios if v["Status"] == "Disputed"]

    today_str = datetime.now().strftime("%Y-%m-%d")
    in_discount_window = [v for v in unpaid if v["Discount Window Ends"] >= today_str]
    past_discount_window = [v for v in unpaid if v["Discount Window Ends"] < today_str]

    violations_summary = {
        "total_unpaid_count": len(unpaid),
        "total_disputed_count": len(disputed),
        "total_unpaid_full_amount": round(sum(v["Fine Amount"] for v in unpaid), 2),
        "total_unpaid_with_discount": round(
            sum(v["Net If Paid Early"] if v["Discount Window Ends"] >= today_str else v["Fine Amount"]
                for v in unpaid), 2
        ),
        "in_discount_window_count": len(in_discount_window),
        "past_discount_window_count": len(past_discount_window),
    }

    # Appointments — upcoming first
    appts = [a for a in appointments if a["Citizen ID"] == cid]
    upcoming_appts = sorted(
        [a for a in appts if a["Status"] == "Confirmed" and a["Date"] >= today_str],
        key=lambda a: (a["Date"], a["Time"]),
    )
    past_appts = sorted(
        [a for a in appts if a["Date"] < today_str or a["Status"] in ("Completed", "Cancelled")],
        key=lambda a: a["Date"], reverse=True,
    )

    # Enrich each appointment with office info
    def enrich_appt(a):
        out = dict(a)
        office = find_office(a["Office ID"])
        if office:
            out["Office Address (EN)"] = office["Address (EN)"]
            out["Office Address (AR)"] = office["Address (AR)"]
            out["Office Phone"] = office["Phone"]
            out["Office Hours (EN)"] = office["Hours (EN)"]
        return out

    upcoming_appts = [enrich_appt(a) for a in upcoming_appts]

    # Notifications — unread first, sorted newest first
    notifs = [n for n in notifications if n["Citizen ID"] == cid]
    notifs.sort(key=lambda n: (n["Read"], -datetime.strptime(n["Created Date"], "%Y-%m-%d").timestamp()))
    recent_notifs = notifs if include_history else notifs[:10]

    unread_count = sum(1 for n in notifs if not n["Read"])
    urgent_count = sum(1 for n in notifs if not n["Read"] and n.get("Priority") in ("High", "Urgent"))

    # Transactions — newest first
    txns = [t for t in transactions if t["Citizen ID"] == cid]
    txns.sort(key=lambda t: (t["Date"], t["Time"]), reverse=True)
    recent_txns = txns if include_history else txns[:10]

    # Active service requests (in progress / awaiting pickup)
    reqs = [r for r in service_requests if r["Citizen ID"] == cid]
    active_reqs = [r for r in reqs if r["Status"] in ("In Progress", "Awaiting Pickup", "Pending Review")]

    # Refresh time-derived fields against today's date BEFORE computing urgency flags
    # so the agent always sees current Days Until Expiry / Status values, regardless
    # of how stale the stored JSON has become.
    _recompute_temporal_fields(docs, vehs, active_reqs)

    # Document urgency flags
    expiring_docs = [d for d in docs
                     if d.get("Days Until Expiry") is not None
                     and 0 <= d["Days Until Expiry"] <= 60]
    expired_docs = [d for d in docs
                    if d.get("Days Until Expiry") is not None
                    and d["Days Until Expiry"] < 0]

    documents_summary = {
        "total_count": len(docs),
        "expiring_within_60_days_count": len(expiring_docs),
        "expired_count": len(expired_docs),
        "pending_pickup_count": sum(1 for d in docs if d.get("Status") == "Pending Pickup"),
    }

    # Vehicle urgency flags
    vehicles_summary = {
        "total_count": len(vehs),
        "registration_expiring_count": sum(
            1 for v in vehs
            if v.get("Days Until Registration Expiry") is not None
            and 0 <= v["Days Until Registration Expiry"] <= 30
        ),
        "registration_expired_count": sum(
            1 for v in vehs
            if v.get("Days Until Registration Expiry") is not None
            and v["Days Until Registration Expiry"] < 0
        ),
    }

    return {
        "profile": citizen,
        "documents": docs,
        "documents_summary": documents_summary,
        "dependents": deps,
        "vehicles": vehs,
        "vehicles_summary": vehicles_summary,
        "recent_violations": recent_vios,
        "violations_summary": violations_summary,
        "upcoming_appointments": upcoming_appts,
        "past_appointments": past_appts[:5],
        "recent_notifications": recent_notifs,
        "notifications_summary": {
            "total_count": len(notifs),
            "unread_count": unread_count,
            "urgent_unread_count": urgent_count,
        },
        "recent_transactions": recent_txns,
        "active_service_requests": active_reqs,
    }


# ============================================================
# /violations — filtered violations
# ============================================================

@app.get("/violations")
def get_violations(
    citizen_id: Optional[str] = Query(None),
    vehicle_id: Optional[str] = Query(None),
    plate_number: Optional[str] = Query(None, description="Plate number (English format like 'ABC 1234')"),
    status: Optional[str] = Query(None, description="Unpaid / Paid / Disputed / Waived"),
    date_from: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    in_discount_window_only: bool = Query(False, description="Return only unpaid violations still in discount window"),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Filtered violation lookup. Most agent flows can use the data already returned
    by /citizen — call this only when the caller wants to filter by status, date range,
    or wants to see all violations beyond the recent set.
    """
    results = list(violations)

    if citizen_id:
        results = [v for v in results if v["Citizen ID"] == citizen_id]
    if vehicle_id:
        results = [v for v in results if v["Vehicle ID"] == vehicle_id]
    if plate_number:
        results = [v for v in results if v["Plate Number"] == plate_number]
    if status:
        results = [v for v in results if v["Status"].lower() == status.lower()]
    if date_from:
        results = [v for v in results if v["Date"] >= date_from]
    if date_to:
        results = [v for v in results if v["Date"] <= date_to]

    today_str = datetime.now().strftime("%Y-%m-%d")
    if in_discount_window_only:
        results = [v for v in results if v["Status"] == "Unpaid" and v["Discount Window Ends"] >= today_str]

    # Sort newest first
    results.sort(key=lambda v: v["Date"], reverse=True)
    results = results[:limit]

    # Compute summary
    unpaid_results = [v for v in results if v["Status"] == "Unpaid"]
    summary = {
        "total_count": len(results),
        "unpaid_count": len(unpaid_results),
        "paid_count": sum(1 for v in results if v["Status"] == "Paid"),
        "disputed_count": sum(1 for v in results if v["Status"] == "Disputed"),
        "total_unpaid_full_amount": round(sum(v["Fine Amount"] for v in unpaid_results), 2),
        "total_unpaid_with_discount": round(
            sum(v["Net If Paid Early"] if v["Discount Window Ends"] >= today_str else v["Fine Amount"]
                for v in unpaid_results), 2
        ),
    }

    return {"violations": results, "summary": summary}


# ============================================================
# /service — service catalog lookup
# ============================================================

@app.get("/service")
def get_service(
    service_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None, description="Identity Documents / Vehicles / Travel / Family / Business / Other"),
    available_online_only: bool = Query(False),
):
    """
    Look up a specific service by ID, or browse the catalog by category.
    """
    if service_id:
        svc = find_service(service_id)
        if not svc:
            raise HTTPException(status_code=404, detail=f"Service {service_id} not found.")
        # Enrich with fee info
        fee = find_fee(service_id)
        out = dict(svc)
        if fee:
            out["fee_details"] = fee
        return out

    results = list(services_catalog)
    if category:
        results = [s for s in results if s["Category"].lower() == category.lower()]
    if available_online_only:
        results = [s for s in results if s.get("Available Online")]

    return {"services": results, "count": len(results)}


# ============================================================
# /appointment-slots — synthesized available slots
# ============================================================

@app.get("/appointment-slots")
def get_appointment_slots(
    service_id: Optional[str] = Query(None),
    office_id: Optional[str] = Query(None),
    city: Optional[str] = Query(None, description="Riyadh / Jeddah / Mecca / Medina / Dammam / Khobar"),
    date_from: Optional[str] = Query(None, description="ISO date YYYY-MM-DD; defaults to today"),
    days: int = Query(14, ge=1, le=30, description="Number of forward days to scan"),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Returns available appointment slots. Slots are synthesized deterministically from
    each office's working hours.

    Filtering precedence: service_id narrows to offices that offer the service;
    office_id narrows further to a single office; city is an additional filter.
    """
    # Default date range
    today = datetime.now().date()
    if date_from:
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="date_from must be YYYY-MM-DD")
    else:
        start = today

    end = start + timedelta(days=days)

    # Pick eligible offices
    offices = list(offices_catalog)
    if office_id:
        offices = [o for o in offices if o["Office ID"] == office_id]
    if city:
        offices = [o for o in offices if o["City (EN)"].lower() == city.lower()]
    if service_id:
        svc = find_service(service_id)
        if not svc:
            raise HTTPException(status_code=404, detail=f"Service {service_id} not found")
        # Match by service name appearing in the office's services list
        svc_name = svc["Service Name (EN)"]
        offices = [o for o in offices if any(svc_name in s for s in o.get("Services Offered (EN)", []))]

    if not offices:
        return {"slots": [], "count": 0, "note": "No offices match the given filters."}

    # Pull all confirmed appointments to mark slots as taken
    confirmed = {(a["Office ID"], a["Date"], a["Time"]) for a in appointments if a["Status"] == "Confirmed"}

    # Synthesize slots: weekdays (Sun-Thu in Saudi) at 9:00, 10:00, 11:00, 13:00, 14:00 — first available
    slot_times = ["09:00", "10:00", "11:00", "13:00", "14:00"]
    saudi_weekend = {4, 5}  # Friday=4, Saturday=5 in Python's weekday() (Mon=0)

    slots = []
    cur = start
    while cur < end and len(slots) < limit * len(offices):
        if cur.weekday() in saudi_weekend:
            cur += timedelta(days=1)
            continue
        date_str = cur.strftime("%Y-%m-%d")
        for o in offices:
            for t in slot_times:
                if (o["Office ID"], date_str, t) in confirmed:
                    continue
                slots.append({
                    "Office ID": o["Office ID"],
                    "Office (EN)": o["Office Name (EN)"],
                    "Office (AR)": o["Office Name (AR)"],
                    "City (EN)": o["City (EN)"],
                    "Address (EN)": o["Address (EN)"],
                    "Date": date_str,
                    "Day of Week": cur.strftime("%A"),
                    "Time": t,
                })
        cur += timedelta(days=1)

    # Trim to limit
    slots = slots[:limit]

    return {"slots": slots, "count": len(slots)}


# ============================================================
# /office — office locator
# ============================================================

@app.get("/office")
def get_office(
    office_id: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    office_type: Optional[str] = Query(None, description="Jawazat / Civil Affairs / Muroor / Vehicle Inspection / Ministry of Commerce"),
    service: Optional[str] = Query(None, description="Service name (matches against services offered)"),
):
    """
    Look up a specific office or browse offices by city / type / service offered.
    """
    if office_id:
        office = find_office(office_id)
        if not office:
            raise HTTPException(status_code=404, detail=f"Office {office_id} not found.")
        return office

    results = list(offices_catalog)
    if city:
        results = [o for o in results if o["City (EN)"].lower() == city.lower()]
    if office_type:
        results = [o for o in results if o["Office Type"].lower() == office_type.lower()]
    if service:
        results = [o for o in results
                   if any(service.lower() in s.lower() for s in o.get("Services Offered (EN)", []))]

    return {"offices": results, "count": len(results)}


# ============================================================
# /fee — fee schedule lookup
# ============================================================

@app.get("/fee")
def get_fee(
    service_id: Optional[str] = Query(None),
    fee_id: Optional[str] = Query(None),
):
    """
    Look up the fee schedule for a service. Returns standard fee, express fee (if any),
    SADAD code, biller name, and validity period.
    """
    if not service_id and not fee_id:
        # Return the whole catalog
        return {"fees": fees_catalog, "count": len(fees_catalog)}

    if fee_id:
        fee = next((f for f in fees_catalog if f["Fee ID"] == fee_id), None)
        if not fee:
            raise HTTPException(status_code=404, detail=f"Fee {fee_id} not found.")
        return fee

    if service_id:
        fee = find_fee(service_id)
        if not fee:
            raise HTTPException(status_code=404, detail=f"No fee for service {service_id}.")
        return fee
