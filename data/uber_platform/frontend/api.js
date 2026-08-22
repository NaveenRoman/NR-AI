const API_BASE = window.location.origin;

const UberAPI = {
  async health() {
    const res = await fetch(`${API_BASE}/api/health`);
    return await res.json();
  },

  async estimateFares(pickupLat, pickupLng, dropoffLat, dropoffLng) {
    const res = await fetch(`${API_BASE}/api/rides/estimate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pickup_lat: pickupLat,
        pickup_lng: pickupLng,
        dropoff_lat: dropoffLat,
        dropoff_lng: dropoffLng,
      }),
    });
    return await res.json();
  },

  async requestRide(data) {
    const res = await fetch(`${API_BASE}/api/rides/request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return await res.json();
  },

  async getRide(rideId) {
    const res = await fetch(`${API_BASE}/api/rides/${rideId}`);
    return await res.json();
  },

  async acceptRide(rideId, driverId = 1) {
    const res = await fetch(`${API_BASE}/api/rides/${rideId}/accept`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ driver_id: driverId }),
    });
    return await res.json();
  },

  async updateLocation(rideId, lat, lng) {
    const res = await fetch(`${API_BASE}/api/rides/${rideId}/update_location`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat, lng }),
    });
    return await res.json();
  },

  async completeRide(rideId) {
    const res = await fetch(`${API_BASE}/api/rides/${rideId}/complete`, {
      method: "POST",
    });
    return await res.json();
  },

  async cancelRide(rideId) {
    const res = await fetch(`${API_BASE}/api/rides/${rideId}/cancel`, {
      method: "POST",
    });
    return await res.json();
  },

  async getNearbyDrivers() {
    const res = await fetch(`${API_BASE}/api/drivers/nearby`);
    return await res.json();
  },
};
