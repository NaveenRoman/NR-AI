
// -----------------------------------------------------------------------------
// Speech Interruption / Barge-in & Central Return
// -----------------------------------------------------------------------------
function interruptSpeech() {
  if (state.isSpeakingAudio || (state.speechSynth && state.speechSynth.speaking)) {
    if (state.speechSynth) {
      state.speechSynth.cancel();
    }
    state.isSpeakingAudio = false;
    const voiceNotice = document.getElementById("introVoiceNotice");
    if (voiceNotice) {
      voiceNotice.textContent = "USER INTERRUPTED • LISTENING";
      voiceNotice.style.display = "block";
    }
    fetch("/api/conversation/interrupt", { method: "POST" }).catch(() => {});
  }
}

async function returnToCentral() {
  state.activeConversationAgent = null;
  state.activeConversationAgentName = null;
  const banner = document.getElementById("activeChatBanner");
  if (banner) banner.style.display = "none";

  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: "NR-AI" }),
    });
    const data = await res.json();
    const reply = data.text || "Resumed central orchestration.";
    if (state.ttsEnabled) speakText(reply);
  } catch (e) {
    console.error(e);
  }
  pollGalaxyState();
}
/**
 * NR-AI Galaxy UI — Interactive Celestial Canvas & Command Center Engine
 * 60 FPS Canvas rendering, Black Hole Vortex, Dynamic Agent Binding, Real Telemetry Polling.
 * Refinement: Fixed stable galaxy composition (zero mouse zoom/pan) and Galaxy Introduction Mode.
 */

// -----------------------------------------------------------------------------
// State Store
// -----------------------------------------------------------------------------
const state = {
  galaxy: null,
  selectedNode: null,
  activeConversationAgent: null,
  activeConversationAgentName: null,
  searchQuery: "",
  statusFilter: "ALL",

  // Fixed camera composition — zero mouse drift/zoom
  panX: 0,
  panY: 0,
  zoom: 1.0,

  particles: [],
  activeConnections: [],
  lastPoll: 0,
  ttsEnabled: true,
  speechSynth: window.speechSynthesis,
  recognition: null,

  // Introduction Mode State
  introMode: false,
  introPaused: false,
  introStep: -1, // -1: inactive, 0: NR-AI core intro, 1..N: agents, N+1: NR-AI outro
  introQueue: [],
  introGreeting: "Of course, Boss. Let me introduce you to my agents.",
  introOutro: "That's my current agent team, Boss. Tell me what you want to build, learn, research, or solve.",
  currentSpeakerId: null,
  isSpeakingAudio: false,
  introTimer: null,
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
// Canvas & Render Loop (Fixed Composition — Zero Mouse Movement Camera Shifts)
// -----------------------------------------------------------------------------
let canvas, ctx;

function initCanvas() {
  canvas = document.getElementById("galaxyCanvas");
  ctx = canvas.getContext("2d");
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);

  // Click detection for selecting agent nodes (NO camera translation or zooming)
  canvas.addEventListener("click", (e) => {
    const rect = canvas.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;
    handleCanvasClick(clickX, clickY);
  });

  // Start 60 FPS Render Loop
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
      radius: 95 + Math.random() * 85,
      angle: Math.random() * Math.PI * 2,
      speed: (0.015 + Math.random() * 0.02),
      size: 1.2 + Math.random() * 2.5,
      hue: Math.random() > 0.5 ? (30 + Math.random() * 30) : (280 + Math.random() * 60),
      opacity: 0.3 + Math.random() * 0.7,
    });
  }
}

// -----------------------------------------------------------------------------
// Main Render Loop — 60 FPS Rock-Solid Fixed Composition
// -----------------------------------------------------------------------------
let lastFrameTime = performance.now();

