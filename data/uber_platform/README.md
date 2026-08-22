# Uber NextGen — Ride Hailing & Autonomous Mobility Platform

An autonomous full-stack mobility application built by NR-AI.

## Architecture
- **Backend**: Python HTTP/REST microservice with SQLite ORM, token-based authentication, and geospatial pricing engine.
- **Frontend**: High-contrast glassmorphic Dark Theme SPA with Leaflet maps, animated vehicle tracking, and dual Rider / Driver dispatch views.
- **State Machine**: `REQUESTED` -> `ACCEPTED` -> `ARRIVING` -> `IN_PROGRESS` -> `COMPLETED`.

## API Endpoints
- `GET /api/health` — Service health probe
- `POST /api/auth/register` — Register rider or driver
- `POST /api/auth/login` — Authenticate and retrieve token
- `POST /api/rides/estimate` — Calculate multi-category fares (UberX, Comfort, UberXL, Black)
- `POST /api/rides/request` — Create ride dispatch request
- `POST /api/rides/<id>/accept` — Driver acceptance
- `POST /api/rides/<id>/update_location` — GPS coordinates stream
- `POST /api/rides/<id>/complete` — Ride completion & receipt

## Running the Application
```bash
python backend/app.py 8088
```
Navigate to `http://127.0.0.1:8088` in any modern web browser.
