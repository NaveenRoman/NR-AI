/**
 * NR-AI Galaxy UI — Interactive Celestial Canvas & Command Center Engine
 * 60 FPS Canvas rendering, Black Hole Vortex, Dynamic Agent Binding, Real Telemetry Polling.
 */

// -----------------------------------------------------------------------------
// State Store
// -----------------------------------------------------------------------------
const state = {
  galaxy: null,
  selectedNode: null,
  searchQuery: "",
  statusFilter: "ALL",
  panX: 0,
  panY: 0,
  targetPanX: 0,
  targetPanY: 0,
  zoom: 1.0,
  targetZoom: 1.0,
  isDragging: false,
  dragStartX: 0,
  dragStartY: 0,
  particles: [],
  activeConnections: [],
  lastPoll: 0,
  ttsEnabled: true,
  speechSynth: window.speechSynthesis,
  recognition: null,
};

// -----------------------------------------------------------------------------
// Initialization
// -----------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  initClock();
  initCanvas();
  initParticles();
  initEventListeners();
  initSpeechRecognition();
  pollGalaxyState();
  setInterval(pollGalaxyState, 1500);
});

// -----------------------------------------------------------------------------
// Real-time Clock
// -----------------------------------------------------------------------------
function initClock() {
  function update() {
    const now = new Date();
    const timeElem = document.getElementById("clockTime");
    const dateElem = document.getElementById("clockDate");
    if (timeElem) {
      timeElem.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
    if (dateElem) {
      dateElem.textContent = now.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
    }
  }
  update();
  setInterval(update, 1000);
}

// -----------------------------------------------------------------------------
// Canvas & Render Loop
// -----------------------------------------------------------------------------
let canvas, ctx;

function initCanvas() {
  canvas = document.getElementById("galaxyCanvas");
  ctx = canvas.getContext("2d");
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);

  // Mouse pan/zoom events
  canvas.addEventListener("mousedown", (e) => {
    state.isDragging = true;
    state.dragStartX = e.clientX - state.targetPanX;
    state.dragStartY = e.clientY - state.targetPanY;
  });

  window.addEventListener("mousemove", (e) => {
    if (state.isDragging) {
      state.targetPanX = e.clientX - state.dragStartX;
      state.targetPanY = e.clientY - state.dragStartY;
    }
  });

  window.addEventListener("mouseup", () => {
    state.isDragging = false;
  });

  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
    state.targetZoom = Math.min(Math.max(state.targetZoom * zoomFactor, 0.4), 2.5);
  });

  // Click detection for nodes
  canvas.addEventListener("click", (e) => {
    const rect = canvas.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;
    handleCanvasClick(clickX, clickY);
  });

  // Start Animation Loop
  requestAnimationFrame(renderLoop);
}

function resizeCanvas() {
  if (!canvas) return;
  canvas.width = canvas.parentElement.clientWidth;
  canvas.height = canvas.parentElement.clientHeight;
}

// -----------------------------------------------------------------------------
// Black Hole Accretion Disk Particles
// -----------------------------------------------------------------------------
function initParticles() {
  const count = 180;
  for (let i = 0; i < count; i++) {
    state.particles.push({
      radius: 95 + Math.random() * 80,
      angle: Math.random() * Math.PI * 2,
      speed: (0.015 + Math.random() * 0.02) * (Math.random() > 0.5 ? 1 : 1),
      size: 1.2 + Math.random() * 2.5,
      hue: Math.random() > 0.5 ? (30 + Math.random() * 30) : (280 + Math.random() * 60),
      opacity: 0.3 + Math.random() * 0.7,
    });
  }
}

// -----------------------------------------------------------------------------
// Main Render Loop
// -----------------------------------------------------------------------------
let lastFrameTime = performance.now();

function renderLoop(now) {
  const dt = (now - lastFrameTime) / 1000;
  lastFrameTime = now;

  // Smooth camera interpolation
  state.panX += (state.targetPanX - state.panX) * 0.12;
  state.panY += (state.targetPanY - state.panY) * 0.12;
  state.zoom += (state.targetZoom - state.zoom) * 0.12;

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  ctx.save();
  // Center coordinate system
  ctx.translate(canvas.width / 2 + state.panX, canvas.height / 2 + state.panY);
  ctx.scale(state.zoom, state.zoom);

  // 1. Draw Concentric Orbits
  drawOrbits();

  // 2. Draw Connections to Central Core
  drawConnections();

  // 3. Draw Central Black Hole Intelligence Core
  drawCentralBlackHole(now);

  // 4. Draw Celestial Agent Nodes
  drawNodes();

  ctx.restore();

  if (!document.hidden) {
    requestAnimationFrame(renderLoop);
  } else {
    setTimeout(() => requestAnimationFrame(renderLoop), 200);
  }
}

