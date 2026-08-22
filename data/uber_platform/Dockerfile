# Uber NextGen Platform — Container Specification
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY backend/ ./backend/
COPY frontend/ ./frontend/

EXPOSE 8088

CMD ["python", "backend/app.py", "8088"]
