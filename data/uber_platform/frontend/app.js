// Uber NextGen — Interactive Map & Ride State Machine
let map;
let pickupMarker;
let dropoffMarker;
let driverMarker;
let routeLine;

let currentRide = null;
let selectedTier = "UberX";
let isDriverOnline = true;
let driverSimInterval = null;

const SF_COORDS = {
  pickup: [37.7749, -122.4194],  // Market St
  dropoff: [37.7879, -122.4075], // Union Square
  driver: [37.7710, -122.4240],  // Incoming driver start point
};

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  setupEventListeners();
  updateFares();
});

function initMap() {
  // Initialize Leaflet map centered in San Francisco
  map = L.map("map-view", {
    zoomControl: false,
  }).setView([37.7770, -122.4150], 14);

  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    subdomains: 'abcd',
    maxZoom: 19,
  }).addTo(map);

  L.control.zoom({ position: "bottomright" }).addTo(map);

  // Custom vehicle & pin icons
  const pickupIcon = L.divIcon({
    className: "custom-pin",
    html: '<div style="background:#05a357;width:14px;height:14px;border-radius:50%;border:2px solid white;box-shadow:0 0 8px #05a357;"></div>',
    iconSize: [14, 14],
  });

  const dropoffIcon = L.divIcon({
    className: "custom-pin",
    html: '<div style="background:#ffffff;width:14px;height:14px;border-radius:2px;border:2px solid #111;box-shadow:0 0 8px white;"></div>',
    iconSize: [14, 14],
  });

  const driverIcon = L.divIcon({
    className: "driver-pin",
    html: '<div style="background:#276ef1;color:white;padding:4px 8px;border-radius:12px;font-size:10px;font-weight:bold;box-shadow:0 2px 8px rgba(0,0,0,0.6);">🚗 Uber</div>',
    iconSize: [40, 20],
  });

  pickupMarker = L.marker(SF_COORDS.pickup, { icon: pickupIcon }).addTo(map);
  dropoffMarker = L.marker(SF_COORDS.dropoff, { icon: dropoffIcon }).addTo(map);
  driverMarker = L.marker(SF_COORDS.driver, { icon: driverIcon }).addTo(map);

  // Draw Route Polyline
  routeLine = L.polyline([SF_COORDS.pickup, SF_COORDS.dropoff], {
    color: "#276ef1",
    weight: 4,
    opacity: 0.8,
    dashArray: "6, 8",
  }).addTo(map);
}

function setupEventListeners() {
  // Mode Switch
  document.getElementById("btn-rider-mode").addEventListener("click", () => {
    switchMode("rider");
  });
  document.getElementById("btn-driver-mode").addEventListener("click", () => {
    switchMode("driver");
  });

  // Ride Tier Selection
  document.querySelectorAll(".tier-card").forEach((card) => {
    card.addEventListener("click", () => {
      document.querySelectorAll(".tier-card").forEach((c) => c.classList.remove("active"));
      card.classList.add("active");
      selectedTier = card.dataset.type;
      document.getElementById("btn-request-ride").innerText = `Request ${selectedTier}`;
    });
  });

  // Request Ride
  document.getElementById("btn-request-ride").addEventListener("click", handleRideRequest);
  document.getElementById("btn-cancel-ride").addEventListener("click", handleCancelRide);

  // Driver Mode Controls
  const toggleBtn = document.getElementById("btn-toggle-online");
  toggleBtn.addEventListener("click", () => {
    isDriverOnline = !isDriverOnline;
    toggleBtn.className = `online-toggle-btn ${isDriverOnline ? "online" : "offline"}`;
    toggleBtn.innerText = isDriverOnline ? "ONLINE" : "OFFLINE";
    document.getElementById("driver-status-text").innerText = isDriverOnline
      ? "You are currently ONLINE"
      : "You are currently OFFLINE";
  });

  document.getElementById("btn-accept-dispatch").addEventListener("click", handleAcceptDispatch);
}

