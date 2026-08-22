import json
import sys
import unittest
from pathlib import Path

# Add backend directory to sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

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
from pricing import calculate_fares, haversine_distance


class TestUberBackend(unittest.TestCase):

    def setUp(self):
        init_db()

    def test_01_pricing_and_distance(self):
        # Test distance between SF points
        dist = haversine_distance(37.7749, -122.4194, 37.7833, -122.4167)
        self.assertGreater(dist, 0.5)

        # Test multi-category fare estimates
        fares = calculate_fares(37.7749, -122.4194, 37.7833, -122.4167)
        self.assertIn("UberX", fares["estimates"])
        self.assertIn("Comfort", fares["estimates"])
        self.assertIn("UberXL", fares["estimates"])
        self.assertIn("Black", fares["estimates"])
        self.assertGreater(fares["estimates"]["Black"]["fare"], fares["estimates"]["UberX"]["fare"])

    def test_02_auth_and_tokens(self):
        pwd_hash = hash_password("secure_pass_123")
        self.assertEqual(len(pwd_hash), 64)

        token = generate_token(101, "rider")
        payload = verify_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["user_id"], 101)
        self.assertEqual(payload["role"], "rider")

        bad_payload = verify_token("tampered:token:signature")
        self.assertIsNone(bad_payload)

    def test_03_ride_lifecycle(self):
        # 1. Request ride
        ride = request_ride(
            rider_id=1,
            pickup_addr="777 Market St",
            dropoff_addr="100 Van Ness Ave",
            pickup_lat=37.7749,
            pickup_lng=-122.4194,
            dropoff_lat=37.7833,
            dropoff_lng=-122.4167,
            fare=14.50,
            distance_km=2.3,
            ride_type="Comfort",
        )
        self.assertIsNotNone(ride)
        self.assertEqual(ride["status"], "REQUESTED")
        self.assertEqual(ride["ride_type"], "Comfort")
        rid = ride["id"]

        # 2. Driver accepts
        accepted = update_ride_status(rid, "ACCEPTED", driver_id=1)
        self.assertEqual(accepted["status"], "ACCEPTED")
        self.assertEqual(accepted["driver_id"], 1)

        # 3. Update driver location (GPS movement)
        updated = update_driver_location(rid, 37.7752, -122.4190)
        self.assertAlmostEqual(updated["driver_lat"], 37.7752, places=3)

        # 4. Complete ride
        completed = update_ride_status(rid, "COMPLETED")
        self.assertEqual(completed["status"], "COMPLETED")

    def test_04_drivers_and_rides_list(self):
        drivers = get_available_drivers()
        self.assertGreaterEqual(len(drivers), 1)

        all_rides = get_all_rides()
        self.assertGreaterEqual(len(all_rides), 1)


if __name__ == "__main__":
    unittest.main()
