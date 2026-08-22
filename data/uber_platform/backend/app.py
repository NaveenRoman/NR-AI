import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# Ensure local backend imports resolve
sys.path.insert(0, str(Path(__file__).resolve().parent))

from auth import generate_token, hash_password, verify_token
from models import (
    create_user,
    get_all_rides,
    get_available_drivers,
    get_ride_by_id,
    get_user_by_email,
    init_db,
    request_ride,
    update_driver_location,
    update_ride_status,
)
from pricing import calculate_fares

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class UberAPIHandler(BaseHTTPRequestHandler):

    def _send_json(self, status: int, data: Any) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, filepath: Path, content_type: str) -> None:
        if not filepath.exists():
            self.send_error(404, f"File Not Found: {filepath.name}")
            return
        content = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _read_json_body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                raw = self.rfile.read(length).decode("utf-8")
                return json.loads(raw)
        except Exception:
            pass
        return {}

    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path

        # Static Frontend routing
        if path in {"/", "/index.html"}:
            return self._send_file(FRONTEND_DIR / "index.html", "text/html")
        elif path == "/styles.css":
            return self._send_file(FRONTEND_DIR / "styles.css", "text/css")
        elif path == "/app.js":
            return self._send_file(FRONTEND_DIR / "app.js", "application/javascript")
        elif path == "/api.js":
            return self._send_file(FRONTEND_DIR / "api.js", "application/javascript")

        # Health Probe
        if path == "/api/health":
            return self._send_json(200, {
                "status": "healthy",
                "service": "Uber Platform Core API",
                "version": "1.0.0",
                "uptime": "online",
            })

        # List All Rides
        if path == "/api/rides":
            rides = get_all_rides()
            return self._send_json(200, {"rides": rides, "total": len(rides)})

        # Get Single Ride
        if path.startswith("/api/rides/"):
            try:
                rid = int(path.split("/")[-1])
                ride = get_ride_by_id(rid)
                if ride:
                    return self._send_json(200, ride)
                return self._send_json(404, {"error": "Ride not found"})
            except Exception:
                return self._send_json(400, {"error": "Invalid ride id"})

        # Get Available Drivers
        if path == "/api/drivers/nearby":
            drivers = get_available_drivers()
            return self._send_json(200, {"drivers": drivers, "count": len(drivers)})

        return self._send_json(404, {"error": "Endpoint not found"})

    def do_POST(self) -> None:
        url = urlparse(self.path)
        path = url.path
        body = self._read_json_body()

        # Auth Register
        if path == "/api/auth/register":
            name = body.get("name", "User")
            email = body.get("email", "")
            password = body.get("password", "")
            role = body.get("role", "rider")
            if not email or not password:
                return self._send_json(400, {"error": "Email and password required"})
            if get_user_by_email(email):
                return self._send_json(409, {"error": "User already exists"})
            user = create_user(name, email, hash_password(password), role)
            token = generate_token(user["id"], role)
            return self._send_json(201, {"user": user, "token": token})

        # Auth Login
        if path == "/api/auth/login":
            email = body.get("email", "")
            password = body.get("password", "")
            user = get_user_by_email(email)
            if not user or user["password_hash"] != hash_password(password):
                return self._send_json(401, {"error": "Invalid credentials"})
            token = generate_token(user["id"], user["role"])
            return self._send_json(200, {
                "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]},
                "token": token,
            })

        # Fare Estimation
        if path == "/api/rides/estimate":
            lat1 = float(body.get("pickup_lat", 37.7749))
            lon1 = float(body.get("pickup_lng", -122.4194))
            lat2 = float(body.get("dropoff_lat", 37.7833))
            lon2 = float(body.get("dropoff_lng", -122.4167))
            estimates = calculate_fares(lat1, lon1, lat2, lon2)
            return self._send_json(200, estimates)

        # Ride Request Booking
        if path == "/api/rides/request":
            rider_id = int(body.get("rider_id", 1))
            pickup_addr = body.get("pickup_address", "Market Street, SF")
            dropoff_addr = body.get("dropoff_address", "Union Square, SF")
            pickup_lat = float(body.get("pickup_lat", 37.7749))
            pickup_lng = float(body.get("pickup_lng", -122.4194))
            dropoff_lat = float(body.get("dropoff_lat", 37.7833))
            dropoff_lng = float(body.get("dropoff_lng", -122.4167))
            ride_type = body.get("ride_type", "UberX")
            estimates = calculate_fares(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
            fare = estimates["estimates"].get(ride_type, {}).get("fare", 12.50)
            distance = estimates["distance_km"]

            ride = request_ride(
                rider_id, pickup_addr, dropoff_addr,
                pickup_lat, pickup_lng, dropoff_lat, dropoff_lng,
                fare, distance, ride_type
            )
            return self._send_json(201, {"ride": ride, "status": "REQUESTED", "message": "Driver dispatch initiated"})

        # Accept Ride (Driver)
        if "/accept" in path:
            try:
                rid = int(path.split("/")[3])
                driver_id = int(body.get("driver_id", 1))
                ride = update_ride_status(rid, "ACCEPTED", driver_id)
                return self._send_json(200, {"ride": ride, "message": "Ride accepted by driver"})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})

        # Update Driver Location
        if "/update_location" in path:
            try:
                rid = int(path.split("/")[3])
                lat = float(body.get("lat", 37.7750))
                lng = float(body.get("lng", -122.4190))
                ride = update_driver_location(rid, lat, lng)
                return self._send_json(200, {"ride": ride})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})

        # Complete Ride
        if "/complete" in path:
            try:
                rid = int(path.split("/")[3])
                ride = update_ride_status(rid, "COMPLETED")
                return self._send_json(200, {"ride": ride, "message": "Ride completed successfully"})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})

        # Cancel Ride
        if "/cancel" in path:
            try:
                rid = int(path.split("/")[3])
                ride = update_ride_status(rid, "CANCELLED")
                return self._send_json(200, {"ride": ride, "message": "Ride cancelled"})
            except Exception as e:
                return self._send_json(400, {"error": str(e)})

        return self._send_json(404, {"error": "Endpoint not found"})


def run_server(port: int = 8088, host: str = "127.0.0.1") -> None:
    init_db()
    server = HTTPServer((host, port), UberAPIHandler)
    print(f"[UberPlatform] Core API & Web UI running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[UberPlatform] Server stopped.")
        server.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
    run_server(port=port)