function switchMode(mode) {
  const riderBtn = document.getElementById("btn-rider-mode");
  const driverBtn = document.getElementById("btn-driver-mode");
  const riderPanel = document.getElementById("rider-panel");
  const driverPanel = document.getElementById("driver-panel");

  if (mode === "rider") {
    riderBtn.classList.add("active");
    driverBtn.classList.remove("active");
    riderPanel.classList.add("active");
    driverPanel.classList.remove("active");
  } else {
    driverBtn.classList.add("active");
    riderBtn.classList.remove("active");
    driverPanel.classList.add("active");
    riderPanel.classList.remove("active");
  }
}

async function updateFares() {
  try {
    const data = await UberAPI.estimateFares(
      SF_COORDS.pickup[0], SF_COORDS.pickup[1],
      SF_COORDS.dropoff[0], SF_COORDS.dropoff[1]
    );
    if (data.estimates) {
      document.getElementById("price-uberx").innerText = `$${data.estimates.UberX.fare.toFixed(2)}`;
      document.getElementById("price-comfort").innerText = `$${data.estimates.Comfort.fare.toFixed(2)}`;
      document.getElementById("price-uberxl").innerText = `$${data.estimates.UberXL.fare.toFixed(2)}`;
      document.getElementById("price-black").innerText = `$${data.estimates.Black.fare.toFixed(2)}`;
    }
  } catch (e) {
    console.log("Using fallback fare estimates.");
  }
}

async function handleRideRequest() {
  const statusCard = document.getElementById("ride-status-card");
  const requestBtn = document.getElementById("btn-request-ride");

  requestBtn.disabled = true;
  requestBtn.innerText = "Connecting to Dispatch...";
  statusCard.classList.remove("hidden");
  document.getElementById("status-title").innerText = "Looking for nearby drivers...";

  try {
    const res = await UberAPI.requestRide({
      rider_id: 1,
      pickup_address: document.getElementById("pickup-input").value,
      dropoff_address: document.getElementById("dropoff-input").value,
      pickup_lat: SF_COORDS.pickup[0],
      pickup_lng: SF_COORDS.pickup[1],
      dropoff_lat: SF_COORDS.dropoff[0],
      dropoff_lng: SF_COORDS.dropoff[1],
      ride_type: selectedTier,
    });

    currentRide = res.ride;

    // Simulate Driver match in 2.5 seconds
    setTimeout(() => {
      document.getElementById("status-title").innerText = "Driver Assigned • On the way!";
      document.getElementById("driver-profile").classList.remove("hidden");
      startDriverSimulation();
    }, 2500);

  } catch (e) {
    console.error(e);
  }
}

function startDriverSimulation() {
  let progress = 0;
  const startLat = SF_COORDS.driver[0];
  const startLng = SF_COORDS.driver[1];
  const targetLat = SF_COORDS.pickup[0];
  const targetLng = SF_COORDS.pickup[1];

  if (driverSimInterval) clearInterval(driverSimInterval);

  driverSimInterval = setInterval(() => {
    progress += 0.05;
    if (progress >= 1.0) {
      clearInterval(driverSimInterval);
      document.getElementById("status-title").innerText = "Driver has ARRIVED at pickup spot!";
      document.getElementById("eta-timer").innerText = "0 mins";
      return;
    }

    const curLat = startLat + (targetLat - startLat) * progress;
    const curLng = startLng + (targetLng - startLng) * progress;
    driverMarker.setLatLng([curLat, curLng]);

    const remainingMins = Math.max(1, Math.round(3 * (1 - progress)));
    document.getElementById("eta-timer").innerText = `${remainingMins} mins`;
  }, 500);
}

function handleAcceptDispatch() {
  const incCard = document.getElementById("incoming-request-card");
  incCard.innerHTML = '<div style="color:#05a357;font-weight:bold;text-align:center;padding:12px;">✅ Trip Accepted • Navigation Started</div>';
  startDriverSimulation();
}

function handleCancelRide() {
  if (driverSimInterval) clearInterval(driverSimInterval);
  document.getElementById("ride-status-card").classList.add("hidden");
  const requestBtn = document.getElementById("btn-request-ride");
  requestBtn.disabled = false;
  requestBtn.innerText = `Request ${selectedTier}`;
  driverMarker.setLatLng(SF_COORDS.driver);
}