// -----------------------------------------------------------------------------
// Drawing Helpers
// -----------------------------------------------------------------------------
function drawOrbits() {
  const rings = [220, 310, 390];
  ctx.save();
  for (const r of rings) {
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(56, 189, 248, 0.08)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 8]);
    ctx.stroke();
  }
  ctx.restore();
}

function drawConnections() {
  if (!state.galaxy || !state.galaxy.nodes) return;
  ctx.save();

  const now = performance.now() * 0.002;

  for (const node of state.galaxy.nodes) {
    const isFiltered = isNodeFilteredOut(node);
    const alpha = isFiltered ? 0.06 : 0.25;

    const rad = (node.orbit_angle * Math.PI) / 180;
    const nx = Math.cos(rad) * node.orbit_radius;
    const ny = Math.sin(rad) * node.orbit_radius;

    // Glowing connection line
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(nx, ny);

    if (node.status === "WORKING" || node.status === "THINKING") {
      ctx.strokeStyle = node.color;
      ctx.lineWidth = 2.5;
      ctx.shadowColor = node.color;
      ctx.shadowBlur = 12;
      ctx.stroke();

      // Traveling photon particle
      const t = (now % 1);
      const px = nx * t;
      const py = ny * t;
      ctx.beginPath();
      ctx.arc(px, py, 4, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff";
      ctx.shadowColor = "#ffffff";
      ctx.shadowBlur = 16;
      ctx.fill();
    } else {
      ctx.strokeStyle = `rgba(56, 189, 248, ${alpha})`;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 4]);
      ctx.stroke();
    }
  }
  ctx.restore();
}

function drawCentralBlackHole(now) {
  ctx.save();

  // 1. Accretion Disk Plasma Glow
  const glowRadius = 160;
  const gradient = ctx.createRadialGradient(0, 0, 40, 0, 0, glowRadius);
  gradient.addColorStop(0, "rgba(0, 0, 0, 1)");
  gradient.addColorStop(0.35, "rgba(245, 158, 11, 0.45)");
  gradient.addColorStop(0.65, "rgba(236, 72, 153, 0.35)");
  gradient.addColorStop(0.85, "rgba(56, 189, 248, 0.25)");
  gradient.addColorStop(1, "rgba(10, 15, 30, 0)");

  ctx.beginPath();
  ctx.arc(0, 0, glowRadius, 0, Math.PI * 2);
  ctx.fillStyle = gradient;
  ctx.fill();

  // 2. Rotating Particles in Accretion Disk
  for (const p of state.particles) {
    p.angle += p.speed;
    const px = Math.cos(p.angle) * p.radius;
    const py = Math.sin(p.angle) * p.radius;

    ctx.beginPath();
    ctx.arc(px, py, p.size, 0, Math.PI * 2);
    ctx.fillStyle = `hsla(${p.hue}, 95%, 65%, ${p.opacity})`;
    ctx.shadowColor = `hsla(${p.hue}, 95%, 65%, 0.8)`;
    ctx.shadowBlur = 8;
    ctx.fill();
  }

  // 3. Black Hole Event Horizon (Deep Black Void)
  ctx.beginPath();
  ctx.arc(0, 0, 75, 0, Math.PI * 2);
  ctx.fillStyle = "#020409";
  ctx.shadowColor = "#38bdf8";
  ctx.shadowBlur = 30;
  ctx.fill();

  // Inner border ring
  ctx.beginPath();
  ctx.arc(0, 0, 75, 0, Math.PI * 2);
  ctx.strokeStyle = "rgba(56, 189, 248, 0.6)";
  ctx.lineWidth = 2;
  ctx.stroke();

  // Central pulsating intelligence waveform
  const isEStop = state.galaxy && state.galaxy.central_core.status === "STOPPED";
  drawWaveformInCore(now, isEStop);

  ctx.restore();
}

