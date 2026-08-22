import math
from typing import Any, Dict

RIDE_RATES = {
    "UberX": {"base": 2.50, "per_km": 1.50, "capacity": 4, "multiplier": 1.0},
    "Comfort": {"base": 4.00, "per_km": 2.20, "capacity": 4, "multiplier": 1.3},
    "UberXL": {"base": 5.50, "per_km": 3.00, "capacity": 6, "multiplier": 1.6},
    "Black": {"base": 8.00, "per_km": 4.50, "capacity": 4, "multiplier": 2.2},
}


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates spherical distance between two GPS coordinates in kilometers."""
    R = 6371.0  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)


def calculate_fares(lat1: float, lon1: float, lat2: float, lon2: float) -> Dict[str, Any]:
    distance = haversine_distance(lat1, lon1, lat2, lon2)
    # Ensure minimum distance for short local hops
    dist = max(distance, 1.2)
    eta_mins = max(int(dist * 2.5), 3)

    estimates = {}
    for rtype, rate in RIDE_RATES.items():
        fare = round(rate["base"] + (dist * rate["per_km"]), 2)
        estimates[rtype] = {
            "fare": fare,
            "currency": "USD",
            "eta_minutes": eta_mins,
            "capacity": rate["capacity"],
            "distance_km": dist,
        }

    return {
        "distance_km": dist,
        "eta_minutes": eta_mins,
        "estimates": estimates,
    }
