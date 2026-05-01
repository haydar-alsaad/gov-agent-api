# Government Agent API

FastAPI backend for the Nebelus × T2 government services WhatsApp agent demo.

Simulates a unified Saudi government services portal (Absher / Tawakkalna / Muqeem) covering identity documents, vehicles, traffic violations, dependents, appointments at government offices, document downloads, and SADAD payments for government services.

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Then visit http://localhost:8000

## Deploy to Railway

This repo is Railway-ready — push to GitHub, connect to Railway, and it deploys with the `Procfile`. No env vars needed.

## Endpoints

7 endpoints (6 demo + `/health` + root):

| Endpoint | Purpose |
| --- | --- |
| `GET /` | API metadata |
| `GET /health` | Status + record counts per data file |
| `GET /citizen` | **Workhorse**: full citizen package (profile, documents, dependents, vehicles, recent violations, appointments, notifications, transactions, active service requests). Single call returns everything the agent needs. |
| `GET /violations` | Filtered violations history (status, vehicle, date range, in-discount-window) |
| `GET /service` | Service catalog (by ID or category) — covers ID renewal, passport renewal, vehicle services, business registration, etc. |
| `GET /appointment-slots` | Synthesized available appointment slots at offices |
| `GET /office` | Office locator (by city, type, service offered) |
| `GET /fee` | Fee schedule lookup with SADAD codes |

## Data

The `data/` folder contains 12 JSON files: 10 citizens, 35 documents, 16 dependents, 15 vehicles, 15 violations, 5 appointments, 21 notifications, 20 transactions, 13 services, 14 offices, 13 fees, 4 active service requests.

All cross-references are validated end-to-end. Every plate number on a violation matches a vehicle, every appointment service ID exists in the catalog, etc.

## Demo personas (quick reference)

| ID | Name | Lang | Best for demoing |
| --- | --- | --- | --- |
| **CIT-001** | Mohammed Al-Shammari | EN | English headline — full profile, 2 vehicles, family, upcoming passport renewal |
| **CIT-002** | Nora Al-Faisal | AR | Arabic headline — same depth in Arabic |
| CIT-003 | Khalid Al-Mutairi | EN | 5 unpaid violations — bulk SADAD payment |
| CIT-004 | Lama Al-Zahrani | AR | NID expires in 30 days — ID renewal flow |
| CIT-005 | Abdullah Al-Qahtani | AR | Large family — family services |
| CIT-006 | Sara Al-Dossary | AR | Vehicle reg expires in 7 days — registration renewal |
| CIT-007 | Faisal Al-Otaibi | EN | Disputed SAR 3000 violation — escalation flow |
| CIT-008 | Rajesh Kumar | EN | Indian expat — Iqama renewal in progress |
| CIT-009 | Fatima Al-Zamil | AR | Starting a business — CR issuance flow |
| CIT-010 | Yousef Al-Harbi | AR | Passport ready for pickup — collection flow |

## Quick test

```bash
# Health check
curl localhost:8000/health

# Get a citizen package
curl localhost:8000/citizen?citizen_id=CIT-001

# Pull all unpaid violations for the heavy persona
curl 'localhost:8000/violations?citizen_id=CIT-003&status=Unpaid'

# Look up a service
curl localhost:8000/service?service_id=SVC-005

# Find Riyadh Jawazat offices
curl 'localhost:8000/office?city=Riyadh&office_type=Jawazat'
```