function drawWaveformInCore(now, isEStop) {
  ctx.save();
  const width = 80;
  const numBars = 16;
  const step = width / numBars;
  const startX = -width / 2;

  ctx.beginPath();
  ctx.strokeStyle = isEStop ? "#ef4444" : "#38bdf8";
  ctx.lineWidth = 2;
  ctx.lineCap = "round";

  for (let i = 0; i < numBars; i++) {
    const x = startX + i * step;
    const wave = isEStop ? 0 : Math.sin(now * 0.005 + i * 0.5) * 10;
    ctx.moveTo(x, -wave);
    ctx.lineTo(x, wave);
  }
  ctx.stroke();
  ctx.restore();
}

function drawNodes() {
  if (!state.galaxy || !state.galaxy.nodes) return;

  for (const node of state.galaxy.nodes) {
    const isFiltered = isNodeFilteredOut(node);
    const isSelected = state.selectedNode && state.selectedNode.agent_id === node.agent_id;

    ctx.save();
    const rad = (node.orbit_angle * Math.PI) / 180;
    const x = Math.cos(rad) * node.orbit_radius;
    const y = Math.sin(rad) * node.orbit_radius;

    ctx.translate(x, y);

    if (isFiltered) {
      ctx.globalAlpha = 0.2;
    }

    // Outer Glow Ring
    const baseRadius = 26;
    ctx.beginPath();
    ctx.arc(0, 0, baseRadius + 8, 0, Math.PI * 2);
    ctx.fillStyle = node.glow;
    ctx.fill();

    // Node Sphere
    ctx.beginPath();
    ctx.arc(0, 0, baseRadius, 0, Math.PI * 2);
    ctx.fillStyle = "#0f172a";
    ctx.shadowColor = node.color;
    ctx.shadowBlur = isSelected ? 32 : 18;
    ctx.fill();

    // Node Border
    ctx.beginPath();
    ctx.arc(0, 0, baseRadius, 0, Math.PI * 2);
    ctx.strokeStyle = isSelected ? "#ffffff" : node.color;
    ctx.lineWidth = isSelected ? 3 : 2;
    ctx.stroke();

    // Agent Icon Text/Symbol
    ctx.font = "bold 13px var(--font-sans)";
    ctx.fillStyle = "#ffffff";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(getIconGlyph(node.icon_type), 0, -2);

    // Friendly Name Label
    ctx.font = "bold 12px var(--font-sans)";
    ctx.fillStyle = "#ffffff";
    ctx.fillText(node.friendly_name, 0, baseRadius + 15);

    // Role Subtitle
    ctx.font = "9px var(--font-sans)";
    ctx.fillStyle = "#94a3b8";
    ctx.fillText(node.role, 0, baseRadius + 27);

    // Status Pill Badge
    const statusText = `● ${node.status}`;
    ctx.font = "bold 9px var(--font-sans)";
    ctx.fillStyle = node.status_color;
    ctx.fillText(statusText, 0, baseRadius + 39);

    ctx.restore();
  }
}

function getIconGlyph(type) {
  const glyphs = {
    android: "🤖",
    visual_studio: "💻",
    unity: "🎮",
    unreal: "🚀",
    book: "📖",
    gear: "⚙️",
    nexus: "🧬",
    shield: "🛡️",
    beaker: "🔬",
    mic: "🎙️",
    eye: "👁️",
    code: "⚡",
    monitor: "🖥️",
    cpu: "💠",
  };
  return glyphs[type] || "✦";
}

function isNodeFilteredOut(node) {
  if (state.statusFilter !== "ALL" && node.status !== state.statusFilter) {
    return true;
  }
  if (state.searchQuery) {
    const q = state.searchQuery.toLowerCase();
    const match =
      node.friendly_name.toLowerCase().includes(q) ||
      node.role.toLowerCase().includes(q) ||
      node.capabilities.some(c => c.toLowerCase().includes(q));
    return !match;
  }
  return false;
}

// -----------------------------------------------------------------------------
// Hit Detection & Agent Selection
// -----------------------------------------------------------------------------
function handleCanvasClick(screenX, screenY) {
  if (!state.galaxy || !state.galaxy.nodes) return;

  // Convert screen coordinates to world coordinates
  const worldX = (screenX - (canvas.width / 2 + state.panX)) / state.zoom;
  const worldY = (screenY - (canvas.height / 2 + state.panY)) / state.zoom;

  let clickedNode = null;
  const hitRadius = 35;

  for (const node of state.galaxy.nodes) {
    const rad = (node.orbit_angle * Math.PI) / 180;
    const nx = Math.cos(rad) * node.orbit_radius;
    const ny = Math.sin(rad) * node.orbit_radius;
    const dist = Math.hypot(worldX - nx, worldY - ny);

    if (dist <= hitRadius) {
      clickedNode = node;
      break;
    }
  }

  if (clickedNode) {
    selectAgent(clickedNode);
  }
}

