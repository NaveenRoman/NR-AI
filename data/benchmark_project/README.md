# Full-Stack Task Management Application

Built autonomously by NR-AI.

## Architecture
- **Frontend**: React Single-Page Application with responsive grid layout, status filtering, and JWT token storage.
- **Backend**: Python HTTP REST API service with CORS, authentication middleware, and input validation.
- **Database**: SQLite with User and Task relation schemas.
- **Testing**: Python `unittest` suite covering database models, authentication, and CRUD endpoints.

## API Endpoints
- `GET /health`: Health check probe
- `POST /api/auth/register`: User registration
- `POST /api/auth/login`: User login & token generation
- `GET /api/tasks`: Retrieve user tasks (supports `?status=pending|completed`)
- `POST /api/tasks`: Create new task
- `PUT /api/tasks/<id>`: Update task title, description, priority, or status
- `DELETE /api/tasks/<id>`: Delete task

## Setup & Running
1. Run backend: `python backend/app.py 8088`
2. Run tests: `python -m unittest discover tests`
