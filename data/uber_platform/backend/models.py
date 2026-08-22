import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "uber.db"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'rider',
                rating REAL DEFAULT 5.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS drivers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                vehicle_name TEXT NOT NULL,
                plate_number TEXT NOT NULL,
                ride_type TEXT DEFAULT 'UberX',
                is_online INTEGER DEFAULT 1,
                current_lat REAL DEFAULT 37.7749,
                current_lng REAL DEFAULT -122.4194,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS rides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rider_id INTEGER NOT NULL,
                driver_id INTEGER,
                pickup_address TEXT NOT NULL,
                dropoff_address TEXT NOT NULL,
                pickup_lat REAL NOT NULL,
                pickup_lng REAL NOT NULL,
                dropoff_lat REAL NOT NULL,
                dropoff_lng REAL NOT NULL,
                fare REAL NOT NULL,
                distance_km REAL NOT NULL,
                ride_type TEXT NOT NULL DEFAULT 'UberX',
                status TEXT NOT NULL DEFAULT 'REQUESTED',
                driver_lat REAL,
                driver_lng REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (rider_id) REFERENCES users(id),
                FOREIGN KEY (driver_id) REFERENCES drivers(id)
            )
        """)

        # Insert seed demo driver if not exists
        cur = conn.execute("SELECT id FROM users WHERE email = 'driver@uber.com'")
        if not cur.fetchone():
            conn.execute("""
                INSERT INTO users (name, email, password_hash, role, rating)
                VALUES ('Alex Rivera', 'driver@uber.com', 'demo_hash', 'driver', 4.95)
            """)
            uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("""
                INSERT INTO drivers (user_id, vehicle_name, plate_number, ride_type, is_online, current_lat, current_lng)
                VALUES (?, 'Tesla Model 3', '7XYZ987', 'UberX', 1, 37.7755, -122.4180)
            """, (uid,))
        conn.commit()


def create_user(name: str, email: str, password_hash: str, role: str = "rider") -> Dict[str, Any]:
    with get_db_connection() as conn:
        cur = conn.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            (name, email, password_hash, role),
        )
        conn.commit()
        uid = cur.lastrowid
        return {"id": uid, "name": name, "email": email, "role": role, "rating": 5.0}


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def request_ride(
    rider_id: int,
    pickup_addr: str,
    dropoff_addr: str,
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    fare: float,
    distance_km: float,
    ride_type: str = "UberX",
) -> Dict[str, Any]:
    with get_db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO rides (
                rider_id, pickup_address, dropoff_address,
                pickup_lat, pickup_lng, dropoff_lat, dropoff_lng,
                fare, distance_km, ride_type, status, driver_lat, driver_lng
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REQUESTED', ?, ?)
            """,
            (
                rider_id, pickup_addr, dropoff_addr,
                pickup_lat, pickup_lng, dropoff_lat, dropoff_lng,
                fare, distance_km, ride_type, pickup_lat + 0.005, pickup_lng - 0.005
            ),
        )
        conn.commit()
        rid = cur.lastrowid
        return get_ride_by_id(rid)  # type: ignore


def get_ride_by_id(ride_id: int) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM rides WHERE id = ?", (ride_id,)).fetchone()
        return dict(row) if row else None


def update_ride_status(ride_id: int, status: str, driver_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        if driver_id is not None:
            conn.execute("UPDATE rides SET status = ?, driver_id = ? WHERE id = ?", (status, driver_id, ride_id))
        else:
            conn.execute("UPDATE rides SET status = ? WHERE id = ?", (status, ride_id))
        conn.commit()
        return get_ride_by_id(ride_id)


def update_driver_location(ride_id: int, lat: float, lng: float) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        conn.execute("UPDATE rides SET driver_lat = ?, driver_lng = ? WHERE id = ?", (lat, lng, ride_id))
        conn.commit()
        return get_ride_by_id(ride_id)


def get_all_rides() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM rides ORDER BY id DESC LIMIT 50").fetchall()
        return [dict(r) for r in rows]


def get_available_drivers() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute("""
            SELECT d.*, u.name as driver_name, u.rating
            FROM drivers d
            JOIN users u ON d.user_id = u.id
            WHERE d.is_online = 1
        """).fetchall()
        return [dict(r) for r in rows]