function selectAgent(node) {
  state.selectedNode = node;

  // Smoothly center on selected agent node
  const rad = (node.orbit_angle * Math.PI) / 180;
  state.targetPanX = -Math.cos(rad) * node.orbit_radius * state.zoom;
  state.targetPanY = -Math.sin(rad) * node.orbit_radius * state.zoom;
  state.targetZoom = 1.35;

  renderAgentPanel(node);

  // Voice greeting if enabled
  if (state.ttsEnabled && node.greeting) {
    speak(node.greeting);
  }
}

// -----------------------------------------------------------------------------
// Agent Interaction Panel Rendering
// -----------------------------------------------------------------------------
function renderAgentPanel(node) {
  const panel = document.getElementById("agentPanel");
  if (!panel) return;

  document.getElementById("panelAgentAvatar").textContent = getIconGlyph(node.icon_type);
  document.getElementById("panelAgentAvatar").style.borderColor = node.color;
  document.getElementById("panelAgentAvatar").style.boxShadow = `0 0 16px ${node.glow}`;

  document.getElementById("panelAgentName").textContent = node.friendly_name;
  document.getElementById("panelAgentRole").textContent = node.role;

  const statusBadge = document.getElementById("panelStatusPill");
  statusBadge.textContent = `● ${node.status}`;
  statusBadge.style.color = node.status_color;
  statusBadge.style.borderColor = node.status_color;
  statusBadge.style.background = `${node.status_color}18`;

  document.getElementById("panelGreeting").textContent = node.greeting;

  // Render capability action buttons
  const actionGrid = document.getElementById("panelActionsGrid");
  actionGrid.innerHTML = "";
  if (node.suggested_actions && node.suggested_actions.length > 0) {
    for (const act of node.suggested_actions) {
      const btn = document.createElement("button");
      btn.className = "action-card-btn";
      btn.innerHTML = `<span>▶</span> <span>${act.label}</span>`;
      btn.onclick = () => executeAgentAction(node.agent_id, act.id, act.label);
      actionGrid.appendChild(btn);
    }
  }

  // Current task card
  const taskCard = document.getElementById("panelTaskCard");
  if (node.current_task && node.status === "WORKING") {
    taskCard.style.display = "block";
    document.getElementById("panelTaskTitle").textContent = node.current_task.description || "Executing workflow...";
    document.getElementById("panelTaskProgress").style.width = `${node.current_task.progress || 68}%`;
  } else {
    taskCard.style.display = "none";
  }

  // Chat stream reset
  const chatHistory = document.getElementById("panelChatHistory");
  chatHistory.innerHTML = `
    <div class="chat-bubble agent">${node.greeting}</div>
  `;

  panel.classList.add("open");
}

function closeAgentPanel() {
  const panel = document.getElementById("agentPanel");
  if (panel) panel.classList.remove("open");
  state.selectedNode = null;
  state.targetPanX = 0;
  state.targetPanY = 0;
  state.targetZoom = 1.0;
}