function renderLoop(now) {
  const dt = (now - lastFrameTime) / 1000;
  lastFrameTime = now;

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  ctx.save();
  // Fixed composition centered in canvas — zero mouse pan or zoom drift
  ctx.translate(canvas.width / 2, canvas.height / 2);
  ctx.scale(1.0, 1.0);

  // 1. Draw Static Concentric Orbital Rings
  drawOrbits();

  // 2. Draw Gravitational Energy Beams to Agents
  drawConnections(now);

  // 3. Draw Central Black Hole Intelligence Core
  drawCentralBlackHole(now);

  // 4. Draw Celestial Agent Nodes (with speaker spotlighting)
  drawNodes(now);

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
function getNodePosition(node, now) {
  // Slow, elegant continuous orbital revolution without camera translation
  const ring = node.orbit_ring || 1;
  const speed = 0.00003 * (4 - ring);
  const rad = ((node.orbit_angle * Math.PI) / 180) + (now ? now * speed : 0);
  return {
    x: Math.cos(rad) * node.orbit_radius,
    y: Math.sin(rad) * node.orbit_radius,
    angleRad: rad,
  };
}

function drawOrbits() {
  const rings = (state.galaxy && state.galaxy.orbital_rings) || [260, 400, 540];
  ctx.save();
  for (const r of rings) {
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(56, 189, 248, 0.09)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([4, 10]);
    ctx.stroke();
  }
  ctx.restore();
}

function drawConnections(now) {
  if (!state.galaxy || !state.galaxy.nodes) return;
  ctx.save();

  const timeSec = now * 0.002;
  const nodeMap = new Map();
  for (const node of state.galaxy.nodes) {
    nodeMap.set(node.agent_id, node);
  }

  // Render connections list (Central and Trinity Inter-Agent)
  if (state.galaxy.connections && Array.isArray(state.galaxy.connections)) {
    for (const conn of state.galaxy.connections) {
      if (conn.from === "nr_ai_central_intelligence") {
        const toNode = nodeMap.get(conn.to);
        if (!toNode) continue;
        const isSpeaker = state.introMode && state.currentSpeakerId === toNode.agent_id;
        const isFiltered = isNodeFilteredOut(toNode);
        const alpha = isFiltered ? 0.05 : (state.introMode && !isSpeaker ? 0.12 : 0.25);

        const pos = getNodePosition(toNode, now);
        const nx = pos.x;
        const ny = pos.y;

        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(nx, ny);

        if (isSpeaker) {
          ctx.strokeStyle = "#00f0ff";
          ctx.lineWidth = 3.5;
          ctx.shadowColor = "#00f0ff";
          ctx.shadowBlur = 18;
          ctx.stroke();

          for (let k = 0; k < 3; k++) {
            const t = ((timeSec * 2.0 + k * 0.33) % 1);
            const px = nx * t;
            const py = ny * t;
            ctx.beginPath();
            ctx.arc(px, py, 4.5, 0, Math.PI * 2);
            ctx.fillStyle = "#ffffff";
            ctx.shadowColor = "#00f0ff";
            ctx.shadowBlur = 16;
            ctx.fill();
          }
        } else if (conn.animated || toNode.status === "WORKING" || toNode.status === "THINKING") {
          ctx.strokeStyle = conn.color || toNode.color;
          ctx.lineWidth = 2.5;
          ctx.shadowColor = conn.color || toNode.color;
          ctx.shadowBlur = 12;
          ctx.stroke();

          const t = (timeSec % 1);
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
          ctx.setLineDash([]);
        }
      } else if (conn.is_trinity) {
        // Inter-Agent Trinity Department Connection (Knowledge <-> Nova <-> Aegis)
        const fromNode = nodeMap.get(conn.from);
        const toNode = nodeMap.get(conn.to);
        if (!fromNode || !toNode) continue;

        const posA = getNodePosition(fromNode, now);
        const posB = getNodePosition(toNode, now);

        ctx.beginPath();
        ctx.moveTo(posA.x, posA.y);
        ctx.lineTo(posB.x, posB.y);

        if (conn.animated) {
          ctx.strokeStyle = conn.color || "#06b6d4";
          ctx.lineWidth = 2.8;
          ctx.shadowColor = conn.glow || conn.color;
          ctx.shadowBlur = 16;
          ctx.stroke();

          for (let k = 0; k < 2; k++) {
            const t = ((timeSec * 1.8 + k * 0.5) % 1);
            const px = posA.x + (posB.x - posA.x) * t;
            const py = posA.y + (posB.y - posA.y) * t;
            ctx.beginPath();
            ctx.arc(px, py, 3.5, 0, Math.PI * 2);
            ctx.fillStyle = "#ffffff";
            ctx.shadowColor = conn.color || "#06b6d4";
            ctx.shadowBlur = 14;
            ctx.fill();
          }
        } else {
          // Luminous idle harmonic bond for Trinity cluster
          ctx.strokeStyle = conn.glow || "rgba(6, 182, 212, 0.35)";
          ctx.lineWidth = 1.4;
          ctx.setLineDash([4, 6]);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
    }
  } else {
    // Fallback: draw straight central beams to nodes
    for (const node of state.galaxy.nodes) {
      const pos = getNodePosition(node, now);
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(pos.x, pos.y);
      ctx.strokeStyle = `rgba(56, 189, 248, 0.25)`;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }
  ctx.restore();
}

function drawCentralBlackHole(now) {
  ctx.save();

  const isCoreSpeaker = state.introMode && state.currentSpeakerId === "nr_ai_central_intelligence";
  const pulseFactor = isCoreSpeaker ? 1.0 + 0.08 * Math.sin(now * 0.008) : 1.0;

  // 1. Accretion Disk Plasma Glow
  const glowRadius = 160 * pulseFactor;
  const gradient = ctx.createRadialGradient(0, 0, 40, 0, 0, glowRadius);
  gradient.addColorStop(0, "rgba(0, 0, 0, 1)");
  gradient.addColorStop(0.35, isCoreSpeaker ? "rgba(245, 158, 11, 0.65)" : "rgba(245, 158, 11, 0.45)");
  gradient.addColorStop(0.65, isCoreSpeaker ? "rgba(236, 72, 153, 0.55)" : "rgba(236, 72, 153, 0.35)");
  gradient.addColorStop(0.85, isCoreSpeaker ? "rgba(0, 240, 255, 0.45)" : "rgba(56, 189, 248, 0.25)");
  gradient.addColorStop(1, "rgba(10, 15, 30, 0)");

  ctx.beginPath();
  ctx.arc(0, 0, glowRadius, 0, Math.PI * 2);
  ctx.fillStyle = gradient;
  ctx.fill();

  // 2. Rotating Particles in Accretion Disk
  const speedMult = isCoreSpeaker ? 1.8 : 1.0;
  for (const p of state.particles) {
    p.angle += p.speed * speedMult;
    const px = Math.cos(p.angle) * p.radius * pulseFactor;
    const py = Math.sin(p.angle) * p.radius * pulseFactor;

    ctx.beginPath();
    ctx.arc(px, py, p.size, 0, Math.PI * 2);
    ctx.fillStyle = `hsla(${p.hue}, 95%, 65%, ${p.opacity})`;
    ctx.shadowColor = `hsla(${p.hue}, 95%, 65%, 0.8)`;
    ctx.shadowBlur = 8;
    ctx.fill();
  }

  // 3. Black Hole Event Horizon (Deep Black Void)
  const coreR = 75 * pulseFactor;
  ctx.beginPath();
  ctx.arc(0, 0, coreR, 0, Math.PI * 2);
  ctx.fillStyle = "#020409";
  ctx.shadowColor = isCoreSpeaker ? "#00f0ff" : "#38bdf8";
  ctx.shadowBlur = isCoreSpeaker ? 45 : 30;
  ctx.fill();

  // Inner border ring
  ctx.beginPath();
  ctx.arc(0, 0, coreR, 0, Math.PI * 2);
  ctx.strokeStyle = isCoreSpeaker ? "rgba(0, 240, 255, 0.9)" : "rgba(56, 189, 248, 0.6)";
  ctx.lineWidth = isCoreSpeaker ? 3 : 2;
  ctx.stroke();

  // Central pulsating intelligence waveform
  const isEStop = state.galaxy && state.galaxy.central_core.status === "STOPPED";
  drawWaveformInCore(now, isEStop, isCoreSpeaker);

  ctx.restore();
}

function drawWaveformInCore(now, isEStop, isCoreSpeaker) {
  ctx.save();
  const width = 84;
  const numBars = 16;
  const step = width / numBars;
  const startX = -width / 2;

  ctx.beginPath();
  ctx.strokeStyle = isEStop ? "#ef4444" : (isCoreSpeaker ? "#00f0ff" : "#38bdf8");
  ctx.lineWidth = isCoreSpeaker ? 3 : 2;
  ctx.lineCap = "round";

  const amp = isEStop ? 0 : (isCoreSpeaker ? 18 : 10);
  for (let i = 0; i < numBars; i++) {
    const x = startX + i * step;
    const wave = Math.sin(now * 0.006 + i * 0.5) * amp;
    ctx.moveTo(x, -wave);
    ctx.lineTo(x, wave);
  }
  ctx.stroke();
  ctx.restore();
}

function drawNodes(now) {
  if (!state.galaxy || !state.galaxy.nodes) return;

  for (const node of state.galaxy.nodes) {
    const isFiltered = isNodeFilteredOut(node);
    const isSelected = state.selectedNode && state.selectedNode.agent_id === node.agent_id;
    const isSpeaker = state.introMode && state.currentSpeakerId === node.agent_id;

    ctx.save();
    const pos = getNodePosition(node, now);
    const x = pos.x;
    const y = pos.y;

    ctx.translate(x, y);

    const activeAgentId = state.activeConversationAgent || (state.galaxy && state.galaxy.active_conversation_agent);
    const isActiveChat = activeAgentId && (activeAgentId.toLowerCase() === node.agent_id.toLowerCase() || activeAgentId.toLowerCase() === node.friendly_name.toLowerCase());

    // If intro mode is active, subdue non-speaking agents to 35% opacity
    if (state.introMode && !isSpeaker) {
      ctx.globalAlpha = 0.35;
    } else if (isFiltered) {
      ctx.globalAlpha = 0.2;
    } else {
      ctx.globalAlpha = 1.0;
    }

    if (isSpeaker) {
      // 🌟 THE STAR OF THE GALAXY 🌟
      const speakerRadius = ((node.base_radius || 26) + 6) + Math.sin(now * 0.008) * 3;

      // Outer Breathing Concentric Glowing Rings
      ctx.beginPath();
      ctx.arc(0, 0, speakerRadius + 28, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(0, 240, 255, 0.12)";
      ctx.fill();

      ctx.beginPath();
      ctx.arc(0, 0, speakerRadius + 14, 0, Math.PI * 2);
      ctx.fillStyle = node.glow || "rgba(0, 240, 255, 0.35)";
      ctx.fill();

      ctx.beginPath();
      ctx.arc(0, 0, speakerRadius + 8, 0, Math.PI * 2);
      ctx.strokeStyle = "#00f0ff";
      ctx.lineWidth = 2.5;
      ctx.shadowColor = "#00f0ff";
      ctx.shadowBlur = 24;
      ctx.stroke();

      // Orbiting Spark Particles around Speaker
      for (let s = 0; s < 4; s++) {
        const sAngle = (now * 0.004 + (s * Math.PI) / 2);
        const sx = Math.cos(sAngle) * (speakerRadius + 16);
        const sy = Math.sin(sAngle) * (speakerRadius + 16);
        ctx.beginPath();
        ctx.arc(sx, sy, 2.5, 0, Math.PI * 2);
        ctx.fillStyle = "#ffffff";
        ctx.shadowColor = "#00f0ff";
        ctx.shadowBlur = 10;
        ctx.fill();
      }

      // Speaker Node Sphere
      ctx.beginPath();
      ctx.arc(0, 0, speakerRadius, 0, Math.PI * 2);
      ctx.fillStyle = "#091326";
      ctx.shadowColor = "#00f0ff";
      ctx.shadowBlur = 35;
      ctx.fill();

      ctx.beginPath();
      ctx.arc(0, 0, speakerRadius, 0, Math.PI * 2);
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 3;
      ctx.stroke();

      // Agent Icon
      ctx.font = "bold 17px var(--font-sans)";
      ctx.fillStyle = "#ffffff";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(getIconGlyph(node.icon_type), 0, -2);

      // Prominent Bold Name
      ctx.font = "bold 15px var(--font-sans)";
      ctx.fillStyle = "#ffffff";
      ctx.shadowColor = "#00f0ff";
      ctx.shadowBlur = 12;
      ctx.fillText(node.friendly_name.toUpperCase(), 0, speakerRadius + 18);
      ctx.shadowBlur = 0;

      // Role Subtitle
      ctx.font = "bold 10px var(--font-sans)";
      ctx.fillStyle = "#38bdf8";
      ctx.fillText(node.role, 0, speakerRadius + 32);

      // ● SPEAKING Badge Pill
      ctx.fillStyle = "rgba(16, 185, 129, 0.2)";
      ctx.strokeStyle = "#10b981";
      ctx.lineWidth = 1;
      const bW = 84;
      const bH = 18;
      ctx.beginPath();
      ctx.roundRect(-bW / 2, speakerRadius + 38, bW, bH, 9);
      ctx.fill();
      ctx.stroke();

      ctx.font = "bold 9px var(--font-sans)";
      ctx.fillStyle = "#10b981";
      ctx.fillText("● SPEAKING", 0, speakerRadius + 47);

      // Animated Mini Equalizer below badge while audio is active
      if (state.isSpeakingAudio) {
        drawMiniEqualizer(0, speakerRadius + 64, now);
      }
    } else {
      // Normal Node (Knowledge: 34px, Nova/Aegis: 24px, Specialists: 26px)
      const baseRadius = node.base_radius || 26;

      // Outer Glow Ring
      ctx.beginPath();
      ctx.arc(0, 0, baseRadius + 8, 0, Math.PI * 2);
      ctx.fillStyle = node.glow;
      ctx.fill();

      // Distinctive Epistemic Anchor Ring for Universal Knowledge Engine
      if (node.agent_id === "universal_knowledge_engine") {
        ctx.beginPath();
        ctx.arc(0, 0, baseRadius + 7, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(59, 130, 246, 0.75)";
        ctx.lineWidth = 2.2;
        ctx.stroke();
      }

      if (isActiveChat) {
        // High-emphasis active conversational focus halo
        ctx.beginPath();
        ctx.arc(0, 0, baseRadius + 14, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(0, 240, 255, 0.18)";
        ctx.fill();

        ctx.beginPath();
        ctx.arc(0, 0, baseRadius + 8, 0, Math.PI * 2);
        ctx.strokeStyle = "#00f0ff";
        ctx.lineWidth = 2;
        ctx.shadowColor = "#00f0ff";
        ctx.shadowBlur = 18;
        ctx.stroke();
      }

      // Node Sphere
      ctx.beginPath();
      ctx.arc(0, 0, baseRadius, 0, Math.PI * 2);
      ctx.fillStyle = "#0f172a";
      ctx.shadowColor = isActiveChat ? "#00f0ff" : node.color;
      ctx.shadowBlur = (isSelected || isActiveChat) ? 32 : 18;
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
      const statusText = isActiveChat ? "● ACTIVE CHAT" : `● ${node.status}`;
      ctx.font = "bold 9px var(--font-sans)";
      ctx.fillStyle = isActiveChat ? "#00f0ff" : (node.status_color || "#10b981");
      ctx.fillText(statusText, 0, baseRadius + 39);
    }

    ctx.restore();
  }
}

function drawMiniEqualizer(cx, cy, now) {
  ctx.save();
  ctx.translate(cx, cy);
  const bars = 5;
  const barW = 3;
  const gap = 3;
  const totalW = bars * barW + (bars - 1) * gap;
  const startX = -totalW / 2;

  for (let i = 0; i < bars; i++) {
    const height = 4 + Math.abs(Math.sin(now * 0.01 + i * 0.8)) * 12;
    const x = startX + i * (barW + gap);
    ctx.fillStyle = "#00f0ff";
    ctx.shadowColor = "#00f0ff";
    ctx.shadowBlur = 6;
    ctx.fillRect(x, -height / 2, barW, height);
  }
  ctx.restore();
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
// Hit Detection & Agent Selection (Zero Camera Movement)
// -----------------------------------------------------------------------------
function handleCanvasClick(screenX, screenY) {
  if (!state.galaxy || !state.galaxy.nodes) return;

  // Convert screen coordinates to centered world coordinates
  const worldX = screenX - canvas.width / 2;
  const worldY = screenY - canvas.height / 2;

  let clickedNode = null;
  const now = performance.now();

  for (const node of state.galaxy.nodes) {
    const hitRadius = (node.base_radius || 26) + 12;
    const pos = getNodePosition(node, now);
    const nx = pos.x;
    const ny = pos.y;
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

async function selectAgent(node) {
  state.selectedNode = node;

  // Knowledge Trinity: Nova and Aegis belong to the ONE Knowledge Department
  const isTrinityChild = (node.agent_id === "nova_discovery_agent" || node.agent_id === "aegis_verification_agent" || node.agent_id === "universal_knowledge_engine" || node.parent_department === "universal_knowledge_engine");
  const targetAgentId = isTrinityChild ? "universal_knowledge_engine" : node.agent_id;
  const targetFriendlyName = isTrinityChild ? "Knowledge" : node.friendly_name;

  state.activeConversationAgent = targetAgentId;
  state.activeConversationAgentName = targetFriendlyName;

  // Show Active Conversation Banner in Central Viewport with role-specific mode
  const banner = document.getElementById("activeChatBanner");
  const bannerName = document.getElementById("activeChatAgentName");
  if (banner) banner.style.display = "flex";
  if (bannerName) {
    if (node.agent_id === "nova_discovery_agent") {
      bannerName.textContent = "Knowledge (Nova Discovery Mode)";
    } else if (node.agent_id === "aegis_verification_agent") {
      bannerName.textContent = "Knowledge (Aegis Verification Mode)";
    } else if (node.agent_id === "universal_knowledge_engine") {
      bannerName.textContent = "Knowledge (Epistemic Reasoning Core)";
    } else {
      bannerName.textContent = node.friendly_name;
    }
  }

  // Render Base Agent Panel immediately
  renderAgentPanel(node);

  // Asynchronously activate agent session (session-aware greeting)
  try {
    const actRes = await fetch(`/api/agent/${targetAgentId}/activate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
    if (actRes.ok) {
      const actData = await actRes.json();
      if (actData.speech) {
        appendChatMessage("agent", actData.speech);
        if (state.ttsEnabled && !state.introMode) {
          updatePttState("SPEAKING");
          speakText(actData.speech, () => updatePttState("READY"));
        }
      }
    }
  } catch (err) {
    console.warn("Agent activation call error:", err);
  }

  // Load detailed workspace context (Focus, checklist, telemetry)
  loadAgentWorkspace(targetAgentId);

  // Load existing chat history from the ONE Knowledge workspace
  loadAgentChatHistory(targetAgentId);
}

// -----------------------------------------------------------------------------
// Agent Chat Workspace & Interaction Panel Rendering
// -----------------------------------------------------------------------------
function renderAgentPanel(node) {
  const panel = document.getElementById("agentPanel");
  if (!panel) return;

  const avatar = document.getElementById("panelAgentAvatar");
  if (avatar) {
    avatar.textContent = getIconGlyph(node.icon_type);
    avatar.style.borderColor = node.color;
    avatar.style.boxShadow = `0 0 16px ${node.glow}`;
  }

  const nameElem = document.getElementById("panelAgentName");
  if (nameElem) nameElem.textContent = node.friendly_name;

  const roleElem = document.getElementById("panelAgentRole");
  if (roleElem) roleElem.textContent = node.role;

  const statusBadge = document.getElementById("panelStatusPill");
  if (statusBadge) {
    statusBadge.textContent = `● ${node.status}`;
    statusBadge.style.color = node.status_color || "#10b981";
    statusBadge.style.borderColor = node.status_color || "#10b981";
    statusBadge.style.background = `${node.status_color || "#10b981"}18`;
  }

  const modelPill = document.getElementById("panelModelPill");
  if (modelPill) modelPill.textContent = node.model_name || "Auto-Routed";

  // Set Workspace Focus & Project Defaults
  const isTrinityChild = (node.agent_id === "nova_discovery_agent" || node.agent_id === "aegis_verification_agent" || node.agent_id === "universal_knowledge_engine" || node.parent_department === "universal_knowledge_engine");

  const wsFocus = document.getElementById("panelWorkspaceFocus");
  if (wsFocus) {
    wsFocus.textContent = isTrinityChild ? "● Universal Knowledge Workspace" : `● ${node.workspace_name || "Central Workspace"}`;
  }

  const actProj = document.getElementById("panelActiveProject");
  if (actProj) {
    actProj.textContent = isTrinityChild ? "Knowledge Trinity (Epistemic Core)" : (node.project_name || "None");
  }

  const currTask = document.getElementById("panelCurrentTask");
  if (currTask) {
    if (isTrinityChild) {
      currTask.textContent = node.agent_id === "nova_discovery_agent"
        ? "Scouting deep web sources & live developments"
        : node.agent_id === "aegis_verification_agent"
        ? "Auditing factual claims & enforcing ground truth"
        : (node.current_task ? (node.current_task.task_name || "Synthesizing verified knowledge & multi-hop continuum") : "Synthesizing verified knowledge & multi-hop continuum");
    } else {
      currTask.textContent = node.current_task ? (node.current_task.task_name || "Standing by") : "Standing by in workspace";
    }
  }

  // Set PTT button initial ready state
  updatePttState("READY");

  // Open Panel without changing camera
  panel.classList.add("open");
}

async function loadAgentWorkspace(agentId) {
  try {
    const res = await fetch(`/api/agent/${agentId}/context`);
    if (!res.ok) return;
    const ctx = await res.json();

    const wsFocus = document.getElementById("panelWorkspaceFocus");
    if (wsFocus) wsFocus.textContent = `● ${ctx.workspace_name || "Central Workspace"}`;

    const actProj = document.getElementById("panelActiveProject");
    if (actProj) actProj.textContent = ctx.project_name || "None";

    const currTask = document.getElementById("panelCurrentTask");
    if (currTask && ctx.development_context) {
      currTask.textContent = ctx.development_context.current_task || ctx.development_context.ide || "Standing by";
    }

    // Render Checklist Steps
    const stepList = document.getElementById("panelStepList");
    if (stepList && ctx.step_checklist) {
      stepList.innerHTML = "";
      ctx.step_checklist.forEach(step => {
        const div = document.createElement("div");
        div.className = `step-item ${step.status || "idle"}`;
        div.id = step.id;
        div.innerHTML = `<span class="step-item-icon">${step.icon || "○"}</span> <span>${step.label}</span>`;
        stepList.appendChild(div);
      });
    }

    // Render Suggested Action Buttons
    const actionsGrid = document.getElementById("panelActionsGrid");
    if (actionsGrid && ctx.suggested_actions) {
      actionsGrid.innerHTML = "";
      ctx.suggested_actions.forEach(act => {
        const btn = document.createElement("button");
        btn.className = "action-card-btn";
        btn.innerHTML = `<span>▶</span> <span>${act.label}</span>`;
        btn.onclick = () => executeAgentAction(agentId, act.id);
        actionsGrid.appendChild(btn);
      });
    }
  } catch (e) {
    console.warn("Failed to load agent workspace context:", e);
  }
}

async function loadAgentChatHistory(agentId) {
  const container = document.getElementById("panelChatHistory");
  if (!container) return;

  try {
    const res = await fetch(`/api/agent/${agentId}/chat`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.history && Array.isArray(data.history)) {
      container.innerHTML = "";
      data.history.forEach(m => {
        appendChatMessage(m.role, m.text, m.data, false);
      });
      container.scrollTop = container.scrollHeight;
    }
  } catch (e) {
    console.warn("Failed to load agent chat history:", e);
  }
}

// -----------------------------------------------------------------------------
// Rich Markdown, Epistemic Badges, Provenance Accordion & Media Grid Helpers
// -----------------------------------------------------------------------------
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

window.copyCode = function(btn) {
  const wrapper = btn.closest('.code-block-wrapper');
  if (!wrapper) return;
  const codeElem = wrapper.querySelector('code');
  if (!codeElem) return;
  const text = codeElem.innerText || codeElem.textContent;
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 2000);
  }).catch(() => {
    btn.textContent = 'Failed';
  });
};

window.retryLastUserTurn = function() {
  if (state.lastUserTurn) {
    sendAgentTurn(state.lastUserTurn);
  }
};

function renderMarkdownText(rawText) {
  if (!rawText) return "";
  let text = rawText;

  // 1. Fenced Code blocks
  const codeBlocks = [];
  text = text.replace(/```([a-zA-Z0-9_\-\+]*)\n([\s\S]*?)```/g, (match, lang, code) => {
    const idx = codeBlocks.length;
    codeBlocks.push({ lang: lang || "code", code: code.trim() });
    return `__CODE_BLOCK_${idx}__`;
  });

  // 2. Escape HTML on prose
  text = escapeHtml(text);

  // 3. Inline code `code`
  text = text.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

  // 4. Bold **text**
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // 5. Safe Markdown links: [text](url)
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)/g, (match, linkText, url) => {
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${linkText}</a>`;
  });

  // 6. Paragraphs and Linebreaks
  text = text.split(/\n\n+/).map(para => `<p>${para.replace(/\n/g, '<br>')}</p>`).join('');

  // 7. Re-insert formatted code blocks
  codeBlocks.forEach((b, idx) => {
    const safeCode = escapeHtml(b.code);
    const blockHtml = `
      <div class="code-block-wrapper">
        <div class="code-block-header">
          <span class="code-block-lang">${escapeHtml(b.lang)}</span>
          <button class="code-copy-btn" onclick="copyCode(this)">Copy</button>
        </div>
        <pre><code>${safeCode}</code></pre>
      </div>
    `;
    text = text.replace(`__CODE_BLOCK_${idx}__`, blockHtml);
    text = text.replace(`<p>__CODE_BLOCK_${idx}__</p>`, blockHtml);
  });

  return text;
}

function extractEpistemicBadge(text, meta) {
  let badge = null;
  let cleanText = text || "";
  const card = (meta && (meta.card || (meta.response && meta.response.card) || (meta.response && meta.response.data) || meta.data || meta)) || {};

  if (card.badge && (card.badge.tag || card.badge.label)) {
    badge = {
      tag: card.badge.tag || "[VERIFIED FACT]",
      label: card.badge.label || card.epistemic_type || "Verified Fact",
      type: card.epistemic_type || "VERIFIED_FACT",
      confidence: card.confidence != null ? card.confidence : 1.0,
      css_class: card.badge.css_class || "badge-verified",
    };
  }

  // Check for leading tag in text e.g. [VERIFIED FACT]
  const match = cleanText.match(/^\[([A-Z\s_]+)\]\s*\n*/);
  if (match) {
    const matchedTag = match[1].trim();
    cleanText = cleanText.substring(match[0].length).trim();
    if (!badge) {
      const typeKey = matchedTag.replace(/\s+/g, '_');
      badge = {
        tag: `[${matchedTag}]`,
        label: matchedTag.replace(/_/g, ' '),
        type: typeKey,
        confidence: card.confidence != null ? card.confidence : 1.0,
        css_class: `badge-${typeKey.toLowerCase()}`,
      };
    }
  }

  return { badge, cleanText, card };
}

function getEpistemicBadgeClass(type) {
  const t = (type || "").toUpperCase();
  if (t.includes("VERIFIED")) return "epistemic-verified";
  if (t.includes("EMPIRICAL")) return "epistemic-empirical";
  if (t.includes("INFER")) return "epistemic-inferred";
  if (t.includes("CONTEST")) return "epistemic-contested";
  if (t.includes("UNVERIFIED") || t.includes("HYPOTHESIS")) return "epistemic-unverified";
  if (t.includes("REFUTED") || t.includes("CONTRADICTION")) return "epistemic-refuted";
  return "epistemic-default";
}

function renderProvenanceAccordion(evidenceItems, sources) {
  const items = (evidenceItems && evidenceItems.length > 0) ? evidenceItems : (sources || []).map(s => ({
    title: typeof s === 'string' ? s : (s.title || s.url || 'Source'),
    url: typeof s === 'string' && s.startsWith('http') ? s : (s.url || ''),
    snippet: s.snippet || '',
    source_type: s.source_type || 'SOURCE ATTRIBUTED',
  }));

  if (!items || items.length === 0) return "";

  let cardsHtml = "";
  items.forEach(it => {
    const title = escapeHtml(it.title || it.snippet || "Evidence Source");
    const snippet = it.snippet ? escapeHtml(it.snippet) : "";
    const stype = escapeHtml(it.source_type || it.domain || "EVIDENCE");
    const safeUrl = it.url && (it.url.startsWith("http://") || it.url.startsWith("https://")) ? escapeHtml(it.url) : null;
    const linkHtml = safeUrl ? `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="evidence-link">🔗 View Source</a>` : "";

    cardsHtml += `
      <div class="evidence-card">
        <div class="evidence-card-header">
          <span class="evidence-title">${title}</span>
          <span class="evidence-source-tag">${stype}</span>
        </div>
        ${snippet ? `<div class="evidence-snippet">${snippet}</div>` : ""}
        ${linkHtml}
      </div>
    `;
  });

  return `
    <details class="provenance-accordion">
      <summary class="provenance-summary">
        <span>📚 Sources & Provenance (${items.length})</span>
        <span class="acc-arrow">▼</span>
      </summary>
      <div class="evidence-list">
        ${cardsHtml}
      </div>
    </details>
  `;
}

function renderMediaGrid(mediaItems) {
  if (!mediaItems || !Array.isArray(mediaItems) || mediaItems.length === 0) return "";

  let gridHtml = '<div class="media-card-grid">';
  mediaItems.forEach(m => {
    const type = (m.type || "doc").toLowerCase();
    let icon = "🌐";
    let badgeText = "Document";
    if (type.includes("video") || type.includes("youtube")) {
      icon = "🎬";
      badgeText = "Video";
    } else if (type.includes("paper") || type.includes("arxiv") || type.includes("scholarly")) {
      icon = "📄";
      badgeText = "Paper";
    } else if (type.includes("github") || type.includes("code") || type.includes("repo")) {
      icon = "💻";
      badgeText = "Code Repo";
    }

    const title = escapeHtml(m.title || "Media Resource");
    const desc = escapeHtml(m.description || m.snippet || "");
    const safeUrl = m.url && (m.url.startsWith("http://") || m.url.startsWith("https://")) ? escapeHtml(m.url) : "#";

    gridHtml += `
      <a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="media-card">
        <div class="media-card-badge">${icon} ${badgeText}</div>
        <div class="media-card-title">${title}</div>
        ${desc ? `<div class="media-card-desc">${desc}</div>` : ""}
      </a>
    `;
  });
  gridHtml += '</div>';
  return gridHtml;
}

function appendChatMessage(role, text, meta, scroll = true) {
  const container = document.getElementById("panelChatHistory");
  if (!container) return;

  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${role}`;

  if (role === "user") {
    bubble.textContent = text;
  } else {
    if (meta && meta.is_error) {
      bubble.classList.add("error-bubble");
      bubble.innerHTML = `
        <span>⚠️ ${escapeHtml(text)}</span>
        <button class="chat-retry-btn" onclick="retryLastUserTurn()">🔄 Retry</button>
      `;
    } else {
      const { badge, cleanText, card } = extractEpistemicBadge(text, meta);
      let contentHtml = "";

      // 1. Epistemic Badge Pill
      if (badge) {
        const badgeClass = getEpistemicBadgeClass(badge.type);
        const confPct = Math.round((badge.confidence != null ? badge.confidence : 1.0) * 100);
        contentHtml += `<div class="epistemic-badge-pill ${badgeClass}">● ${escapeHtml(badge.label)} (${confPct}%)</div>`;
      }

      // 2. Trinity Collaboration Box
      if (card && card.collaboration_block) {
        contentHtml += `<div class="trinity-collab-box"><span class="collab-tag">⚡ TRINITY</span> ${escapeHtml(card.collaboration_block)}</div>`;
      }

      // 3. Formatted Markdown Body
      contentHtml += renderMarkdownText(cleanText);

      // 4. Provenance Accordion (Evidence & Sources)
      const evidence = (card && card.evidence_items) || [];
      const sources = (card && card.sources) || [];
      if (evidence.length > 0 || sources.length > 0) {
        contentHtml += renderProvenanceAccordion(evidence, sources);
      }

      // 5. Media Resources Grid (Videos, Papers, Code)
      const media = (card && card.media_items) || [];
      if (media.length > 0) {
        contentHtml += renderMediaGrid(media);
      }

      bubble.innerHTML = contentHtml;
    }
  }

  container.appendChild(bubble);

  if (scroll) {
    container.scrollTop = container.scrollHeight;
  }
}

function clearActiveAgentChat() {
  const container = document.getElementById("panelChatHistory");
  if (container) container.innerHTML = "";
}

function closeAgentPanel() {
  const panel = document.getElementById("agentPanel");
  if (panel) panel.classList.remove("open");
  state.selectedNode = null;
  // Zero camera movement — stable composition
}

// -----------------------------------------------------------------------------
// Push-to-Talk Voice & Interaction State Machine
// -----------------------------------------------------------------------------
function updatePttState(newState) {
  state.pttState = newState;
  const btn = document.getElementById("panelMicBtn");
  const label = document.getElementById("panelMicStatusText");
  if (!btn || !label) return;

  // Clear existing state classes
  btn.className = "ptt-mic-btn";
  btn.classList.add(`state-${newState.toLowerCase()}`);

  const stateLabels = {
    READY: "TAP TO SPEAK",
    LISTENING: "🔴 LISTENING...",
    TRANSCRIBING: "◌ TRANSCRIBING...",
    UNDERSTANDING: "◌ UNDERSTANDING...",
    RESPONDING: "◌ SYNTHESIZING...",
    SPEAKING: "🔊 SPEAKING (TAP TO STOP)",
    ERROR: "⚠️ MIC ERROR (USE TEXT)"
  };

  label.textContent = stateLabels[newState] || newState;
}

function toggleAgentPushToTalk() {
  // If agent is currently speaking, tap acts as instant barge-in / interruption
  if (state.pttState === "SPEAKING" || state.isSpeakingAudio) {
    interruptSpeech();
    updatePttState("READY");
    return;
  }

  // If already listening, user tapped to stop early
  if (state.pttState === "LISTENING") {
    if (state.agentRecognition) {
      try { state.agentRecognition.stop(); } catch (e) {}
    }
    updatePttState("TRANSCRIBING");
    return;
  }

  // Turn-based Start Push-to-Talk
  if (state.pttState === "READY") {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      updatePttState("ERROR");
      alert("Speech recognition is not available in this browser environment. Please use the text input below.");
      return;
    }

    try {
      state.agentRecognition = new SpeechRecognition();
      state.agentRecognition.continuous = false; // Strictly single turn! No automatic re-recording loop!
      state.agentRecognition.interimResults = false;

      state.agentRecognition.onstart = () => {
        updatePttState("LISTENING");
      };

      state.agentRecognition.onresult = async (event) => {
        const spokenText = event.results[0][0].transcript;
        updatePttState("TRANSCRIBING");
        await sendAgentTurn(spokenText);
      };

      state.agentRecognition.onerror = (e) => {
        console.warn("Push-to-talk error:", e);
        updatePttState("READY");
      };

      state.agentRecognition.onend = () => {
        if (state.pttState === "LISTENING") {
          updatePttState("TRANSCRIBING");
        }
      };

      state.agentRecognition.start();
    } catch (e) {
      console.warn("Could not start agent speech recognition:", e);
      updatePttState("READY");
    }
  }
}

async function sendAgentTurn(text) {
  if (!text || !text.trim()) {
    updatePttState("READY");
    return;
  }

  state.lastUserTurn = text;
  let aid = state.activeConversationAgent || (state.selectedNode ? state.selectedNode.agent_id : "universal_knowledge_engine");
  if (aid === "nova_discovery_agent" || aid === "aegis_verification_agent") {
    aid = "universal_knowledge_engine";
  }
  appendChatMessage("user", text);
  updatePttState("UNDERSTANDING");

  // If Knowledge Trinity active, render real-time in-flight activity banner
  const container = document.getElementById("panelChatHistory");
  if (aid === "universal_knowledge_engine" && container) {
    const activityElem = document.createElement("div");
    activityElem.id = "trinityActivityIndicator";
    activityElem.className = "chat-bubble agent trinity-activity-bubble";
    activityElem.innerHTML = `
      <div class="trinity-activity-banner">
        <div class="trinity-pulse-dot"></div>
        <div>
          <div class="trinity-activity-phase">KNOWLEDGE TRINITY ACTIVE</div>
          <div class="trinity-activity-sub" id="trinityActivitySub">Scouting sources & verifying epistemics...</div>
        </div>
      </div>
    `;
    container.appendChild(activityElem);
    container.scrollTop = container.scrollHeight;
  }

  try {
    updatePttState("RESPONDING");
    const res = await fetch(`/api/agent/${aid}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, speak_output: false })
    });

    // Remove in-flight indicator
    const liveIndicator = document.getElementById("trinityActivityIndicator");
    if (liveIndicator) liveIndicator.remove();

    const data = await res.json();
    const reply = data.reply || (data.response && data.response.text) || "Understood.";
    const card = data.card || (data.response && data.response.card) || (data.response && data.response.data);
    appendChatMessage("agent", reply, { card: card });

    // Update checklist step visually if relevant
    const low = text.toLowerCase();
    if (low.includes("build") || low.includes("compile")) {
      const bStep = document.getElementById("step_building");
      if (bStep) {
        bStep.className = "step-item active";
        const icon = bStep.querySelector(".step-item-icon");
        if (icon) icon.textContent = "▶";
      }
    }

    if (state.ttsEnabled) {
      updatePttState("SPEAKING");
      speakText(reply, () => {
        updatePttState("READY");
      });
    } else {
      updatePttState("READY");
    }
  } catch (err) {
    console.error("Error sending agent turn:", err);
    const liveIndicator = document.getElementById("trinityActivityIndicator");
    if (liveIndicator) liveIndicator.remove();
    appendChatMessage("agent", "Error communicating with Knowledge Trinity.", { is_error: true });
    updatePttState("READY");
  }
}

function sendAgentTextMessage() {
  const input = document.getElementById("panelTextInput");
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  sendAgentTurn(text);
}

// -----------------------------------------------------------------------------
// Galaxy Introduction Mode Engine (One-by-One Agent Presentation)
// -----------------------------------------------------------------------------
async function startIntroductionMode() {
  if (state.introMode && !state.introPaused) return;

  try {
    const res = await fetch("/api/galaxy/introduction");
    if (!res.ok) {
      console.error("Failed to load introduction data");
      return;
    }
    const data = await res.json();
    if (!data.success || !data.sequence) return;

    state.introMode = true;
    state.introPaused = false;
    state.introStep = 0;
    state.introQueue = data.sequence;
    state.introGreeting = data.intro_greeting || "Of course, Boss. Let me introduce you to my agents.";
    state.introOutro = data.intro_outro || "That's my current agent team, Boss. Tell me what you want to build, learn, research, or solve.";

    // Show Intro HUD
    const hud = document.getElementById("introHud");
    if (hud) {
      hud.style.display = "flex";
    }

    updateIntroQueuePreview();
    playIntroStep(0);
  } catch (err) {
    console.error("Error starting introduction mode:", err);
  }
}

function updateIntroQueuePreview() {
  const container = document.getElementById("introQueuePreview");
  if (!container) return;

  let html = `<div class="queue-pill ${state.introStep === 0 ? 'current' : (state.introStep > 0 ? 'done' : '')}">
    ${state.introStep > 0 ? '✓' : (state.introStep === 0 ? '●' : '○')} NR-AI
  </div>`;

  state.introQueue.forEach((agent, idx) => {
    const stepIdx = idx + 1;
    const isDone = state.introStep > stepIdx;
    const isCurrent = state.introStep === stepIdx;
    const marker = isDone ? '✓' : (isCurrent ? '●' : '○');
    const cls = isDone ? 'done' : (isCurrent ? 'current' : '');
    html += `<div class="queue-pill ${cls}">${marker} ${agent.name}</div>`;
  });

  container.innerHTML = html;
}

function playIntroStep(step) {
  if (!state.introMode || state.introPaused) return;

  clearTimeout(state.introTimer);

  const total = state.introQueue.length;
  updateIntroQueuePreview();

  // Step 0: NR-AI Central Intelligence Greeting
  if (step === 0) {
    state.currentSpeakerId = "nr_ai_central_intelligence";
    updateIntroHudSpeaker({
      name: "NR-AI",
      role: "Central Intelligence Core",
      avatar: "🌌",
      badge: "● SPEAKING",
      text: state.introGreeting,
      stepText: `Core Intro (0 / ${total})`,
    });

    speakText(state.introGreeting, () => {
      if (state.introMode && !state.introPaused) {
        advanceIntroStep();
      }
    });
    return;
  }

  // Steps 1..total: Agent Introductions
  if (step >= 1 && step <= total) {
    const agent = state.introQueue[step - 1];
    state.currentSpeakerId = agent.agent_id;

    updateIntroHudSpeaker({
      name: agent.name,
      role: agent.role,
      avatar: getIconGlyph(agent.icon_type),
      badge: "● SPEAKING",
      text: agent.speech_text,
      stepText: `${step} / ${total}`,
    });

    speakText(agent.speech_text, () => {
      if (state.introMode && !state.introPaused) {
        advanceIntroStep();
      }
    });
    return;
  }

  // Step > total: NR-AI Central Core Outro
  if (step > total) {
    state.currentSpeakerId = "nr_ai_central_intelligence";
    updateIntroHudSpeaker({
      name: "NR-AI",
      role: "Central Intelligence Core",
      avatar: "🌌",
      badge: "● SPEAKING",
      text: state.introOutro,
      stepText: `Complete (${total} / ${total})`,
    });

    speakText(state.introOutro, () => {
      finishIntroduction();
    });
  }
}

function updateIntroHudSpeaker({ name, role, avatar, badge, text, stepText }) {
  const nameEl = document.getElementById("introSpeakerName");
  const roleEl = document.getElementById("introSpeakerRole");
  const avatarEl = document.getElementById("introSpeakerAvatar");
  const badgeEl = document.getElementById("introSpeakerBadge");
  const bubbleEl = document.getElementById("introSpeechBubble");
  const stepEl = document.getElementById("introStepCount");

  if (nameEl) nameEl.textContent = name;
  if (roleEl) roleEl.textContent = role;
  if (avatarEl) avatarEl.textContent = avatar;
  if (badgeEl) badgeEl.textContent = badge;
  if (bubbleEl) bubbleEl.textContent = `"${text}"`;
  if (stepEl) stepEl.textContent = stepText;
}

function advanceIntroStep() {
  if (!state.introMode || state.introPaused) return;
  state.introStep++;
  playIntroStep(state.introStep);
}

function togglePauseIntroduction() {
  if (!state.introMode) return;
  state.introPaused = !state.introPaused;

  const btn = document.getElementById("btnIntroPause");
  if (state.introPaused) {
    if (btn) btn.innerHTML = "▶️ Resume";
    if (state.speechSynth) state.speechSynth.pause();
    clearTimeout(state.introTimer);
    state.isSpeakingAudio = false;
    const eq = document.getElementById("introEqualizer");
    if (eq) eq.classList.remove("active");
  } else {
    if (btn) btn.innerHTML = "⏸️ Pause";
    if (state.speechSynth && state.speechSynth.paused) {
      state.speechSynth.resume();
      state.isSpeakingAudio = true;
      const eq = document.getElementById("introEqualizer");
      if (eq) eq.classList.add("active");
    } else {
      playIntroStep(state.introStep);
    }
  }
}

function skipIntroduction() {
  if (!state.introMode) return;
  if (state.speechSynth) state.speechSynth.cancel();
  clearTimeout(state.introTimer);
  state.isSpeakingAudio = false;
  state.introPaused = false;
  const btn = document.getElementById("btnIntroPause");
  if (btn) btn.innerHTML = "⏸️ Pause";
  advanceIntroStep();
}

function stopIntroduction() {
  if (state.speechSynth) state.speechSynth.cancel();
  clearTimeout(state.introTimer);
  state.introMode = false;
  state.introPaused = false;
  state.introStep = -1;
  state.currentSpeakerId = null;
  state.isSpeakingAudio = false;

  const hud = document.getElementById("introHud");
  if (hud) hud.style.display = "none";

  const btn = document.getElementById("btnIntroPause");
  if (btn) btn.innerHTML = "⏸️ Pause";

  const eq = document.getElementById("introEqualizer");
  if (eq) eq.classList.remove("active");

  pollGalaxyState();
}

function finishIntroduction() {
  setTimeout(() => {
    stopIntroduction();
  }, 2000);
}

function speakText(text, onComplete) {
  clearTimeout(state.introTimer);
  const eq = document.getElementById("introEqualizer");
  const notice = document.getElementById("introVoiceNotice");

  if (state.ttsEnabled && 'speechSynthesis' in window) {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;

    state.isSpeakingAudio = true;
    if (eq) eq.classList.add("active");
    if (notice) notice.style.display = "none";

    let completed = false;
    const finish = () => {
      if (!completed) {
        completed = true;
        state.isSpeakingAudio = false;
        if (eq) eq.classList.remove("active");
        if (onComplete) onComplete();
      }
    };

    utterance.onend = finish;
    utterance.onerror = (e) => {
      console.warn("Speech synthesis error:", e);
      finish();
    };

    window.speechSynthesis.speak(utterance);

    // Safety timeout in case speech synthesis hangs or voices unavailable
    const maxDuration = Math.max(4000, text.length * 85);
    state.introTimer = setTimeout(finish, maxDuration);
  } else {
    // Voice unavailable: display clearly and advance after reading delay
    state.isSpeakingAudio = false;
    if (eq) eq.classList.remove("active");
    if (notice) {
      notice.style.display = "block";
      notice.textContent = "VOICE UNAVAILABLE • TEXT DISPLAY";
    }

    const readDuration = Math.max(3000, Math.min(7500, text.length * 60));
    state.introTimer = setTimeout(() => {
      if (onComplete) onComplete();
    }, readDuration);
  }
}

// -----------------------------------------------------------------------------
// Agent Action & Command Execution
// -----------------------------------------------------------------------------
async function executeAgentAction(agentId, actionId) {
  const panel = document.getElementById("agentPanel");
  if (!panel) return;

  const chatHistory = document.getElementById("panelChatHistory");
  const thinkingMsg = document.createElement("div");
  thinkingMsg.className = "chat-bubble agent thinking";
  thinkingMsg.textContent = `Dispatching action '${actionId}' to ${agentId}...`;
  chatHistory.appendChild(thinkingMsg);
  chatHistory.scrollTop = chatHistory.scrollHeight;

  try {
    const res = await fetch(`/api/agent/${agentId}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: actionId, parameters: {} }),
    });
    const result = await res.json();
    thinkingMsg.classList.remove("thinking");
    thinkingMsg.textContent = result.message || `Action '${actionId}' executed successfully.`;

    if (state.ttsEnabled && !state.introMode) {
      speakText(thinkingMsg.textContent);
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

  // Check if user requested introduction mode
  const cmdLower = cmd.toLowerCase();
  const introTriggers = [
    "introduce yourself", "introduce yourselves", "who are you all",
    "let every agent introduce themselves", "introduce your agents",
    "introduce the agents", "who are your agents", "tell me about your agents",
    "agent introduction", "meet the agents"
  ];
  const isIntro = introTriggers.some(t => cmdLower.includes(t));

  interruptSpeech();

  if (isIntro) {
    startIntroductionMode();
  }

  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: cmd }),
    });
    const data = await res.json();
    const replyText = data.text || data.response || "Command executed.";

    if (data.data) {
      if (data.data.active_conversation_agent !== undefined) {
        state.activeConversationAgent = data.data.active_conversation_agent;
        state.activeConversationAgentName = data.data.agent_name || (data.data.active_conversation_agent ? data.data.active_conversation_agent.split("_")[0].toUpperCase() : null);
      }
      if (data.data.single_agent_introduction && data.data.single_speaker_id) {
        state.currentSpeakerId = data.data.single_speaker_id;
        state.isSpeakingAudio = true;
        setTimeout(() => {
          state.isSpeakingAudio = false;
          state.currentSpeakerId = null;
        }, 5000);
      } else if (data.data.introduction_mode && !state.introMode) {
        startIntroductionMode();
      }
    }

    // Append to conversation if agent panel is open
    const chatHistory = document.getElementById("panelChatHistory");
    if (chatHistory && state.selectedNode) {
      chatHistory.innerHTML += `
        <div class="chat-bubble user">${cmd}</div>
        <div class="chat-bubble agent">${replyText}</div>
      `;
      chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    if (state.ttsEnabled && !state.introMode) {
      speakText(replyText);
    }
  } catch (e) {
    console.error("Command error:", e);
  } finally {
    input.placeholder = "Ask NR-AI or dispatch any agent...";
    pollGalaxyState();
  }
}

async function triggerEmergencyStop() {
  interruptSpeech();
  updatePttState("READY");
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
    updateHUDTelemetry(data.system_metrics);

    // Sync active conversational agent banner
    const activeId = data.active_conversation_agent || state.activeConversationAgent;
    const banner = document.getElementById("activeChatBanner");
    const bannerName = document.getElementById("activeChatAgentName");
    if (activeId && banner && bannerName) {
      if (activeId === "universal_knowledge_engine") {
        if (state.selectedNode && state.selectedNode.agent_id === "nova_discovery_agent") {
          bannerName.textContent = "Knowledge (Nova Discovery Mode)";
        } else if (state.selectedNode && state.selectedNode.agent_id === "aegis_verification_agent") {
          bannerName.textContent = "Knowledge (Aegis Verification Mode)";
        } else {
          bannerName.textContent = "Knowledge (Epistemic Reasoning Core)";
        }
      } else {
        const matchingNode = data.nodes ? data.nodes.find(n => n.agent_id.toLowerCase() === activeId.toLowerCase()) : null;
        bannerName.textContent = matchingNode ? matchingNode.friendly_name : activeId;
      }
      banner.style.display = "flex";
    } else if (banner) {
      banner.style.display = "none";
    }

    // Update live Trinity activity indicator if in flight and telemetry reports progress
    if (data.trinity_telemetry) {
      const sub = document.getElementById("trinityActivitySub");
      const phase = document.getElementById("trinityActivityPhase");
      const tt = data.trinity_telemetry;
      if (sub && phase && tt.workflow_state && tt.workflow_state !== "IDLE" && tt.workflow_state !== "COMPLETED") {
        phase.textContent = `KNOWLEDGE TRINITY: ${tt.workflow_state}`;
        if (tt.current_operation) {
          sub.textContent = tt.current_operation;
        } else if (tt.workflow_state === "DISCOVERING") {
          sub.textContent = `Nova: Discovering sources... (${tt.source_count || 0} found)`;
        } else if (tt.workflow_state === "VERIFYING") {
          sub.textContent = `Aegis: Verifying claims (${tt.verification_status || 'CHECKING'})...`;
        } else if (tt.workflow_state === "SYNTHESIZING") {
          sub.textContent = `Knowledge: Synthesizing verified answer...`;
        }
      }
    }

    // Update Central Core Status
    const coreStatus = document.getElementById("coreStatusIndicator");
    if (coreStatus) {
      coreStatus.textContent = data.central_core.status_indicator || "● OPERATIONAL";
      coreStatus.style.color = data.central_core.status === "STOPPED" ? "#ef4444" : "#38bdf8";
    }
  } catch (err) {
    // Keep running offline or without connection
  }
}

function updateHUDTelemetry(metrics) {
  if (!metrics) return;

  const cpuVal = document.getElementById("metricCpuVal");
  const cpuBar = document.getElementById("metricCpuBar");
  if (cpuVal && cpuBar) {
    cpuVal.textContent = `${metrics.cpu_percent}%`;
    cpuBar.style.width = `${Math.min(metrics.cpu_percent, 100)}%`;
  }

  const memVal = document.getElementById("metricMemVal");
  const memBar = document.getElementById("metricMemBar");
  if (memVal && memBar) {
    memVal.textContent = `${metrics.memory_percent}%`;
    memBar.style.width = `${Math.min(metrics.memory_percent, 100)}%`;
  }

  const agentsVal = document.getElementById("metricAgentsVal");
  const agentsBar = document.getElementById("metricAgentsBar");
  if (agentsVal && agentsBar) {
    agentsVal.textContent = metrics.agents_metric_display || `${metrics.active_agents_count}/${metrics.total_registered_agents}`;
    const pct = metrics.total_registered_agents > 0 ? (metrics.active_agents_count / metrics.total_registered_agents) * 100 : 0;
    agentsBar.style.width = `${pct}%`;
  }
}

// -----------------------------------------------------------------------------
// Filter & Search Controls
// -----------------------------------------------------------------------------
function setOrbitFilter(filterType, elem) {
  state.statusFilter = filterType;
  document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));
  if (elem) elem.classList.add("active");
}

function initEventListeners() {
  const searchInput = document.getElementById("globalSearchInput");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      state.searchQuery = e.target.value;
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

// -----------------------------------------------------------------------------
// Voice Recognition (Dictation)
// -----------------------------------------------------------------------------
function initSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return;

  state.recognition = new SpeechRecognition();
  state.recognition.continuous = false;
  state.recognition.interimResults = false;

  state.recognition.onresult = (event) => {
    const text = event.results[0][0].transcript;
    const input = document.getElementById("globalCommandInput");
    if (input) {
      input.value = text;
      sendGlobalCommand();
    }
  };

  state.recognition.onerror = (e) => {
    console.warn("Speech recognition error:", e);
    const micBtn = document.getElementById("micBtn");
    if (micBtn) micBtn.classList.remove("listening");
  };

  state.recognition.onend = () => {
    const micBtn = document.getElementById("micBtn");
    if (micBtn) micBtn.classList.remove("listening");
  };
}

function toggleVoiceInput() {
  if (!state.recognition) {
    alert("Speech recognition is not supported in this browser. Please use Chrome or Edge.");
    return;
  }
  const micBtn = document.getElementById("micBtn");
  try {
    state.recognition.start();
    if (micBtn) micBtn.classList.add("listening");
  } catch (e) {
    state.recognition.stop();
    if (micBtn) micBtn.classList.remove("listening");
  }
}