// -----------------------------------------------------------------------------
// Action Execution & Global Commands
// -----------------------------------------------------------------------------
async function executeAgentAction(agentId, actionId, label) {
  const chatHistory = document.getElementById("panelChatHistory");
  const userMsg = document.createElement("div");
  userMsg.className = "chat-bubble user";
  userMsg.textContent = label;
  chatHistory.appendChild(userMsg);
  chatHistory.scrollTop = chatHistory.scrollHeight;

  const thinkingMsg = document.createElement("div");
  thinkingMsg.className = "chat-bubble agent";
  thinkingMsg.textContent = "Processing action...";
  chatHistory.appendChild(thinkingMsg);

  try {
    const res = await fetch(`/api/agent/${agentId}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_id: actionId, label }),
    });
    const data = await res.json();
    thinkingMsg.textContent = data.result || data.message || "Done, Boss.";
    if (state.ttsEnabled && thinkingMsg.textContent) {
      speak(thinkingMsg.textContent);
    }
  } catch (err) {
    thinkingMsg.textContent = "Error executing action: " + err;
  }
  pollGalaxyState();
}

async function sendGlobalCommand() {
  const input = document.getElementById("globalCommandInput");
  const cmd = input.value.trim();
  if (!cmd) return;

  input.value = "";
  input.placeholder = "Processing command...";

  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: cmd }),
    });
    const data = await res.json();
    const replyText = data.text || data.response || "Command executed.";

    // If an agent panel is open, append to its conversation
    const chatHistory = document.getElementById("panelChatHistory");
    if (chatHistory && state.selectedNode) {
      chatHistory.innerHTML += `
        <div class="chat-bubble user">${cmd}</div>
        <div class="chat-bubble agent">${replyText}</div>
      `;
      chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    if (state.ttsEnabled) {
      speak(replyText);
    }
  } catch (e) {
    console.error("Command error:", e);
  } finally {
    input.placeholder = "Ask NR-AI or any agent anything...";
    pollGalaxyState();
  }
}

async function triggerEmergencyStop() {
  if (!confirm("TRIGGER EMERGENCY STOP: Halt all active agent workflows and computer actions immediately?")) {
    return;
  }
  try {
    const res = await fetch("/api/emergency_stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ triggered_by: "GalaxyUI_Operator", reason: "Operator triggered 1-touch Emergency Stop" }),
    });
    const data = await res.json();
    alert("EMERGENCY STOP TRIGGERED: " + (data.message || "All workflows halted."));
    pollGalaxyState();
  } catch (e) {
    alert("Failed to trigger emergency stop: " + e);
  }
}

// -----------------------------------------------------------------------------
// Real Telemetry Polling
// -----------------------------------------------------------------------------
async function pollGalaxyState() {
  try {
    const res = await fetch("/api/galaxy/state");
    if (!res.ok) return;
    const data = await res.json();
    if (!data.success) return;

    state.galaxy = data;

    // Update bottom-left real metrics
    const m = data.system_metrics;
    if (m) {
      document.getElementById("metricCpuVal").textContent = `${m.cpu_percent}%`;
      document.getElementById("metricCpuBar").style.width = `${m.cpu_percent}%`;

      document.getElementById("metricMemVal").textContent = `${m.memory_percent}%`;
      document.getElementById("metricMemBar").style.width = `${m.memory_percent}%`;

      document.getElementById("metricAgentsVal").textContent = m.agents_metric_display;
      document.getElementById("metricAgentsBar").style.width = `${(m.active_agents_count / Math.max(m.total_registered_agents, 1)) * 100}%`;
    }

    // Update Central Core text
    const core = data.central_core;
    if (core) {
      const coreInd = document.getElementById("coreStatusIndicator");
      if (coreInd) coreInd.textContent = core.status_indicator;
    }
  } catch (err) {
    console.warn("Galaxy state poll failed:", err);
  }
}

// -----------------------------------------------------------------------------
// Speech Recognition & Synthesis
// -----------------------------------------------------------------------------
function initSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    const micBtn = document.getElementById("micBtn");
    if (micBtn) micBtn.title = "Voice recognition not supported in this browser";
    return;
  }

  state.recognition = new SpeechRecognition();
  state.recognition.continuous = false;
  state.recognition.interimResults = false;
  state.recognition.lang = "en-US";

  state.recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    const input = document.getElementById("globalCommandInput");
    if (input) {
      input.value = transcript;
      sendGlobalCommand();
    }
  };

  state.recognition.onerror = (e) => {
    console.warn("Speech recognition error:", e.error);
  };
}

function toggleVoiceInput() {
  if (!state.recognition) {
    alert("Speech recognition is not supported in this browser. Please use text input.");
    return;
  }
  try {
    state.recognition.start();
    const micBtn = document.getElementById("micBtn");
    if (micBtn) micBtn.style.color = "#ef4444";
  } catch (e) {
    state.recognition.stop();
  }
}

function speak(text) {
  if (!state.speechSynth || !state.ttsEnabled) return;
  // Clean markdown and special symbols before speaking
  const clean = text.replace(/[*#`_~[\]()<>]/g, "").replace(/http\S+/g, "").slice(0, 350);
  state.speechSynth.cancel();
  const utter = new SpeechSynthesisUtterance(clean);
  utter.rate = 1.05;
  utter.pitch = 1.0;
  state.speechSynth.speak(utter);
}

// -----------------------------------------------------------------------------
// Search & Filter Events
// -----------------------------------------------------------------------------
function initEventListeners() {
  const searchInput = document.getElementById("globalSearchInput");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      state.searchQuery = e.target.value.trim();
    });
  }

  const cmdInput = document.getElementById("globalCommandInput");
  if (cmdInput) {
    cmdInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        sendGlobalCommand();
      }
    });
  }
}
