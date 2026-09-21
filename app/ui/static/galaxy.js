// =============================================================================
// NR-AI GALAXY CORE CONTROLLER
// =============================================================================

// -----------------------------------------------------------------------------
// Speech Interruption / Barge-in & Central Return
// -----------------------------------------------------------------------------
function interruptSpeech() {
  const synth = window.speechSynthesis || state.speechSynth;
  if (state.isSpeakingAudio || (synth && synth.speaking)) {
    if (synth) {
      synth.cancel();
    }
    state.isSpeakingAudio = false;
    clearTimeout(state.introTimer);
    const eq = document.getElementById("introEqualizer");
    if (eq) eq.classList.remove("active");
    const voiceNotice = document.getElementById("introVoiceNotice");
    if (voiceNotice) {
      voiceNotice.textContent = "USER INTERRUPTED • LISTENING";
      voiceNotice.style.display = "block";
    }
    if (typeof updateVoiceTelemetry === "function") {
      updateVoiceTelemetry({ speech: "INTERRUPTED", audio: "BARGE-IN", event: "interruptSpeech" });
    }
    fetch("/api/conversation/interrupt", { method: "POST" }).catch(() => {});
  }
}
window.interruptSpeech = interruptSpeech;

async function returnToCentral() {
  syncActiveAgent("NR-AI");

  if (state.voiceSessionActive) {
    setVoiceSessionState("PROCESSING", "NR-AI");
  }

  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: "wake up NR-AI" }),
    });
    const data = await res.json();
    const reply = data.text || "NR-AI online and listening, Boss. What should we tackle?";
    if (typeof appendChatMessage === "function") {
      appendChatMessage("NR-AI", "agent", reply);
    }
    if (state.ttsEnabled && state.voiceSessionActive) {
      setVoiceSessionState("SPEAKING", "NR-AI");
      speakText(reply, () => {
        if (state.voiceSessionActive && !state.voiceStopRequested) {
          safeStartRecognition();
        }
      });
    } else if (state.voiceSessionActive && !state.voiceStopRequested) {
      safeStartRecognition();
    }
  } catch (e) {
    console.error("returnToCentral error:", e);
    if (state.voiceSessionActive && !state.voiceStopRequested) {
      safeStartRecognition();
    }
  }
  pollGalaxyState();
}
window.returnToCentral = returnToCentral;
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

  // Continuous Voice Conversation & Agent Focus State
  voiceSession: "INACTIVE", // "INACTIVE" | "LISTENING" | "PROCESSING" | "SPEAKING" | "MICROPHONE ERROR" | "VOICE ERROR"
  voiceSessionActive: false,
  voiceStopRequested: false,
  isRecognitionActive: false,
  isRecognitionStarting: false,
  micStream: null,
  activeAgent: "NR-AI",
  sessionGreeted: false,
  galaxyMode: "NORMAL", // "NORMAL" (NR-AI center + all agents visible) | "FOCUS" (selected agent center, others hidden)

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
  focusAgent: null,
  conversationStore: {}, // { [canonicalAgentId]: [ { message_id, agent_id, role, author, content, text, timestamp } ] }
  workspaceScrollPositions: {}, // { [canonicalAgentId]: number }
};

// -----------------------------------------------------------------------------
// Agent ID Canonicalization & Workspace Identification
// -----------------------------------------------------------------------------
function canonicalizeAgentId(id) {
  if (!id) return "nr_ai";
  const s = String(id).toLowerCase().trim().replace(/[-\s]/g, "_");
  if (["nr_ai", "central", "nrai", "nr_ai_central_intelligence", "companion"].includes(s)) return "nr_ai";
  if (["droid", "android_unified_agent", "android"].includes(s)) return "droid";
  if (["droid_scout", "scout", "scout_agent"].includes(s)) return "droid_scout";
  if (["droid_guardian", "guardian", "guardian_agent"].includes(s)) return "droid_guardian";
  if (["studio", "vs_unified_agent", "visual_studio", "visualstudio"].includes(s)) return "studio";
  if (["unity", "unity_autonomous_agent", "unity_agent"].includes(s)) return "unity";
  if (["unreal", "unreal_autonomous_agent", "unreal_agent"].includes(s)) return "unreal";
  if (["skyshield", "security_skyshield_agent"].includes(s)) return "skyshield";
  if (["knowledge", "universal_knowledge_engine"].includes(s)) return "knowledge";
  if (["nova", "nova_discovery_agent"].includes(s)) return "nova";
  if (["aegis", "aegis_verification_agent"].includes(s)) return "aegis";
  if (["sentinel", "sentinel_agent"].includes(s)) return "sentinel";
  if (["quest", "quest_agent"].includes(s)) return "quest";
  if (["vision", "vision_agent"].includes(s)) return "vision";
  if (["forge", "forge_agent"].includes(s)) return "forge";
  if (["pixel", "pixel_agent"].includes(s)) return "pixel";
  if (["nexus", "nexus_agent"].includes(s)) return "nexus";
  return s;
}
window.canonicalizeAgentId = canonicalizeAgentId;

function getActiveCanonicalAgentId() {
  if (state.activeConversationAgent) {
    return canonicalizeAgentId(state.activeConversationAgent);
  }
  if (state.activeAgent) {
    return canonicalizeAgentId(state.activeAgent);
  }
  return "nr_ai";
}
window.getActiveCanonicalAgentId = getActiveCanonicalAgentId;

// -----------------------------------------------------------------------------
// Voice Session UI & Core Label Visibility
// -----------------------------------------------------------------------------
function updateVoiceUI() {
  const sessionState = state.voiceSession || "INACTIVE";
  const agentName = state.activeAgent || (isDroidFocused() ? "Droid" : "NR-AI");

  let statusText = "VOICE SESSION: INACTIVE";
  let statusClass = "inactive";
  let liveStatusText = "VOICE: INACTIVE • CLICK MIC TO START";
  let liveStatusDotClass = "inactive";

  if (sessionState === "LISTENING") {
    statusText = "VOICE SESSION: ● LISTENING";
    statusClass = "listening";
    liveStatusText = "● LISTENING";
    liveStatusDotClass = "listening";
  } else if (sessionState === "HEARING") {
    statusText = "VOICE SESSION: ● HEARING";
    statusClass = "hearing";
    liveStatusText = "● HEARING";
    liveStatusDotClass = "hearing";
  } else if (sessionState === "PROCESSING" || sessionState === "THINKING") {
    statusText = "VOICE SESSION: ● THINKING";
    statusClass = "processing";
    liveStatusText = "● THINKING";
    liveStatusDotClass = "thinking";
  } else if (sessionState === "SPEAKING") {
    statusText = "VOICE SESSION: ● SPEAKING";
    statusClass = "speaking";
    liveStatusText = "● SPEAKING";
    liveStatusDotClass = "speaking";
  } else if (sessionState === "MICROPHONE ERROR") {
    statusText = "VOICE SESSION: ⚠️ MICROPHONE ERROR";
    statusClass = "error";
    liveStatusText = "⚠️ MICROPHONE ERROR";
    liveStatusDotClass = "error";
  } else if (sessionState === "VOICE ERROR" || sessionState === "ERROR") {
    statusText = "VOICE SESSION: ⚠️ VOICE ERROR";
    statusClass = "error";
    liveStatusText = "⚠️ VOICE ERROR";
    liveStatusDotClass = "error";
  }

  // 1. Voice Session Badge
  const sessionBadge = document.getElementById("voiceSessionBadge");
  if (sessionBadge) {
    sessionBadge.textContent = statusText;
    sessionBadge.className = `voice-session-badge ${statusClass}`;
  }

  // 2. Voice Pill Button (Click-to-Activate / Status Indicator)
  const voicePill = document.getElementById("voicePillBtn");
  if (voicePill) {
    if (sessionState === "LISTENING") {
      voicePill.textContent = "🎙️ LISTENING";
      voicePill.className = "voice-pill-btn listening";
    } else if (sessionState === "HEARING") {
      voicePill.textContent = "🎙️ HEARING";
      voicePill.className = "voice-pill-btn hearing";
    } else if (sessionState === "PROCESSING" || sessionState === "THINKING") {
      voicePill.textContent = "⚙️ THINKING";
      voicePill.className = "voice-pill-btn processing";
    } else if (sessionState === "SPEAKING") {
      voicePill.textContent = "🔊 SPEAKING";
      voicePill.className = "voice-pill-btn speaking";
    } else {
      voicePill.textContent = "🎙️ ACTIVATE VOICE";
      voicePill.className = "voice-pill-btn ready";
    }
  }

  // 3. Live Transcript Indicator inside Dedicated Chat
  const liveDot = document.getElementById("chatLiveStatusDot");
  const liveText = document.getElementById("chatLiveStatusText");
  if (liveDot) liveDot.className = `status-dot ${liveStatusDotClass}`;
  if (liveText) liveText.textContent = liveStatusText;

  // 4. Voice Toggle Chip in footer
  const btnVoice = document.getElementById("btnVoiceToggle");
  if (btnVoice) {
    btnVoice.textContent = state.voiceSessionActive ? "🎙️ Voice: ON" : "🎙️ Voice: OFF";
    if (state.voiceSessionActive) {
      btnVoice.classList.add("active");
    } else {
      btnVoice.classList.remove("active");
    }
  }

  // 5. Active Agent Badge
  const agentBadge = document.getElementById("activeAgentBadge");
  if (agentBadge) {
    agentBadge.textContent = `ACTIVE AGENT: ${agentName}`;
  }

  // 6. Active Chat Banner
  const banner = document.getElementById("activeChatBanner");
  const bannerName = document.getElementById("activeChatAgentName");
  const bannerVoice = document.getElementById("bannerVoiceStatus");
  if (banner) {
    banner.style.display = "flex";
    if (bannerName) bannerName.textContent = agentName;
    if (bannerVoice) bannerVoice.textContent = statusText;
  }

  // 7. Mic buttons listening state
  const micBtn = document.getElementById("micBtn");
  const dedicatedMicBtn = document.getElementById("dedicatedMicBtn");
  if (sessionState === "LISTENING") {
    if (micBtn) micBtn.classList.add("listening", "active");
    if (dedicatedMicBtn) dedicatedMicBtn.classList.add("listening", "active");
  } else {
    if (micBtn) micBtn.classList.remove("listening", "active");
    if (dedicatedMicBtn) dedicatedMicBtn.classList.remove("listening", "active");
  }

  updateCoreLabelVisibility();
}
window.updateVoiceUI = updateVoiceUI;

function updateCoreLabelVisibility() {
  const coreLabel = document.querySelector(".central-core-label");
  if (!coreLabel) return;
  if (state.galaxyMode === "FOCUS") {
    coreLabel.style.display = "none";
  } else {
    coreLabel.style.display = "block";
    const coreStatus = document.getElementById("coreStatusIndicator");
    if (coreStatus) {
      if (state.voiceSession === "LISTENING") {
        coreStatus.textContent = "● LISTENING • VOICE ACTIVE";
        coreStatus.style.color = "#10b981";
      } else if (state.voiceSession === "SPEAKING") {
        coreStatus.textContent = "● SPEAKING • VOICE ACTIVE";
        coreStatus.style.color = "#f59e0b";
      } else if (state.voiceSession === "PROCESSING") {
        coreStatus.textContent = "● THINKING / PROCESSING";
        coreStatus.style.color = "#38bdf8";
      } else if (state.voiceSession === "MICROPHONE ERROR") {
        coreStatus.textContent = "● MICROPHONE ERROR";
        coreStatus.style.color = "#ef4444";
      } else if (state.voiceSession === "VOICE ERROR") {
        coreStatus.textContent = "● VOICE RECOGNITION ERROR";
        coreStatus.style.color = "#ef4444";
      } else {
        coreStatus.textContent = "● OPTIMAL • MONITORING";
        coreStatus.style.color = "#38bdf8";
      }
    }
  }
}
window.updateCoreLabelVisibility = updateCoreLabelVisibility;

function setVoiceSessionState(newSessionState, newActiveAgent) {
  if (newSessionState !== undefined && newSessionState !== null) {
    state.voiceSession = newSessionState;
  }
  if (newActiveAgent !== undefined && newActiveAgent !== null) {
    state.activeAgent = newActiveAgent;
  }
  updateVoiceUI();
}
window.setVoiceSessionState = setVoiceSessionState;

window.state = state;

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

  updateCoreLabelVisibility();

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
function isNRAIFocused() {
  return state.galaxyMode === "NORMAL" || !state.focusAgent || state.focusAgent === "nr_ai_central_intelligence";
}
window.isNRAIFocused = isNRAIFocused;

function isDroidFocused() {
  if (state.galaxyMode !== "FOCUS") return false;
  const foc = (state.focusAgent || "").toLowerCase();
  const cur = (state.activeConversationAgent || "").toLowerCase();
  const act = (state.activeAgent || "").toLowerCase();
  return foc === "android_unified_agent" || foc.includes("droid") || cur.includes("droid") || act === "droid";
}
window.isDroidFocused = isDroidFocused;

function isOtherAgentFocused() {
  if (state.galaxyMode !== "FOCUS") return false;
  if (isDroidFocused()) return false;
  return Boolean(state.focusAgent && state.focusAgent !== "nr_ai_central_intelligence");
}
window.isOtherAgentFocused = isOtherAgentFocused;

function getNodePosition(node, now) {
  if (!node) return { x: 0, y: 0, hidden: true };

  // -------------------------------------------------------------
  // MODE B — SPECIALIST AGENT FOCUS (state.galaxyMode === "FOCUS")
  // -------------------------------------------------------------
  if (state.galaxyMode === "FOCUS" && state.focusAgent && state.focusAgent !== "nr_ai_central_intelligence") {
    // 1. Droid Focus Mode: Droid moves to center (0, 0), Scout & Guardian orbit Droid, others disappear!
    if (isDroidFocused()) {
      if (node.agent_id === "android_unified_agent") {
        return { x: 0, y: 0, angleRad: 0, isCenter: true, hidden: false };
      }
      if (node.agent_id === "droid_scout") {
        const speed = 0.0008;
        const angle = ((30 * Math.PI) / 180) + (now ? now * speed : 0);
        const r = 150;
        return { x: Math.cos(angle) * r, y: Math.sin(angle) * r, angleRad: angle, hidden: false };
      }
      if (node.agent_id === "droid_guardian") {
        const speed = 0.0008;
        const angle = ((210 * Math.PI) / 180) + (now ? now * speed : 0);
        const r = 185;
        return { x: Math.cos(angle) * r, y: Math.sin(angle) * r, angleRad: angle, hidden: false };
      }
      // All other unrelated agents disappear from visible scene
      return { x: 99999, y: 99999, angleRad: 0, hidden: true };
    }

    // 2. Other Specialist Focus Mode: That agent is at center (0, 0), others disappear
    if (node.agent_id === state.focusAgent) {
      return { x: 0, y: 0, angleRad: 0, isCenter: true, hidden: false };
    }
    if (node.parent_department === state.focusAgent || node.parent_agent === state.focusAgent) {
      const offsetAngle = (now ? now * 0.001 : 0);
      return { x: Math.cos(offsetAngle) * 150, y: Math.sin(offsetAngle) * 150, angleRad: offsetAngle, hidden: false };
    }
    return { x: 99999, y: 99999, angleRad: 0, hidden: true };
  }

  // -------------------------------------------------------------
  // MODE A — NR-AI / NORMAL GALAXY (DEFAULT & UPON RETURN TO NR-AI)
  // NR-AI is central focus AND ALL REGISTERED AGENTS ARE VISIBLE!
  // NO UNRELATED AGENT IS HIDDEN.
  // -------------------------------------------------------------
  // Droid Scout & Guardian orbit Droid as moons/satellites
  if (node.parent_department === "android_unified_agent" || node.parent_agent === "android_unified_agent") {
    const droidNode = state.galaxy && state.galaxy.nodes ? state.galaxy.nodes.find(n => n.agent_id === "android_unified_agent") : null;
    let basePos = { x: 180, y: 180 };
    if (droidNode) {
      const dRing = droidNode.orbit_ring || 1;
      const dSpeed = 0.00003 * (4 - dRing);
      const dRad = ((droidNode.orbit_angle * Math.PI) / 180) + (now ? now * dSpeed : 0);
      basePos = { x: Math.cos(dRad) * droidNode.orbit_radius, y: Math.sin(dRad) * droidNode.orbit_radius };
    }
    const offsetAngle = node.agent_id === "droid_scout" ? (now ? now * 0.001 : 0) : ((now ? now * 0.001 : 0) + Math.PI);
    const satelliteR = node.agent_id === "droid_scout" ? 48 : 64;
    return {
      x: basePos.x + Math.cos(offsetAngle) * satelliteR,
      y: basePos.y + Math.sin(offsetAngle) * satelliteR,
      angleRad: offsetAngle,
      hidden: false,
    };
  }

  // Standard celestial orbital revolution for all top-level specialist agents
  const ring = node.orbit_ring || 1;
  const speed = 0.00003 * (4 - ring);
  const rad = ((node.orbit_angle * Math.PI) / 180) + (now ? now * speed : 0);
  return {
    x: Math.cos(rad) * node.orbit_radius,
    y: Math.sin(rad) * node.orbit_radius,
    angleRad: rad,
    hidden: false,
  };
}
window.getNodePosition = getNodePosition;

function drawOrbits() {
  ctx.save();
  if (state.galaxyMode === "FOCUS" && isDroidFocused()) {
    // Focused Droid Satellite Orbit Rings
    for (const r of [150, 185]) {
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(16, 185, 129, 0.25)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 8]);
      ctx.stroke();
    }
  } else if (state.galaxyMode === "FOCUS") {
    // Single specialist orbit ring
    ctx.beginPath();
    ctx.arc(0, 0, 150, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(56, 189, 248, 0.2)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([4, 8]);
    ctx.stroke();
  } else {
    // Normal concentric celestial rings (Mode A)
    const rings = (state.galaxy && state.galaxy.orbital_rings) || [260, 400, 540];
    for (const r of rings) {
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(56, 189, 248, 0.09)";
      ctx.lineWidth = 1.2;
      ctx.setLineDash([4, 10]);
      ctx.stroke();
    }
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
      if (isDroidFocused()) {
        // In Focus Mode, only draw Droid to child satellites connections
        if (conn.from === "android_unified_agent" || conn.is_droid_hierarchy) {
          const toNode = nodeMap.get(conn.to);
          if (!toNode) continue;
          const pos = getNodePosition(toNode, now);
          if (pos.hidden) continue;
          ctx.beginPath();
          ctx.moveTo(0, 0);
          ctx.lineTo(pos.x, pos.y);
          ctx.strokeStyle = toNode.agent_id === "droid_scout" ? "#34d399" : "#10b981";
          ctx.lineWidth = 2.5;
          ctx.shadowColor = ctx.strokeStyle;
          ctx.shadowBlur = 12;
          ctx.stroke();

          // Data particle
          const t = ((now * 0.002) % 1);
          ctx.beginPath();
          ctx.arc(pos.x * t, pos.y * t, 3.5, 0, Math.PI * 2);
          ctx.fillStyle = "#ffffff";
          ctx.fill();
        }
        continue;
      }

      if (conn.from === "nr_ai_central_intelligence") {
        const toNode = nodeMap.get(conn.to);
        if (!toNode) continue;
        const pos = getNodePosition(toNode, now);
        if (pos.hidden) continue;
        const isSpeaker = state.introMode && state.currentSpeakerId === toNode.agent_id;
        const isFiltered = isNodeFilteredOut(toNode);
        const alpha = isFiltered ? 0.05 : (state.introMode && !isSpeaker ? 0.12 : 0.25);

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
  // In Specialist Focus Mode, the focused agent is the sole center; suppress NR-AI core vortex
  if (state.galaxyMode === "FOCUS" && state.focusAgent && state.focusAgent !== "nr_ai_central_intelligence") {
    return;
  }
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
    const pos = getNodePosition(node, now);
    if (pos.hidden) continue;
    const isFiltered = isNodeFilteredOut(node);
    const isSelected = state.selectedNode && state.selectedNode.agent_id === node.agent_id;
    const isSpeaker = state.introMode && state.currentSpeakerId === node.agent_id;

    ctx.save();
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

  // SkyShield Security Agent: Dedicated Security Command Center
  const isSkyShield = (node.agent_id === "security_agent" || node.agent_id === "skyshield");
  if (isSkyShield) {
    state.activeConversationAgent = "security_agent";
    state.activeConversationAgentName = "SkyShield";

    const banner = document.getElementById("activeChatBanner");
    const bannerName = document.getElementById("activeChatAgentName");
    if (banner) banner.style.display = "flex";
    if (bannerName) bannerName.textContent = "SkyShield (Security Command Center)";

    const panel = document.getElementById("agentPanel");
    if (panel) {
      panel.classList.add("open");
      panel.classList.add("skyshield-mode");
    }

    const stdView = document.getElementById("standardAgentView");
    const skyView = document.getElementById("skyshieldCommandCenterView");
    if (stdView) stdView.style.display = "none";
    if (skyView) skyView.style.display = "flex";

    const nameElem = document.getElementById("panelAgentName");
    if (nameElem) nameElem.textContent = "SkyShield";
    const roleElem = document.getElementById("panelAgentRole");
    if (roleElem) roleElem.textContent = "Security Agent";
    const avatar = document.getElementById("panelAgentAvatar");
    if (avatar) {
      avatar.textContent = "🛡️";
      avatar.style.borderColor = "#ef4444";
      avatar.style.boxShadow = "0 0 16px rgba(239, 68, 68, 0.4)";
    }
    const statusBadge = document.getElementById("panelStatusPill");
    if (statusBadge) {
      statusBadge.textContent = "● ACTIVE";
      statusBadge.style.color = "#ef4444";
      statusBadge.style.borderColor = "#ef4444";
      statusBadge.style.background = "rgba(239, 68, 68, 0.15)";
    }

    renderSkyShieldDashboard();
    return;
  }

  // Restore standard specialist view if previously SkyShield
  const panel = document.getElementById("agentPanel");
  if (panel) panel.classList.remove("skyshield-mode");
  const stdView = document.getElementById("standardAgentView");
  const skyView = document.getElementById("skyshieldCommandCenterView");
  if (stdView) stdView.style.display = "block";
  if (skyView) skyView.style.display = "none";

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

  // Asynchronously activate agent session (session-aware greeting) only if NOT in voice session
  if (!state.voiceSessionActive) {
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
  }

  // Load detailed workspace context (Focus, checklist, telemetry)
  loadAgentWorkspace(targetAgentId);

  // Load existing chat history from the ONE Knowledge workspace
  loadAgentChatHistory(targetAgentId);
}
window.selectAgent = selectAgent;

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
    if (actionsGrid) {
      actionsGrid.innerHTML = "";
      let actions = ctx.suggested_actions || [];

      // Droid Phase 2 Specialist Actions
      if (agentId === "android_unified_agent" || agentId === "android_studio_agent") {
        try {
          const dRes = await fetch("/api/droid/status");
          if (dRes.ok) {
            const dData = await dRes.json();
            const dStatus = (dData.status && dData.status.state) || "AVAILABLE";
            if (currTask) currTask.textContent = `AVD Pixel_6_API_34: ${dStatus}`;
          }
        } catch (_) {}

        actions = [
          { id: "droid_boot_pixel6", label: "Boot Pixel 6 (API 34)" },
          { id: "droid_deploy_app", label: "Deploy & Launch App" },
          { id: "droid_preview_compose", label: "Inspect Compose Previews" },
          { id: "droid_verify_ui", label: "Verify UI Assertions" },
          { id: "droid_capture_screen", label: "Capture Screen" },
          { id: "droid_reproduce_bug", label: "Reproduce Failure" },
          { id: "droid_diagnose_root_cause", label: "Diagnose Root Cause" },
          { id: "droid_e2e_engineering", label: "Run E2E Engineering Loop" },
          ...actions
        ];
      }

      actions.forEach(act => {
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

async function clearActiveAgentChat() {
  const canonId = getActiveCanonicalAgentId();
  if (state.conversationStore && state.conversationStore[canonId]) {
    state.conversationStore[canonId] = [];
  }
  const container = document.getElementById("dedicatedChatHistory");
  if (container) {
    container.innerHTML = "";
  }
  const oldContainer = document.getElementById("panelChatHistory");
  if (oldContainer) {
    oldContainer.innerHTML = "";
  }
  try {
    await fetch(`/api/agent/${canonId}/clear`, { method: "POST" });
  } catch (e) {
    console.warn("Failed to clear backend chat history:", e);
  }
}
window.clearActiveAgentChat = clearActiveAgentChat;

function closeAgentPanel() {
  const panel = document.getElementById("agentPanel");
  if (panel) {
    panel.classList.remove("open");
    panel.classList.remove("skyshield-mode");
  }
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

  // If Droid (Android Agent) active, render real-time in-flight engineering progress banner
  let progressPollTimer = null;
  if ((aid === "android_unified_agent" || aid === "droid") && container) {
    const progressElem = document.createElement("div");
    progressElem.id = "droidProgressBanner";
    progressElem.className = "droid-progress-bubble";
    progressElem.innerHTML = `
      <div class="droid-progress-card" style="font-family: monospace; background: rgba(16, 24, 40, 0.95); border: 1px solid rgba(0, 255, 204, 0.4); border-radius: 8px; padding: 12px; margin: 4px 0; color: #e2e8f0;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
          <span style="color: #00ffcc; font-weight: bold;">DROID ● <span id="droidProgressStage">UNDERSTANDING</span></span>
          <span id="droidProgressPct" style="color: #38bdf8; font-weight: bold;">[░░░░░░░░░░░░░░] 0%</span>
        </div>
        <div style="background: rgba(255,255,255,0.1); border-radius: 4px; height: 8px; width: 100%; margin-bottom: 8px; overflow: hidden;">
          <div id="droidProgressBarFill" style="background: linear-gradient(90deg, #00ffcc, #38bdf8); height: 100%; width: 5%; transition: width 0.3s ease;"></div>
        </div>
        <div id="droidProgressMsg" style="font-size: 13px; color: #f1f5f9; margin-bottom: 4px;">Understanding command...</div>
        <div id="droidProgressEvidence" style="font-size: 11px; color: #94a3b8; font-style: italic;">Awaiting toolchain execution...</div>
      </div>
    `;
    container.appendChild(progressElem);
    container.scrollTop = container.scrollHeight;

    // Start active polling of /api/engineering/progress
    progressPollTimer = setInterval(async () => {
      try {
        const pRes = await fetch("/api/engineering/progress");
        if (!pRes.ok) return;
        const pData = await pRes.json();
        if (pData.success && pData.progress) {
          const p = pData.progress;
          const stageElem = document.getElementById("droidProgressStage");
          const pctElem = document.getElementById("droidProgressPct");
          const fillElem = document.getElementById("droidProgressBarFill");
          const msgElem = document.getElementById("droidProgressMsg");
          const evElem = document.getElementById("droidProgressEvidence");

          if (stageElem) stageElem.textContent = p.stage || "EXECUTING";
          if (pctElem) pctElem.textContent = p.ascii_bar || `[${p.progress}%]`;
          if (fillElem) fillElem.style.width = `${Math.max(5, p.progress || 0)}%`;
          if (msgElem) msgElem.textContent = p.message || "Working...";
          if (evElem && p.evidence && p.evidence.length > 0) {
            evElem.textContent = "Evidence: " + p.evidence.join(" | ");
          }
          if (container) container.scrollTop = container.scrollHeight;
        }
      } catch (e) {
        // ignore polling errors
      }
    }, 250);
  }

  try {
    updatePttState("RESPONDING");
    const res = await fetch(`/api/agent/${aid}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, speak_output: false })
    });

    // Remove in-flight indicators
    if (progressPollTimer) clearInterval(progressPollTimer);
    const liveIndicator = document.getElementById("trinityActivityIndicator");
    if (liveIndicator) liveIndicator.remove();
    const liveProg = document.getElementById("droidProgressBanner");
    if (liveProg) liveProg.remove();

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
    if (progressPollTimer) clearInterval(progressPollTimer);
    const liveIndicator = document.getElementById("trinityActivityIndicator");
    if (liveIndicator) liveIndicator.remove();
    const liveProg = document.getElementById("droidProgressBanner");
    if (liveProg) liveProg.remove();
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

  if (state.voiceSession !== "INACTIVE") {
    setVoiceSessionState("SPEAKING");
  }
  if (typeof updateVoiceTelemetry === "function") {
    updateVoiceTelemetry({ speech: "SPEAKING (TTS)", audio: "TTS PLAYING", event: "speakText" });
  }

  const synth = window.speechSynthesis || state.speechSynth;
  if (state.ttsEnabled && synth) {
    synth.cancel();
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
        clearTimeout(state.introTimer);
        state.isSpeakingAudio = false;
        if (eq) eq.classList.remove("active");
        if (typeof updateVoiceTelemetry === "function") {
          updateVoiceTelemetry({ speech: "IDLE", audio: "NO AUDIO", event: "tts_ended" });
        }
        if (state.voiceSessionActive && !state.voiceStopRequested) {
          safeStartRecognition();
        }
        if (onComplete) onComplete();
      }
    };

    utterance.onend = finish;
    utterance.onerror = (e) => {
      console.warn("Speech synthesis error:", e);
      finish();
    };

    synth.speak(utterance);

    // Safety timeout in case speech synthesis hangs or voices unavailable
    const maxDuration = Math.max(800, Math.min(2500, text.length * 35));
    state.introTimer = setTimeout(finish, maxDuration);
  } else {
    // Voice unavailable: display clearly and advance after reading delay
    state.isSpeakingAudio = false;
    if (eq) eq.classList.remove("active");
    if (notice) {
      notice.style.display = "block";
      notice.textContent = "VOICE UNAVAILABLE • TEXT DISPLAY";
    }

    const readDuration = Math.max(1200, Math.min(3500, text.length * 35));
    state.introTimer = setTimeout(() => {
      if (state.voiceSessionActive && !state.voiceStopRequested) {
        safeStartRecognition();
      }
      if (onComplete) onComplete();
    }, readDuration);
  }
}
window.speakText = speakText;

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
    let result = null;
    if (actionId === "droid_boot_pixel6") {
      const res = await fetch("/api/droid/boot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ avd_name: "Pixel_6_API_34", timeout_seconds: 15.0 }),
      });
      result = await res.json();
      result.message = result.report ? `Droid: AVD state is ${result.report.state}. ${result.report.message}` : "Boot completed.";
    } else if (actionId === "droid_deploy_app") {
      const res = await fetch("/api/droid/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ package_name: "com.nrai.test", activity_name: "MainActivity" }),
      });
      result = await res.json();
      result.message = result.result ? `Droid: Deployment result: ${result.result.message || result.result.state}` : "Deploy evaluated.";
    } else if (actionId === "droid_preview_compose") {
      const res = await fetch("/api/droid/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      result = await res.json();
      result.message = result.report ? `Droid: Found ${result.report.total_previews} @Preview composables.` : "Preview analyzed.";
    } else if (actionId === "droid_verify_ui") {
      const res = await fetch("/api/droid/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ assertions: [{ assertion_type: "SCREEN_NOT_EMPTY", query: "screen" }] }),
      });
      result = await res.json();
      result.message = result.report ? `Droid: UI Verification ${result.report.status} (${result.report.passed_count}/${result.report.total_assertions} passed).` : "Verification complete.";
    } else if (actionId === "droid_capture_screen") {
      const res = await fetch("/api/droid/screenshots");
      result = await res.json();
      result.message = `Droid: Screenshot management active (${(result.screenshots || []).length} stored).`;
    } else {
      const res = await fetch(`/api/agent/${agentId}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: actionId, parameters: {} }),
      });
      result = await res.json();
    }
    thinkingMsg.classList.remove("thinking");
    thinkingMsg.textContent = result.message || (typeof result.result === 'string' ? result.result : (result.result && result.result.message)) || (result.success ? `Action '${actionId}' completed.` : `Action '${actionId}' failed.`);

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

    const prevFocusMode = state.focusAgent !== null;
    const prevFocusAgent = state.focusAgent;
    const prevSelectedNode = state.selectedNode ? state.selectedNode.agent_id : null;
    state.galaxy = data;
    if (prevFocusMode) {
      state.galaxy.focus_mode = true;
      state.galaxy.focus_agent_id = prevFocusAgent;
      state.galaxy.selected_node_id = prevSelectedNode;
    }
    updateHUDTelemetry(data.system_metrics);
    updateVoiceUI();

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
// Voice Recognition (Dictation) & Live Telemetry Engine
// -----------------------------------------------------------------------------
function updateVoiceTelemetry(fields = {}) {
  const map = {
    speech: document.getElementById("dbgSpeech"),
    mic: document.getElementById("dbgMic"),
    recognition: document.getElementById("dbgRec"),
    audio: document.getElementById("dbgAudio"),
    event: document.getElementById("dbgEvent"),
    error: document.getElementById("dbgError")
  };

  for (const [key, el] of Object.entries(map)) {
    if (!el || fields[key] === undefined) continue;
    const val = String(fields[key]);
    el.textContent = val;
    el.classList.remove("active", "warn", "error");
    const upper = val.toUpperCase();
    if (["RUNNING", "ACTIVE", "STARTED", "DETECTED", "LIVE", "CAPTURED"].some(s => upper.includes(s))) {
      el.classList.add("active");
    } else if (["STARTING", "HEARING", "LISTENING", "SPEAKING", "PROCESSING", "PAUSED", "CAPTURING", "SOUND", "SPEECH", "WARN", "STANDBY"].some(s => upper.includes(s))) {
      el.classList.add("warn");
    } else if (["ERROR", "FAILED", "NOT-ALLOWED", "AUDIO-CAPTURE", "DENIED"].some(s => upper.includes(s))) {
      el.classList.add("error");
    }
  }
}
window.updateVoiceTelemetry = updateVoiceTelemetry;

async function ensureMicrophone() {
  if (state.micStream && state.micStream.active && state.micStream.getAudioTracks().some(t => t.readyState === "live")) {
    updateVoiceTelemetry({ mic: "ACTIVE" });
    return true;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    console.warn("navigator.mediaDevices.getUserMedia unavailable");
    updateVoiceTelemetry({ mic: "UNAVAILABLE" });
    return true;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    });
    state.micStream = stream;
    const tracks = stream.getAudioTracks();
    const liveTrack = tracks.find(t => t.readyState === "live");
    if (liveTrack) {
      liveTrack.onended = () => {
        updateVoiceTelemetry({ mic: "INACTIVE" });
      };
      liveTrack.onmute = () => {
        updateVoiceTelemetry({ mic: "MUTED" });
      };
      liveTrack.onunmute = () => {
        updateVoiceTelemetry({ mic: "ACTIVE" });
      };
      updateVoiceTelemetry({ mic: "ACTIVE" });
      return true;
    }
    updateVoiceTelemetry({ mic: "NO TRACK" });
    return true;
  } catch (err) {
    console.warn("Microphone access prompt error:", err.name, err.message);
    updateVoiceTelemetry({ mic: "DENIED", error: err.name || "not-allowed" });
    return false;
  }
}
window.ensureMicrophone = ensureMicrophone;

function updateInterimUserTranscript(text) {
  if (!text || !text.trim()) return;
  const container = document.getElementById("dedicatedChatHistory");
  if (!container) return;

  const notice = document.getElementById("chatLiveInterimNotice");
  const noticeText = document.getElementById("chatLiveInterimText");
  if (notice && noticeText) {
    noticeText.textContent = `Hearing: "${text}..."`;
    notice.style.display = "flex";
  }

  let bubble = document.getElementById("interimUserChatBubble");
  if (!bubble) {
    bubble = document.createElement("div");
    bubble.id = "interimUserChatBubble";
    bubble.className = "chat-bubble user interim";

    const authorSpan = document.createElement("span");
    authorSpan.className = "bubble-author";
    authorSpan.textContent = "USER";

    const textNode = document.createElement("div");
    textNode.className = "bubble-text";
    textNode.textContent = `${text}...`;

    bubble.appendChild(authorSpan);
    bubble.appendChild(textNode);
    container.appendChild(bubble);
  } else {
    const textNode = bubble.querySelector(".bubble-text");
    if (textNode) textNode.textContent = `${text}...`;
  }
  container.scrollTop = container.scrollHeight;
}
window.updateInterimUserTranscript = updateInterimUserTranscript;

function finalizeUserTranscript(finalText) {
  const notice = document.getElementById("chatLiveInterimNotice");
  if (notice) notice.style.display = "none";

  const bubble = document.getElementById("interimUserChatBubble");
  if (bubble) {
    bubble.remove();
  }
  appendChatMessage("USER", "user", finalText);
}
window.finalizeUserTranscript = finalizeUserTranscript;

function initSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    console.warn("SpeechRecognition not supported in browser engine.");
    updateVoiceTelemetry({ speech: "UNSUPPORTED", recognition: "NOT SUPPORTED", error: "SpeechRecognition missing" });
    return null;
  }

  if (state.recognition) {
    return state.recognition;
  }

  const rec = new SpeechRecognition();
  rec.continuous = true;
  rec.interimResults = true;
  rec.maxAlternatives = 1;
  rec.lang = navigator.language || "en-IN";

  rec.onstart = () => {
    state.isRecognitionStarting = false;
    state.isRecognitionActive = true;
    updateVoiceTelemetry({
      speech: "RUNNING",
      recognition: "STARTED",
      audio: "LISTENING",
      event: "onstart",
      error: "NONE"
    });
    // CRITICAL: Only transition to LISTENING once onstart actually fires from browser and assistant is not speaking!
    if (state.voiceSessionActive && !state.voiceStopRequested && !state.isSpeakingAudio) {
      setVoiceSessionState("LISTENING", state.activeAgent || "NR-AI");
    }
  };

  rec.onaudiostart = () => {
    updateVoiceTelemetry({ audio: "CAPTURING", event: "onaudiostart" });
  };

  rec.onsoundstart = () => {
    updateVoiceTelemetry({ audio: "SOUND DETECTED", event: "onsoundstart" });
  };

  rec.onspeechstart = () => {
    updateVoiceTelemetry({ audio: "SPEECH DETECTED", event: "onspeechstart" });
    if (state.voiceSessionActive && !state.voiceStopRequested) {
      if (state.isSpeakingAudio || (state.speechSynth && state.speechSynth.speaking)) {
        console.log("⚡ Barge-in: interrupting speech output.");
        interruptSpeech();
      }
      setVoiceSessionState("HEARING", state.activeAgent || "NR-AI");
    }
  };

  rec.onspeechend = () => {
    updateVoiceTelemetry({ audio: "SPEECH ENDED", event: "onspeechend" });
  };

  rec.onsoundend = () => {
    updateVoiceTelemetry({ audio: "SOUND ENDED", event: "onsoundend" });
  };

  rec.onaudioend = () => {
    updateVoiceTelemetry({ audio: "AUDIO ENDED", event: "onaudioend" });
  };

  rec.onresult = (event) => {
    // Discard speech if voice session is inactive or stop requested
    if (!state.voiceSessionActive || state.voiceStopRequested) {
      console.log("Speech input discarded: voice session is inactive.");
      return;
    }

    if (state.isSpeakingAudio || (state.speechSynth && state.speechSynth.speaking)) {
      console.log("⚡ Barge-in at onresult: interrupting speech output.");
      interruptSpeech();
    }

    let finalTranscript = "";
    let interimTranscript = "";
    for (let i = event.resultIndex; i < event.results.length; ++i) {
      const res = event.results[i];
      const trans = res[0] ? res[0].transcript : "";
      const isFin = Boolean(res.isFinal || (res[0] && res[0].isFinal));
      if (isFin) {
        finalTranscript += trans;
      } else {
        interimTranscript += trans;
      }
    }

    if (interimTranscript && !finalTranscript) {
      updateVoiceTelemetry({ audio: "HEARING: " + interimTranscript.slice(0, 16), event: "onresult(interim)" });
      setVoiceSessionState("HEARING", state.activeAgent || "NR-AI");
      updateInterimUserTranscript(interimTranscript.trim());
      return;
    }

    if (!finalTranscript && event.results && event.results[0] && event.results[0][0]) {
      finalTranscript = event.results[0][0].transcript;
    }

    if (finalTranscript && finalTranscript.trim()) {
      const cleanFinal = finalTranscript.trim();
      console.log("🎤 Speech finalized:", cleanFinal);
      updateVoiceTelemetry({ audio: "CAPTURED", event: "onresult(final)" });
      finalizeUserTranscript(cleanFinal);
      setVoiceSessionState("PROCESSING", state.activeAgent || "NR-AI");
      dispatchVoiceUtterance(cleanFinal, false, true);
    }
  };

  rec.onerror = (e) => {
    console.warn("Speech recognition error:", e.error);
    updateVoiceTelemetry({ event: "onerror: " + e.error, error: e.error || "unknown" });

    if (e.error === "no-speech") {
      updateVoiceTelemetry({ audio: "NO AUDIO", error: "no-speech (silence)" });
      return;
    }

    if (e.error === "aborted") {
      const isStandby = state.voiceStopRequested || !state.voiceSessionActive;
      updateVoiceTelemetry({
        recognition: "STOPPED",
        error: isStandby ? "STANDBY" : "aborted"
      });
      return;
    }

    if (e.error === "not-allowed" || e.error === "audio-capture") {
      state.voiceSessionActive = false;
      state.isRecognitionActive = false;
      state.isRecognitionStarting = false;
      setVoiceSessionState("MICROPHONE ERROR");
      updateVoiceTelemetry({ speech: "ERROR", mic: "ERROR: " + e.error, recognition: "STOPPED", error: e.error });
    } else if (e.error === "network") {
      updateVoiceTelemetry({ speech: "WARN", error: "network" });
    }
  };

  rec.onend = () => {
    state.isRecognitionActive = false;
    state.isRecognitionStarting = false;
    updateVoiceTelemetry({ recognition: "STOPPED", event: "onend" });

    const notice = document.getElementById("chatLiveInterimNotice");
    if (notice) notice.style.display = "none";

    // Auto-restart recognition in continuous mode if session is active
    if (state.voiceSessionActive && !state.voiceStopRequested &&
        state.voiceSession !== "INACTIVE" &&
        state.voiceSession !== "MICROPHONE ERROR" &&
        state.voiceSession !== "VOICE ERROR") {
      if (state.voiceSession !== "SPEAKING" && state.voiceSession !== "PROCESSING") {
        setTimeout(() => {
          if (state.voiceSessionActive && !state.voiceStopRequested &&
              state.voiceSession !== "INACTIVE" &&
              !state.isRecognitionActive && !state.isRecognitionStarting &&
              state.voiceSession !== "SPEAKING" && state.voiceSession !== "PROCESSING") {
            safeStartRecognition();
          }
        }, 100);
      }
    }
  };

  state.recognition = rec;
  updateVoiceTelemetry({ speech: "READY", mic: "INACTIVE", recognition: "STOPPED", audio: "NO AUDIO", event: "initialized", error: "NONE" });
  return rec;
}
window.initSpeechRecognition = initSpeechRecognition;

async function safeStartRecognition() {
  if (!state.voiceSessionActive || state.voiceStopRequested) {
    updateVoiceTelemetry({ recognition: "STOPPED" });
    return false;
  }

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    state.voiceSessionActive = false;
    setVoiceSessionState("VOICE ERROR");
    updateVoiceTelemetry({ speech: "ERROR", error: "SpeechRecognition unsupported" });
    return false;
  }

  if (state.isRecognitionActive) {
    if (state.voiceSessionActive && !state.voiceStopRequested && !state.isSpeakingAudio) {
      setVoiceSessionState("LISTENING", state.activeAgent || "NR-AI");
    }
    updateVoiceTelemetry({ speech: "RUNNING", recognition: "STARTED" });
    return true;
  }

  if (state.isRecognitionStarting) {
    return true;
  }

  const hasMic = await ensureMicrophone();
  if (!hasMic || !state.voiceSessionActive || state.voiceStopRequested) {
    return false;
  }

  if (!state.recognition) {
    initSpeechRecognition();
  }

  try {
    state.isRecognitionStarting = true;
    updateVoiceTelemetry({ speech: "STARTING", recognition: "STARTING...", event: "safeStartRecognition" });
    state.recognition.start();
    return true;
  } catch (e) {
    state.isRecognitionStarting = false;
    if (e.name === "InvalidStateError") {
      state.isRecognitionActive = true;
      if (state.voiceSessionActive && !state.voiceStopRequested && !state.isSpeakingAudio) {
        setVoiceSessionState("LISTENING", state.activeAgent || "NR-AI");
      }
      updateVoiceTelemetry({ speech: "RUNNING", recognition: "STARTED" });
      return true;
    } else {
      console.warn("safeStartRecognition error:", e);
      updateVoiceTelemetry({ speech: "ERROR", error: e.name || e.message });
      return false;
    }
  }
}
window.safeStartRecognition = safeStartRecognition;
window.startContinuousRecognition = safeStartRecognition;

async function toggleVoiceSession() {
  if (!state.voiceSessionActive || state.voiceSession === "INACTIVE" || state.voiceStopRequested) {
    await startManualVoiceSession();
  } else {
    stopVoiceCommunication();
  }
}
window.toggleVoiceSession = toggleVoiceSession;
window.toggleVoiceInput = toggleVoiceSession;

// -----------------------------------------------------------------------------
// SkyShield Security Command Center Engine
// -----------------------------------------------------------------------------
async function renderSkyShieldDashboard(cachedData) {
  let data = cachedData;
  if (!data) {
    try {
      const res = await fetch("/api/skyshield/dashboard");
      if (res.ok) {
        data = await res.json();
      }
    } catch (e) {
      console.warn("Failed to load SkyShield dashboard:", e);
    }
  }

  if (!data) return;

  // 1. Hero Card & Security Status
  const status = data.overall_security_status || "SECURE";
  const stateVal = data.state || "IDLE";
  const isEstop = Boolean(data.emergency_stop_active || stateVal === "STOPPED");

  const badgeElem = document.getElementById("skyshieldSourceBadge");
  if (badgeElem) {
    const src = (data.device && data.device.verification_state) || "LIVE";
    badgeElem.textContent = `${src} TELEMETRY`;
    badgeElem.className = `skyshield-badge ${src.toLowerCase()}`;
  }

  const statePill = document.getElementById("skyshieldStatePill");
  if (statePill) {
    statePill.textContent = stateVal;
    if (isEstop) {
      statePill.style.color = "#ef4444";
      statePill.style.borderColor = "#ef4444";
    } else {
      statePill.style.color = "#38bdf8";
      statePill.style.borderColor = "rgba(56, 189, 248, 0.4)";
    }
  }

  const statusBox = document.getElementById("skyshieldSecurityStatus");
  const statusIcon = document.getElementById("skyshieldStatusIcon");
  const statusText = document.getElementById("skyshieldStatusText");
  const heroDesc = document.getElementById("skyshieldHeroDesc");

  if (statusBox && statusText) {
    statusBox.className = `skyshield-security-status ${status.toLowerCase()}`;
    statusText.textContent = status;
    if (statusIcon) {
      if (isEstop || status === "STOPPED") statusIcon.textContent = "🛑";
      else if (status === "CRITICAL") statusIcon.textContent = "🚨";
      else if (status === "ELEVATED" || status === "ATTENTION") statusIcon.textContent = "⚠️";
      else statusIcon.textContent = "🛡️";
    }
  }

  if (heroDesc) {
    if (isEstop) {
      heroDesc.textContent = "🛑 EMERGENCY STOP is currently ACTIVE. All active scanning and operations halted.";
    } else if (status === "CRITICAL") {
      heroDesc.textContent = "Critical security anomaly detected. Review threats and isolate affected components immediately.";
    } else if (status === "ATTENTION" || status === "ELEVATED") {
      heroDesc.textContent = "Attention required. Elevated risk factors or potential permission anomalies identified.";
    } else {
      heroDesc.textContent = "System operating within verified security policy parameters. Zero covert actions detected.";
    }
  }

  // 2. Device Card
  if (data.device) {
    const dev = data.device;
    const devOs = document.getElementById("devOs");
    const devModel = document.getElementById("devModel");
    const devRooted = document.getElementById("devRooted");
    const devVerif = document.getElementById("devVerif");
    const devTag = document.getElementById("cardDeviceTag");

    if (devOs) devOs.textContent = `${dev.platform || "Host"} ${dev.os_version || ""}`.trim();
    if (devModel) devModel.textContent = dev.device_model || dev.device_id || "Local Machine";
    if (devRooted) {
      devRooted.textContent = dev.is_rooted ? "POSITIVE (HIGH RISK)" : "Negative (Verified)";
      devRooted.style.color = dev.is_rooted ? "#ef4444" : "#10b981";
    }
    if (devVerif) devVerif.textContent = dev.verification_state || "LIVE";
    if (devTag) devTag.textContent = dev.security_status || "VERIFIED";
  }

  // 3. Applications Cards (WhatsApp, Instagram, Snapchat)
  if (data.applications && Array.isArray(data.applications)) {
    const wa = data.applications.find(a => a.package_name === "com.whatsapp");
    if (wa) {
      const el = document.getElementById("waVerif");
      const tag = document.getElementById("cardWhatsAppTag");
      if (el) el.textContent = wa.verification_state || "VERIFIED";
      if (tag) tag.textContent = wa.is_installed ? "INSTALLED / SANDBOXED" : "SANDBOXED";
    }

    const ig = data.applications.find(a => a.package_name === "com.instagram.android");
    if (ig) {
      const el = document.getElementById("igVerif");
      const tag = document.getElementById("cardInstagramTag");
      if (el) el.textContent = ig.verification_state || "VERIFIED";
      if (tag) tag.textContent = ig.is_installed ? "INSTALLED / SANDBOXED" : "SANDBOXED";
    }

    const sc = data.applications.find(a => a.package_name === "com.snapchat.android");
    if (sc) {
      const el = document.getElementById("scVerif");
      const tag = document.getElementById("cardSnapchatTag");
      if (el) el.textContent = sc.verification_state || "VERIFIED";
      if (tag) tag.textContent = sc.is_installed ? "INSTALLED / SANDBOXED" : "SANDBOXED";
    }
  }

  // 4. Camera Card
  if (data.camera) {
    const cam = data.camera;
    const camActive = document.getElementById("camActive");
    const camStreams = document.getElementById("camStreams");
    const camVerif = document.getElementById("camVerif");
    const camTag = document.getElementById("cardCameraTag");

    if (camActive) {
      camActive.textContent = cam.is_sensor_active ? "ACTIVE STREAM" : "No (Idle)";
      camActive.style.color = cam.is_sensor_active ? "#ef4444" : "#10b981";
    }
    if (camStreams) camStreams.textContent = String(cam.active_streams || 0);
    if (camVerif) camVerif.textContent = cam.verification_state || "LIVE_AUDIT";
    if (camTag) camTag.textContent = cam.is_sensor_active ? "STREAMING" : "INACTIVE";
  }

  // 5. Microphone Card
  if (data.microphone) {
    const mic = data.microphone;
    const micActive = document.getElementById("micActive");
    const micSession = document.getElementById("micSession");
    const micVerif = document.getElementById("micVerif");
    const micTag = document.getElementById("cardMicrophoneTag");

    if (micActive) {
      micActive.textContent = mic.is_recording_active ? "RECORDING" : "No (Idle)";
      micActive.style.color = mic.is_recording_active ? "#ef4444" : "#10b981";
    }
    if (micSession) micSession.textContent = mic.authorized_session_id || "None";
    if (micVerif) micVerif.textContent = mic.verification_state || "LIVE_AUDIT";
    if (micTag) micTag.textContent = mic.is_recording_active ? "RECORDING" : "INACTIVE";
  }

  // 6. Permissions Matrix Card
  const permBody = document.getElementById("cardPermissionsBody");
  if (permBody && data.permissions) {
    let rowsHtml = '<table class="perm-matrix-table">';
    for (const [pName, pStatus] of Object.entries(data.permissions)) {
      const statLower = String(pStatus).toLowerCase();
      let badgeCls = "granted";
      if (statLower.includes("denied")) badgeCls = "denied";
      else if (statLower.includes("restricted") || statLower.includes("elevated")) badgeCls = "restricted";
      rowsHtml += `<tr>
        <td style="color: #cbd5e1; font-weight: 500;">${escapeHtml(pName)}</td>
        <td style="text-align: right;"><span class="perm-status-badge ${badgeCls}">${escapeHtml(pStatus)}</span></td>
      </tr>`;
    }
    rowsHtml += "</table>";
    permBody.innerHTML = rowsHtml;
  }

  // 7. Threats Card
  const threatsBody = document.getElementById("cardThreatsBody");
  const threatsCount = document.getElementById("cardThreatsCount");
  if (threatsBody && data.threats) {
    if (threatsCount) {
      threatsCount.textContent = `${data.threats.length} FINDINGS`;
      if (data.threats.length > 0) threatsCount.classList.add("has-threats");
      else threatsCount.classList.remove("has-threats");
    }

    if (data.threats.length === 0) {
      threatsBody.innerHTML = '<div class="skyshield-empty-state">No anomalous security threats detected.</div>';
    } else {
      let tHtml = "";
      data.threats.forEach(t => {
        const sevLower = String(t.severity || "info").toLowerCase();
        tHtml += `<div class="threat-finding-row">
          <div class="threat-header">
            <span class="threat-sev-pill ${sevLower}">${escapeHtml(t.severity)}</span>
            <span style="font-size: 0.65rem; color: #94a3b8;">${escapeHtml(t.category || "GENERAL")}</span>
          </div>
          <div class="threat-desc">${escapeHtml(t.description)}</div>
          <div class="threat-remedy">↳ ${escapeHtml(t.recommendation || "Maintain baseline")}</div>
        </div>`;
      });
      threatsBody.innerHTML = tHtml;
    }
  }

  // 8. Events Card
  const eventsBody = document.getElementById("cardEventsBody");
  if (eventsBody && data.events) {
    if (data.events.length === 0) {
      eventsBody.innerHTML = '<div class="skyshield-empty-state">No real-time security events logged.</div>';
    } else {
      let eHtml = "";
      data.events.slice(-8).reverse().forEach(ev => {
        eHtml += `<div class="audit-entry-row">
          <div class="audit-entry-top">
            <span>${escapeHtml(ev.timestamp || "")}</span>
            <span class="threat-sev-pill ${(ev.severity || "info").toLowerCase()}">${escapeHtml(ev.severity || "INFO")}</span>
          </div>
          <div class="audit-entry-action">${escapeHtml(ev.event_type || "")}</div>
          <div class="audit-entry-target">${escapeHtml(ev.details ? JSON.stringify(ev.details) : "")}</div>
        </div>`;
      });
      eventsBody.innerHTML = eHtml;
    }
  }

  // 9. Audit Log Card
  const auditBody = document.getElementById("cardAuditBody");
  if (auditBody && data.audit_log) {
    if (data.audit_log.length === 0) {
      auditBody.innerHTML = '<div class="skyshield-empty-state">Audit trail records will appear here.</div>';
    } else {
      let aHtml = "";
      data.audit_log.slice(-10).reverse().forEach(rec => {
        aHtml += `<div class="audit-entry-row">
          <div class="audit-entry-top">
            <span>${escapeHtml(rec.timestamp || "")}</span>
            <span style="color: ${rec.result === 'SUCCESS' ? '#10b981' : (rec.result === 'STOPPED' ? '#ef4444' : '#f59e0b')}">${escapeHtml(rec.result || "")}</span>
          </div>
          <div class="audit-entry-action">${escapeHtml(rec.operation || "")} <span style="font-weight: normal; color: #64748b;">(${escapeHtml(rec.initiator || "")})</span></div>
          <div class="audit-entry-target">Target: ${escapeHtml(rec.target || "")}</div>
        </div>`;
      });
      auditBody.innerHTML = aHtml;
    }
  }

  // 10. Enrolled Authorized Devices Card
  const devicesBody = document.getElementById("cardDevicesBody");
  const devicesCount = document.getElementById("cardDevicesCount");
  if (devicesBody && data.enrolled_devices) {
    if (devicesCount) {
      devicesCount.textContent = `${data.enrolled_devices.length} DEVICES`;
    }
    if (data.enrolled_devices.length === 0) {
      devicesBody.innerHTML = '<div class="skyshield-empty-state">No authorized devices paired yet. Click \'+ Pair Device\' to initiate.</div>';
    } else {
      let dHtml = "";
      data.enrolled_devices.forEach(dev => {
        const stateLower = String(dev.enrollment_state || "unregistered").toLowerCase();
        const masked = dev.phone_number_masked ? `<span style="font-size: 0.65rem; color: #64748b;">(📞 ${escapeHtml(dev.phone_number_masked)})</span>` : "";
        let actionButtons = "";
        if (dev.enrollment_state === "ENROLLED" || dev.enrollment_state === "AUTHENTICATED") {
          actionButtons = `
            <button class="device-action-btn suspend" onclick="suspendSkyShieldDevice('${dev.device_id}')">Suspend</button>
            <button class="device-action-btn revoke" onclick="revokeSkyShieldDevice('${dev.device_id}')">Revoke</button>
          `;
        } else if (dev.enrollment_state === "SUSPENDED") {
          actionButtons = `
            <button class="device-action-btn reauthorize" onclick="reauthorizeSkyShieldDevice('${dev.device_id}')">Reauthorize</button>
            <button class="device-action-btn revoke" onclick="revokeSkyShieldDevice('${dev.device_id}')">Revoke</button>
          `;
        } else if (dev.enrollment_state === "REVOKED") {
          actionButtons = `<span style="font-size: 0.62rem; color: #ef4444; font-weight: 700;">PERMANENTLY REVOKED</span>`;
        }

        dHtml += `<div class="device-entry-row">
          <div class="device-entry-top">
            <div class="device-title">
              <span>${escapeHtml(dev.device_name || dev.device_id)}</span>
              <span class="device-platform-badge">${escapeHtml(dev.platform || "android")}</span>
              ${masked}
            </div>
            <span class="device-state-pill ${stateLower}">${escapeHtml(dev.enrollment_state)}</span>
          </div>
          <div class="device-entry-meta">
            <div><strong>ID:</strong> <code style="font-size: 0.65rem; color: #38bdf8;">${escapeHtml(dev.device_id)}</code></div>
            <div><strong>Auth:</strong> ${escapeHtml(dev.authorization_state || "UNAUTHORIZED")} | <strong>Verification:</strong> ${escapeHtml(dev.verification_state || "LIVE")}</div>
            <div><strong>Scopes:</strong> <span style="color: #cbd5e1;">${escapeHtml((dev.granted_capabilities || []).join(", "))}</span></div>
          </div>
          <div class="device-entry-actions">
            ${actionButtons}
          </div>
        </div>`;
      });
      devicesBody.innerHTML = dHtml;
    }
  }

  // 11. Device Operational Health Card (Phase 3)
  const primaryHealth = data.primary_device_health || (data.device_health && data.device_health[0]);
  if (primaryHealth) {
    const tagEl = document.getElementById("cardDeviceHealthTag");
    if (tagEl) tagEl.textContent = primaryHealth.data_source || "MOCK";

    const statusEl = document.getElementById("cardDeviceHealthStatus");
    if (statusEl) {
      statusEl.textContent = primaryHealth.security_posture || "HEALTHY";
      const s = String(primaryHealth.security_posture).toUpperCase();
      if (s === "CRITICAL") {
        statusEl.style.background = "rgba(239, 68, 68, 0.2)";
        statusEl.style.color = "#ef4444";
      } else if (s === "WARNING") {
        statusEl.style.background = "rgba(245, 158, 11, 0.2)";
        statusEl.style.color = "#f59e0b";
      } else if (s === "DEGRADED") {
        statusEl.style.background = "rgba(234, 179, 8, 0.2)";
        statusEl.style.color = "#eab308";
      } else {
        statusEl.style.background = "rgba(16, 185, 129, 0.2)";
        statusEl.style.color = "#10b981";
      }
    }

    const devNameEl = document.getElementById("healthDevName");
    if (devNameEl) devNameEl.textContent = `${primaryHealth.platform || "Android"} (${primaryHealth.device_id})`;

    const battEl = document.getElementById("healthBattery");
    if (battEl) battEl.textContent = `${primaryHealth.battery_level}% (${primaryHealth.charging_state})`;

    const cpuEl = document.getElementById("healthCpu");
    if (cpuEl) cpuEl.textContent = `${primaryHealth.cpu_usage}%`;

    const memEl = document.getElementById("healthMemory");
    if (memEl) {
      const mu = primaryHealth.memory_usage || {};
      memEl.textContent = `${mu.percentage || 0}% (${mu.used_mb || 0} / ${mu.total_mb || 0} MB)`;
    }

    const storEl = document.getElementById("healthStorage");
    if (storEl) {
      const su = primaryHealth.storage_usage || {};
      storEl.textContent = `${su.percentage || 0}% (${su.used_gb || 0} / ${su.total_gb || 0} GB)`;
    }

    const netEl = document.getElementById("healthNetwork");
    if (netEl) netEl.textContent = `${primaryHealth.network_state} (${primaryHealth.network_type})`;

    const uptimeEl = document.getElementById("healthUptime");
    if (uptimeEl) {
      const sec = primaryHealth.uptime || 0;
      const h = Math.floor(sec / 3600);
      const m = Math.floor((sec % 3600) / 60);
      uptimeEl.textContent = `${h}h ${m}m (${Math.round(sec)}s)`;
    }

    const seenEl = document.getElementById("healthLastSeen");
    if (seenEl) seenEl.textContent = primaryHealth.last_seen_iso || "Recent heartbeat";
  }

  // 12. Detected Anomalies Card (Phase 3)
  const anomBody = document.getElementById("cardAnomaliesBody");
  const anomCount = document.getElementById("cardAnomaliesCount");
  const anomList = data.anomalies || [];
  if (anomCount) anomCount.textContent = `${anomList.length} ANOMALIES`;
  if (anomBody) {
    if (anomList.length === 0) {
      anomBody.innerHTML = '<div class="skyshield-empty-state">No active anomalies detected. All metrics within normal baseline.</div>';
    } else {
      let anomHtml = "";
      anomList.forEach(a => {
        const sevLower = String(a.severity || "info").toLowerCase();
        anomHtml += `
          <div class="anomaly-item-row ${sevLower}">
            <div class="anomaly-header">
              <span style="color: #38bdf8;">[${escapeHtml(a.category)}] ${escapeHtml(a.observed_value || "")}</span>
              <span class="threat-badge ${sevLower}">${escapeHtml(a.severity)}</span>
            </div>
            <div class="anomaly-evidence">${escapeHtml(a.evidence || "")}</div>
            <div style="font-size: 0.62rem; color: #94a3b8; display: flex; justify-content: space-between;">
              <span>Expected: ${escapeHtml(String(a.expected_range || "N/A"))}</span>
              <span>Confidence: ${Math.round((a.confidence || 1) * 100)}%</span>
            </div>
          </div>
        `;
      });
      anomBody.innerHTML = anomHtml;
    }
  }

  // 13. Security Posture Score Card (Phase 3)
  const postureData = data.primary_device_posture || (data.posture_scores && data.posture_scores[0]);
  if (postureData) {
    const scoreBadge = document.getElementById("cardPostureScore");
    if (scoreBadge) scoreBadge.textContent = `${postureData.score} / 100 [${postureData.rating}]`;

    const scoreVal = document.getElementById("postureScoreValue");
    if (scoreVal) {
      scoreVal.textContent = `${postureData.score} / 100`;
      scoreVal.style.color = postureData.score >= 80 ? "#10b981" : (postureData.score >= 50 ? "#f59e0b" : "#ef4444");
    }

    const ratingEl = document.getElementById("postureRating");
    if (ratingEl) ratingEl.textContent = postureData.rating;

    const factorsList = document.getElementById("postureFactorsList");
    if (factorsList && postureData.contributing_factors) {
      let fHtml = "";
      postureData.contributing_factors.forEach(f => {
        const isZero = f.deduction === 0;
        fHtml += `
          <div class="posture-factor-item">
            <div>
              <span style="font-weight: 600; color: #f1f5f9;">${escapeHtml(f.factor)}:</span>
              <span style="color: #94a3b8;"> ${escapeHtml(f.evidence)}</span>
            </div>
            <span class="posture-factor-deduction ${isZero ? 'zero' : ''}">${f.deduction > 0 ? '-' + f.deduction : f.deduction} pts</span>
          </div>
        `;
      });
      factorsList.innerHTML = fHtml;
    }
  }

  renderSkyShieldPhase4(data);

  // 14. Device Security Timeline Card (Phase 3)
  const timelineBody = document.getElementById("cardTimelineBody");
  if (timelineBody && data.events) {
    let tHtml = "";
    const recentEvts = data.events.slice(-8).reverse();
    if (recentEvts.length === 0) {
      timelineBody.innerHTML = '<div class="skyshield-empty-state">No security events recorded yet.</div>';
    } else {
      recentEvts.forEach(evt => {
        tHtml += `
          <div class="stream-event-row">
            <div class="stream-event-top">
              <span>${escapeHtml(evt.timestamp || "")}</span>
              <span class="stream-event-action">${escapeHtml(evt.action || "")}</span>
            </div>
            <div class="stream-event-msg">${escapeHtml(evt.result || "")}</div>
          </div>
        `;
      });
      timelineBody.innerHTML = tHtml;
    }
  }
}

async function triggerSkyShieldScan() {
  const btn = document.getElementById("btnSkyShieldScan");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span>⏳</span> <span>Scanning...</span>';
  }
  try {
    const res = await fetch("/api/skyshield/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scan_type: "full" })
    });
    if (res.ok) {
      const data = await res.json();
      renderSkyShieldDashboard(data.dashboard);
    }
  } catch (err) {
    console.warn("Scan failed:", err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span>▶</span> <span>Run Full Security Scan</span>';
    }
  }
}

async function triggerSkyShieldEmergencyStop() {
  try {
    const res = await fetch("/api/skyshield/emergency_stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "Operator Emergency Stop via Command Center" })
    });
    if (res.ok) {
      const data = await res.json();
      renderSkyShieldDashboard(data.dashboard);
    }
  } catch (err) {
    console.warn("Emergency stop trigger failed:", err);
  }
}

async function triggerSkyShieldReset() {
  try {
    const res = await fetch("/api/skyshield/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
    if (res.ok) {
      const data = await res.json();
      renderSkyShieldDashboard(data.dashboard);
    }
  } catch (err) {
    console.warn("Reset failed:", err);
  }
}

async function sendSkyShieldInput() {
  const input = document.getElementById("skyshieldTextInput");
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  await sendSkyShieldCommand(text);
}

async function sendSkyShieldCommand(cmd) {
  try {
    const res = await fetch("/api/agent/security_agent/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: cmd })
    });
    if (res.ok) {
      renderSkyShieldDashboard();
    }
  } catch (err) {
    console.warn("SkyShield command execution failed:", err);
  }
}

// ==============================================================================
// SkyShield Phase 2: Device Pairing & Enrollment UI Actions
// ==============================================================================
let currentPairingState = null;
let pairingTimerInterval = null;

function openSkyShieldPairingModal() {
  const modal = document.getElementById("skyshieldPairingModal");
  if (modal) {
    modal.style.display = "flex";
    const s1 = document.getElementById("skyshieldPairStep1");
    const s2 = document.getElementById("skyshieldPairStep2");
    if (s1) s1.style.display = "block";
    if (s2) s2.style.display = "none";
  }
}

function closeSkyShieldPairingModal() {
  const modal = document.getElementById("skyshieldPairingModal");
  if (modal) modal.style.display = "none";
  if (pairingTimerInterval) {
    clearInterval(pairingTimerInterval);
    pairingTimerInterval = null;
  }
  currentPairingState = null;
}

async function submitSkyShieldPairRequest() {
  const devName = document.getElementById("pairDeviceName") ? document.getElementById("pairDeviceName").value.trim() : "Unknown Device";
  const platform = document.getElementById("pairPlatform") ? document.getElementById("pairPlatform").value : "android";
  const phone = document.getElementById("pairPhoneNumber") ? document.getElementById("pairPhoneNumber").value.trim() : "";

  const caps = ["telemetry:read"];
  if (document.getElementById("capConfig")?.checked) caps.push("config:audit");
  if (document.getElementById("capHardware")?.checked) caps.push("hardware:status");
  if (document.getElementById("capNetwork")?.checked) caps.push("network:diagnostics");
  if (document.getElementById("capPerms")?.checked) caps.push("permission:monitor");

  const btn = document.getElementById("btnCreatePairRequest");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "<span>⏳</span> <span>Generating...</span>";
  }

  try {
    const res = await fetch("/api/skyshield/pair/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        device_name: devName,
        platform: platform,
        phone_number: phone || null,
        requested_capabilities: caps,
        ttl_seconds: 600,
        requester_id: "SkyShield Operator UI",
      }),
    });

    const data = await res.json();
    if (res.ok && data.success && data.pairing_request) {
      currentPairingState = data.pairing_request;
      const s1 = document.getElementById("skyshieldPairStep1");
      const s2 = document.getElementById("skyshieldPairStep2");
      if (s1) s1.style.display = "none";
      if (s2) s2.style.display = "block";
      const codeEl = document.getElementById("skyshieldDisplayCode");
      const pairIdEl = document.getElementById("skyshieldDisplayPairId");
      const targetDevEl = document.getElementById("skyshieldDisplayTargetDevice");
      const targetPhoneEl = document.getElementById("skyshieldDisplayTargetPhone");
      if (codeEl) codeEl.textContent = data.pairing_request.pairing_code;
      if (pairIdEl) pairIdEl.textContent = data.pairing_request.pairing_id;
      if (targetDevEl) targetDevEl.textContent = data.pairing_request.device_name;
      if (targetPhoneEl) targetPhoneEl.textContent = data.pairing_request.phone_number_masked || "None (Direct Pairing)";

      let timeLeft = 600;
      const timerEl = document.getElementById("skyshieldCodeTimer");
      if (pairingTimerInterval) clearInterval(pairingTimerInterval);
      pairingTimerInterval = setInterval(() => {
        timeLeft--;
        if (timeLeft <= 0) {
          clearInterval(pairingTimerInterval);
          if (timerEl) timerEl.textContent = "EXPIRED";
        } else if (timerEl) {
          timerEl.textContent = `Expires in ${timeLeft}s`;
        }
      }, 1000);

      renderSkyShieldDashboard();
    } else {
      alert(`Pairing request failed: ${data.message || data.error || "Unknown error"}`);
    }
  } catch (err) {
    console.warn("Pairing request failed:", err);
    alert(`Pairing request error: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = "<span>🚀</span> <span>Initiate Pairing Request</span>";
    }
  }
}

async function submitSkyShieldPairApproval() {
  if (!currentPairingState) return;
  const btn = document.getElementById("btnApprovePair");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "<span>⏳</span> <span>Enrolling...</span>";
  }

  try {
    const res = await fetch("/api/skyshield/pair/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pairing_id: currentPairingState.pairing_id,
        pairing_code: currentPairingState.pairing_code,
        approver_actor: "Device Owner (Verified UI)",
      }),
    });
    const data = await res.json();
    if (res.ok && data.success) {
      closeSkyShieldPairingModal();
      renderSkyShieldDashboard();
    } else {
      alert(`Approval failed: ${data.message || data.error}`);
    }
  } catch (err) {
    console.warn("Approval error:", err);
    alert(`Approval error: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = "<span>✅</span> <span>Owner Approve Enrollment</span>";
    }
  }
}

async function submitSkyShieldPairRejection() {
  if (!currentPairingState) return;
  const btn = document.getElementById("btnRejectPair");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "<span>⏳</span> <span>Rejecting...</span>";
  }

  try {
    const res = await fetch("/api/skyshield/pair/reject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pairing_id: currentPairingState.pairing_id,
        reason: "Owner explicit rejection via UI",
        rejector_actor: "Device Owner",
      }),
    });
    closeSkyShieldPairingModal();
    renderSkyShieldDashboard();
  } catch (err) {
    console.warn("Rejection error:", err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = "<span>❌</span> <span>Reject Pairing</span>";
    }
  }
}

async function suspendSkyShieldDevice(deviceId) {
  if (!confirm(`Suspend device '${deviceId}'? Active sessions will be temporarily disabled.`)) return;
  try {
    const res = await fetch(`/api/skyshield/devices/${deviceId}/suspend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "Suspended by operator via Command Center" }),
    });
    if (res.ok) renderSkyShieldDashboard();
  } catch (err) {
    console.warn("Device suspend failed:", err);
  }
}

async function revokeSkyShieldDevice(deviceId) {
  if (!confirm(`Permanently revoke device '${deviceId}'? This action terminates all sessions and cryptographic keys.`)) return;
  try {
    const res = await fetch(`/api/skyshield/devices/${deviceId}/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "Permanently revoked by operator via Command Center" }),
    });
    if (res.ok) renderSkyShieldDashboard();
  } catch (err) {
    console.warn("Device revoke failed:", err);
  }
}

async function reauthorizeSkyShieldDevice(deviceId) {
  try {
    const res = await fetch(`/api/skyshield/devices/${deviceId}/reauthorize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "Reauthorized by operator via Command Center" }),
    });
    if (res.ok) renderSkyShieldDashboard();
  } catch (err) {
    console.warn("Device reauthorize failed:", err);
  }
}

// Phase 3 Device Health, Telemetry & Anomaly Handlers
async function triggerDeviceHealthAnalysis(deviceId) {
  const targetId = deviceId || "dev_mock_vivo_v2334";
  const statusEl = document.getElementById("safeResponseStatus");
  if (statusEl) statusEl.textContent = "Analyzing telemetry...";
  try {
    const res = await fetch(`/api/skyshield/devices/${targetId}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mock_scenario: "NORMAL_DEVICE" }),
    });
    if (res.ok) {
      if (statusEl) statusEl.textContent = "Telemetry analyzed";
      renderSkyShieldDashboard();
    }
  } catch (err) {
    console.warn("Telemetry analysis failed:", err);
    if (statusEl) statusEl.textContent = "Analysis failed";
  }
}

async function simulateMockAnomaly(scenario) {
  const targetId = "dev_mock_vivo_v2334";
  const statusEl = document.getElementById("safeResponseStatus");
  if (statusEl) statusEl.textContent = `Injecting ${scenario}...`;
  try {
    const res = await fetch(`/api/skyshield/devices/${targetId}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mock_scenario: scenario }),
    });
    if (res.ok) {
      if (statusEl) statusEl.textContent = `Scenario '${scenario}' active`;
      renderSkyShieldDashboard();
    }
  } catch (err) {
    console.warn("Anomaly injection failed:", err);
    if (statusEl) statusEl.textContent = "Injection failed";
  }
}

async function resetDeviceBaseline(deviceId) {
  const targetId = deviceId || "dev_mock_vivo_v2334";
  const statusEl = document.getElementById("safeResponseStatus");
  if (statusEl) statusEl.textContent = "Resetting baseline...";
  try {
    const res = await fetch(`/api/skyshield/devices/${targetId}/baseline/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (res.ok) {
      if (statusEl) statusEl.textContent = "Baseline reset complete";
      renderSkyShieldDashboard();
    }
  } catch (err) {
    console.warn("Baseline reset failed:", err);
    if (statusEl) statusEl.textContent = "Reset failed";
  }
}

async function triggerDeviceResponse(action) {
  const statusEl = document.getElementById("safeResponseStatus");
  if (action === "reauth") {
    if (statusEl) statusEl.textContent = "Re-authenticating session...";
    await fetch("/api/skyshield/session/authenticate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: "dev_mock_vivo_v2334" }),
    });
    if (statusEl) statusEl.textContent = "Session re-authenticated";
    renderSkyShieldDashboard();
  } else if (action === "permissions") {
    sendSkyShieldCommand("Audit high risk permissions");
  } else if (action === "reconnect") {
    if (statusEl) statusEl.textContent = "Reconnecting device...";
    await triggerDeviceHealthAnalysis("dev_mock_vivo_v2334");
    if (statusEl) statusEl.textContent = "Device reconnected";
  }
}





// ==============================================================================
// Phase 4 SkyShield Intelligence & Incident Center Renderers
// ==============================================================================

let currentSelectedIncidentId = null;

function renderSkyShieldPhase4(data) {
  // 15. Security Overview KPIs
  const ov = data.security_overview;
  if (ov) {
    const actIncEl = document.getElementById("secOverviewActiveIncidents");
    if (actIncEl) actIncEl.textContent = ov.active_incidents_count || 0;

    const critAltEl = document.getElementById("secOverviewCritAlerts");
    if (critAltEl) critAltEl.textContent = ov.critical_alerts_count || 0;

    const highAltEl = document.getElementById("secOverviewHighAlerts");
    if (highAltEl) highAltEl.textContent = ov.high_alerts_count || 0;

    const riskDevEl = document.getElementById("secOverviewRiskDevices");
    if (riskDevEl) riskDevEl.textContent = ov.devices_at_risk_count || 0;

    const avgPostEl = document.getElementById("secOverviewAvgPosture");
    if (avgPostEl) {
      avgPostEl.textContent = ov.overall_security_posture ? `${ov.overall_security_posture}` : "100";
      avgPostEl.className = `kpi-value ${ov.overall_security_posture >= 80 ? 'green' : (ov.overall_security_posture >= 50 ? 'warn' : 'crit')}`;
    }
  }

  // Alerts feed
  const alertsList = document.getElementById("secOverviewAlertsList");
  if (alertsList && data.alerts) {
    if (data.alerts.length === 0) {
      alertsList.innerHTML = '<div class="skyshield-empty-state" style="padding: 6px;">No critical alerts active.</div>';
    } else {
      let aHtml = "";
      data.alerts.slice(0, 5).forEach(alt => {
        const sevClass = (alt.severity || "MEDIUM").toLowerCase();
        aHtml += `
          <div class="alert-chip-item ${sevClass}">
            <div>
              <strong>[${escapeHtml(alt.severity)}]</strong> ${escapeHtml(alt.title)}
              <span style="color: #64748b; font-size: 10px;">(${alt.count > 1 ? alt.count + 'x occurrences' : '1x'})</span>
            </div>
            <span style="font-size: 9.5px; color: #94a3b8;">${escapeHtml(alt.timestamp_iso ? alt.timestamp_iso.split(" ")[1] : "")}</span>
          </div>
        `;
      });
      alertsList.innerHTML = aHtml;
    }
  }

  // 16. Incident Center
  const incBody = document.getElementById("cardIncidentBody");
  const incCount = document.getElementById("cardIncidentCount");
  if (incBody && data.incidents) {
    if (incCount) incCount.textContent = `${data.incidents.length} INCIDENTS`;
    if (data.incidents.length === 0) {
      incBody.innerHTML = '<div class="skyshield-empty-state">No security incidents detected. System operating normally.</div>';
    } else {
      let iHtml = "";
      data.incidents.forEach(inc => {
        const sevClass = (inc.severity || "MEDIUM").toLowerCase();
        iHtml += `
          <div class="incident-entry-card">
            <div class="incident-entry-top">
              <span class="incident-title-text">${escapeHtml(inc.title)}</span>
              <span class="anomaly-severity-badge ${sevClass}">${escapeHtml(inc.severity)}</span>
            </div>
            <div class="incident-meta-row">
              <span><strong>ID:</strong> <code>${escapeHtml(inc.incident_id)}</code></span>
              <span><strong>Device:</strong> ${escapeHtml(inc.device_id)}</span>
              <span><strong>Status:</strong> <span style="color: #38bdf8; font-weight: 600;">${escapeHtml(inc.status)}</span></span>
              <span><strong>Verification:</strong> ${escapeHtml(inc.verification_state)}</span>
            </div>
            <div style="font-size: 11px; color: #cbd5e1; margin-bottom: 6px;">
              ${escapeHtml(inc.description)}
            </div>
            <div class="incident-actions-row">
              <button class="device-action-btn reauthorize" style="padding: 3px 8px; font-size: 10.5px;" onclick="viewIncidentDetail('${inc.incident_id}')">🔍 Details</button>
              <button class="device-action-btn suspend" style="padding: 3px 8px; font-size: 10.5px;" onclick="promptIncidentFalsePositive('${inc.incident_id}')">False Positive</button>
              <button class="device-action-btn" style="padding: 3px 8px; font-size: 10.5px; background: rgba(16, 185, 129, 0.2); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.4);" onclick="resolveIncidentDirect('${inc.incident_id}')">✓ Resolve</button>
            </div>
          </div>
        `;
      });
      incBody.innerHTML = iHtml;
    }
  }

  // 17. Threat Intelligence & CVEs
  const threatBody = document.getElementById("cardThreatsBody");
  if (threatBody && data.threat_advisories) {
    if (data.threat_advisories.length === 0) {
      threatBody.innerHTML = '<div class="skyshield-empty-state">No public threat advisories cached.</div>';
    } else {
      let tHtml = "";
      data.threat_advisories.slice(0, 4).forEach(adv => {
        tHtml += `
          <div class="threat-advisory-item ${adv.severity}">
            <div class="threat-advisory-top">
              <span class="threat-cve-id">${escapeHtml(adv.advisory_id)}</span>
              <span class="anomaly-severity-badge ${adv.severity.toLowerCase()}">${escapeHtml(adv.severity)}</span>
            </div>
            <div style="font-weight: 600; color: #f1f5f9; margin-bottom: 2px;">${escapeHtml(adv.title)}</div>
            <div style="font-size: 10px; color: #94a3b8; margin-bottom: 4px;">
              <strong>Affected:</strong> ${escapeHtml((adv.affected_components || []).join(", "))} | <strong>Source:</strong> ${escapeHtml(adv.source)}
            </div>
            <div style="font-size: 10.5px; color: #cbd5e1;">${escapeHtml(adv.summary)}</div>
          </div>
        `;
      });
      threatBody.innerHTML = tHtml;
    }
  }
}

async function viewIncidentDetail(incidentId) {
  currentSelectedIncidentId = incidentId;
  const modal = document.getElementById("skyshieldIncidentModal");
  const modalBody = document.getElementById("incModalBody");
  if (modal) modal.style.display = "flex";
  if (modalBody) modalBody.innerHTML = '<div class="skyshield-empty-state">Loading incident details...</div>';

  try {
    const res = await fetch(`/api/skyshield/incidents/${incidentId}`);
    if (!res.ok) {
      modalBody.innerHTML = `<div class="skyshield-empty-state">Failed to load incident: ${res.statusText}</div>`;
      return;
    }
    const data = await res.json();
    const inc = data.incident;
    if (!inc) return;

    let evHtml = "";
    (inc.evidence || []).forEach(ev => {
      evHtml += `<li><strong>[${escapeHtml(ev.verification_state)}] ${escapeHtml(ev.evidence_type)}:</strong> ${escapeHtml(ev.description)} <span style="color: #64748b;">(${escapeHtml(ev.source)})</span></li>`;
    });

    let actHtml = "";
    (inc.recommended_actions || []).forEach(act => {
      actHtml += `<button class="skyshield-btn secondary" style="font-size: 11px; padding: 4px 10px;" onclick="proposeAndConfirmAction('${act}', '${inc.device_id}', '${inc.incident_id}')">⚡ Execute ${escapeHtml(act)}</button>`;
    });

    modalBody.innerHTML = `
      <div style="display: flex; flex-direction: column; gap: 12px;">
        <div style="background: rgba(15, 23, 42, 0.5); padding: 10px; border-radius: 6px; border: 1px solid rgba(51, 65, 85, 0.4);">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
            <h3 style="margin: 0; font-size: 14px; color: #38bdf8;">${escapeHtml(inc.title)}</h3>
            <span class="anomaly-severity-badge ${inc.severity.toLowerCase()}">${escapeHtml(inc.severity)}</span>
          </div>
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 6px; font-size: 11px; color: #94a3b8;">
            <div><strong>Incident ID:</strong> <code>${escapeHtml(inc.incident_id)}</code></div>
            <div><strong>Device ID:</strong> <code>${escapeHtml(inc.device_id)}</code></div>
            <div><strong>Status:</strong> ${escapeHtml(inc.status)}</div>
            <div><strong>Confidence:</strong> ${(inc.confidence * 100).toFixed(1)}%</div>
            <div><strong>Verification:</strong> ${escapeHtml(inc.verification_state)}</div>
            <div><strong>Created:</strong> ${escapeHtml(inc.created_at_iso)}</div>
          </div>
          <p style="margin-top: 8px; font-size: 11.5px; color: #cbd5e1;">${escapeHtml(inc.description)}</p>
        </div>

        <div>
          <h4 style="margin: 0 0 6px 0; font-size: 12px; color: #94a3b8; text-transform: uppercase;">Documented Evidence (${inc.evidence_count || 0}):</h4>
          <ul style="margin: 0; padding-left: 20px; font-size: 11px; color: #cbd5e1;">
            ${evHtml || '<li>No individual evidence records linked.</li>'}
          </ul>
        </div>

        <div>
          <h4 style="margin: 0 0 6px 0; font-size: 12px; color: #94a3b8; text-transform: uppercase;">AI Security Analyst Interpretation:</h4>
          <div style="background: rgba(30, 41, 59, 0.5); padding: 8px 12px; border-radius: 6px; font-size: 11.5px; color: #f1f5f9; border-left: 3px solid #38bdf8;">
            ${inc.ai_analysis ? escapeHtml(inc.ai_analysis.interpretation) : 'Advisory analysis available under operator review.'}
            <div style="font-size: 10px; color: #64748b; margin-top: 4px; font-style: italic;">
              ⚠️ AI analysis is advisory only. Does not authorize actions or prove compromise without verified evidence.
            </div>
          </div>
        </div>

        <div>
          <h4 style="margin: 0 0 6px 0; font-size: 12px; color: #94a3b8; text-transform: uppercase;">Recommended Defensive Actions:</h4>
          <div style="display: flex; gap: 8px; flex-wrap: wrap;">
            ${actHtml || '<span style="font-size: 11px; color: #64748b;">No immediate actions recommended.</span>'}
          </div>
        </div>
      </div>
    `;
  } catch (err) {
    modalBody.innerHTML = `<div class="skyshield-empty-state">Error loading incident details: ${err.message}</div>`;
  }
}

function closeSkyShieldIncidentModal() {
  const modal = document.getElementById("skyshieldIncidentModal");
  if (modal) modal.style.display = "none";
  currentSelectedIncidentId = null;
}

async function promptIncidentFalsePositive(incidentId) {
  const targetId = incidentId || currentSelectedIncidentId;
  if (!targetId) return;
  const reason = prompt("Enter rationale for marking this incident as a False Positive:", "Benign developer or baseline test activity");
  if (!reason) return;

  try {
    const res = await fetch(`/api/skyshield/incidents/${targetId}/false_positive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason })
    });
    const d = await res.json();
    alert(d.message || (d.success ? "Marked false positive" : "Error"));
    closeSkyShieldIncidentModal();
    renderSkyShieldDashboard();
  } catch (err) {
    alert("Error: " + err.message);
  }
}

async function resolveIncidentDirect(incidentId) {
  const targetId = incidentId || currentSelectedIncidentId;
  if (!targetId) return;
  const resText = prompt("Enter resolution notes:", "Threat mitigated and baseline verified nominal");
  if (!resText) return;

  try {
    const res = await fetch(`/api/skyshield/incidents/${targetId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resolution: resText })
    });
    const d = await res.json();
    alert(d.message || (d.success ? "Incident resolved" : "Error"));
    closeSkyShieldIncidentModal();
    renderSkyShieldDashboard();
  } catch (err) {
    alert("Error: " + err.message);
  }
}

function resolveCurrentIncident() {
  if (currentSelectedIncidentId) resolveIncidentDirect(currentSelectedIncidentId);
}

async function exportCurrentIncidentReport() {
  if (!currentSelectedIncidentId) return;
  try {
    const res = await fetch(`/api/skyshield/incidents/${currentSelectedIncidentId}/report`);
    const d = await res.json();
    if (d.success && d.markdown_report) {
      const blob = new Blob([d.markdown_report], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Incident_Report_${currentSelectedIncidentId}.md`;
      a.click();
      URL.revokeObjectURL(url);
    } else {
      alert("Failed to export report: " + (d.error || "Unknown error"));
    }
  } catch (err) {
    alert("Export error: " + err.message);
  }
}

async function proposeAndConfirmAction(action, deviceId, incidentId) {
  const confirmMsg = `Are you sure you want to execute high-impact action: '${action}' on device '${deviceId}'?`;
  if (!confirm(confirmMsg)) return;

  try {
    // 1. Propose
    const pRes = await fetch("/api/skyshield/response/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, device_id: deviceId, incident_id: incidentId, reason: "Operator confirmed via UI" })
    });
    const pData = await pRes.json();
    if (!pData.success) {
      alert("Proposal rejected: " + pData.error);
      return;
    }

    // 2. Confirm
    const cRes = await fetch("/api/skyshield/response/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proposal_id: pData.proposal.proposal_id, operator_confirmed: true })
    });
    const cData = await cRes.json();
    alert(cData.message || (cData.success ? "Action executed successfully" : "Execution failed"));
    closeSkyShieldIncidentModal();
    renderSkyShieldDashboard();
  } catch (err) {
    alert("Error executing action: " + err.message);
  }
}

async function checkDeviceVulnerabilities(deviceId) {
  try {
    const res = await fetch(`/api/skyshield/threats/check/${deviceId}`);
    const data = await res.json();
    if (data.success) {
      alert(`Vulnerability Check for ${deviceId}:\nFound ${data.matched_advisories_count} matching advisories.\nHighest Severity: ${data.highest_severity}`);
    } else {
      alert("Check failed: " + (data.error || "Unknown error"));
    }
  } catch (err) {
    alert("Check error: " + err.message);
  }
}


// =============================================================================
// DEDICATED CHAT PANEL & AGENT INFORMATION SYNC
// =============================================================================

// Authoritative catalog for all 17 registered specialist agents + central NR-AI core
const AGENT_CATALOG = {
  "nr_ai_central_intelligence": {
    id: "nr_ai_central_intelligence",
    name: "NR-AI",
    role: "Central Intelligence Core",
    icon: "🌌",
    icon_type: "brain",
    status: "ONLINE",
    status_color: "#38bdf8",
    color: "#38bdf8",
    glow: "rgba(56, 189, 248, 0.6)",
    parent: "Root System",
    children: "17 Specialist Agents",
    project: "NR-AI Ecosystem",
    task: "Orchestrating autonomous specialist network",
    stage: "READY",
    safety: "Autonomous Governance (Central Core)",
    caps: ["orchestration", "voice.continuous", "multi.model", "galaxy.core"]
  },
  "android_unified_agent": {
    id: "android_unified_agent",
    name: "Droid",
    role: "Android Agent",
    icon: "🤖",
    icon_type: "android",
    status: "ONLINE",
    status_color: "#10b981",
    color: "#10b981",
    glow: "rgba(16, 185, 129, 0.6)",
    parent: "NR-AI",
    children: "Droid Scout, Droid Guardian",
    project: "NR-AI (Android Studio)",
    task: "Standing by in Android Studio workspace",
    stage: "READY",
    safety: "ModelIsolationGate (Deterministic)",
    caps: ["studio.workspace", "gradle.build", "adb.deploy", "bounded.repair"]
  },
  "droid_scout": {
    id: "droid_scout",
    name: "Droid Scout",
    role: "Android Studio Watch & Assistant",
    icon: "👁️",
    icon_type: "eye",
    status: "ONLINE",
    status_color: "#10b981",
    color: "#10b981",
    glow: "rgba(16, 185, 129, 0.6)",
    parent: "Droid",
    children: "None",
    project: "Android Studio Workspace",
    task: "Observing Android Studio workspace & AST changes",
    stage: "WATCHING",
    safety: "Deterministic Read-Only",
    caps: ["ast.watch", "logcat.stream", "file.listen", "change.detect"]
  },
  "droid_guardian": {
    id: "droid_guardian",
    name: "Droid Guardian",
    role: "Android Build & Verification Guardian",
    icon: "🛡️",
    icon_type: "shield",
    status: "ONLINE",
    status_color: "#10b981",
    color: "#10b981",
    glow: "rgba(16, 185, 129, 0.6)",
    parent: "Droid",
    children: "None",
    project: "Android Build Pipeline",
    task: "Enforcing Gradle build integrity & pre-execution safety gates",
    stage: "GUARDING",
    safety: "Deterministic Verification Gate",
    caps: ["gradle.verify", "manifest.audit", "safety.barrier", "build.enforce"]
  },
  "unity_autonomous_agent": {
    id: "unity_autonomous_agent",
    name: "Unity",
    role: "Unity Autonomous Agent",
    icon: "🎮",
    icon_type: "gamepad",
    status: "ONLINE",
    status_color: "#a855f7",
    color: "#a855f7",
    glow: "rgba(168, 85, 247, 0.6)",
    parent: "NR-AI",
    children: "None",
    project: "Unity Engine",
    task: "Standing by in Unity 2022.3 workspace",
    stage: "READY",
    safety: "ModelIsolationGate (Deterministic)",
    caps: ["unity.editor", "csharp.ast", "scene.management", "editmode.tests"]
  },
  "unreal_autonomous_agent": {
    id: "unreal_autonomous_agent",
    name: "Unreal",
    role: "Unreal Autonomous Agent",
    icon: "⚡",
    icon_type: "zap",
    status: "ONLINE",
    status_color: "#06b6d4",
    color: "#06b6d4",
    glow: "rgba(6, 182, 212, 0.6)",
    parent: "NR-AI",
    children: "None",
    project: "Unreal Engine 5",
    task: "Standing by in Unreal workspace",
    stage: "READY",
    safety: "ModelIsolationGate (Deterministic)",
    caps: ["ue5.blueprints", "cpp.ast", "render.pipeline", "asset.audit"]
  },
  "vs_unified_agent": {
    id: "vs_unified_agent",
    name: "Studio",
    role: "Visual Studio C++ Agent",
    icon: "💻",
    icon_type: "code",
    status: "ONLINE",
    status_color: "#6366f1",
    color: "#6366f1",
    glow: "rgba(99, 102, 241, 0.6)",
    parent: "NR-AI",
    children: "None",
    project: "Visual Studio C++",
    task: "Standing by in MSBuild C++ solution workspace",
    stage: "READY",
    safety: "ModelIsolationGate (Deterministic)",
    caps: ["msbuild.compile", "cpp.diagnostic", "vcpkg.deps", "native.debug"]
  },
  "universal_knowledge_engine": {
    id: "universal_knowledge_engine",
    name: "Knowledge",
    role: "Universal Knowledge Engine",
    icon: "📚",
    icon_type: "book",
    status: "ONLINE",
    status_color: "#f59e0b",
    color: "#f59e0b",
    glow: "rgba(245, 158, 11, 0.6)",
    parent: "NR-AI",
    children: "Nova, Aegis",
    project: "Knowledge Trinity",
    task: "Synthesizing verified knowledge & multi-hop continuum",
    stage: "READY",
    safety: "Epistemic Verification (Ground Truth)",
    caps: ["deep.research", "fact.verification", "multi.hop", "evidence.graph"]
  },
  "nova_discovery_agent": {
    id: "nova_discovery_agent",
    name: "Nova",
    role: "Knowledge Discovery Mode",
    icon: "✨",
    icon_type: "sparkles",
    status: "ONLINE",
    status_color: "#38bdf8",
    color: "#38bdf8",
    glow: "rgba(56, 189, 248, 0.6)",
    parent: "Knowledge",
    children: "None",
    project: "Knowledge Trinity (Epistemic Core)",
    task: "Scouting deep web sources & live developments",
    stage: "READY",
    safety: "Epistemic Verification",
    caps: ["source.scout", "web.discovery", "live.feeds", "ingestion"]
  },
  "aegis_verification_agent": {
    id: "aegis_verification_agent",
    name: "Aegis",
    role: "Knowledge Verification Mode",
    icon: "⚖️",
    icon_type: "scale",
    status: "ONLINE",
    status_color: "#ec4899",
    color: "#ec4899",
    glow: "rgba(236, 72, 153, 0.6)",
    parent: "Knowledge",
    children: "None",
    project: "Knowledge Trinity (Epistemic Core)",
    task: "Auditing factual claims & enforcing ground truth",
    stage: "READY",
    safety: "Ground Truth Gate",
    caps: ["fact.audit", "citation.verify", "contradiction.check", "truth.gate"]
  },
  "security_agent": {
    id: "security_agent",
    name: "SkyShield",
    role: "Security Command Center Agent",
    icon: "🛡️",
    icon_type: "shield-alert",
    status: "ONLINE",
    status_color: "#ef4444",
    color: "#ef4444",
    glow: "rgba(239, 68, 68, 0.6)",
    parent: "NR-AI",
    children: "None",
    project: "SkyShield Core",
    task: "Monitoring threat feeds, isolation boundaries & audit telemetry",
    stage: "READY",
    safety: "Enforced Isolation & Zero-Trust",
    caps: ["threat.monitor", "firewall.gate", "crypto.audit", "access.control"]
  },
  "computer_control_agent": {
    id: "computer_control_agent",
    name: "Sentinel",
    role: "Computer & OS Control Agent",
    icon: "🖥️",
    icon_type: "monitor",
    status: "ONLINE",
    status_color: "#8b5cf6",
    color: "#8b5cf6",
    glow: "rgba(139, 92, 246, 0.6)",
    parent: "NR-AI",
    children: "None",
    project: "OS Automation",
    task: "Standing by for OS automation and desktop control",
    stage: "READY",
    safety: "ModelIsolationGate (Deterministic)",
    caps: ["mouse.click", "keyboard.type", "window.mgmt", "process.supervise"]
  },
  "nexus_coordinator": {
    id: "nexus_coordinator",
    name: "Nexus",
    role: "Multi-Agent Routing Coordinator",
    icon: "🔗",
    icon_type: "network",
    status: "ONLINE",
    status_color: "#14b8a6",
    color: "#14b8a6",
    glow: "rgba(20, 184, 166, 0.6)",
    parent: "NR-AI",
    children: "None",
    project: "Agent Handoff Matrix",
    task: "Routing intents across the 18 specialist agents",
    stage: "READY",
    safety: "Multi-Model Consensus",
    caps: ["intent.routing", "handoff.manage", "consensus.vote", "context.preserve"]
  },
  "research_agent": {
    id: "research_agent",
    name: "Quest",
    role: "Deep Scientific Research Agent",
    icon: "🔬",
    icon_type: "flask",
    status: "ONLINE",
    status_color: "#0ea5e9",
    color: "#0ea5e9",
    glow: "rgba(14, 165, 233, 0.6)",
    parent: "NR-AI",
    children: "None",
    project: "Scientific Literature",
    task: "Standing by for arXiv, PubMed, and patent research",
    stage: "READY",
    safety: "Epistemic Verification",
    caps: ["arxiv.query", "pubmed.search", "patent.search", "paper.summarize"]
  },
  "vision_agent": {
    id: "vision_agent",
    name: "Vision",
    role: "Vision & Multimodal Agent",
    icon: "👁️",
    icon_type: "eye",
    status: "ONLINE",
    status_color: "#f97316",
    color: "#f97316",
    glow: "rgba(249, 115, 22, 0.6)",
    parent: "NR-AI",
    children: "None",
    project: "Multimodal Vision",
    task: "Standing by for screenshot inspection and OCR analysis",
    stage: "READY",
    safety: "ModelIsolationGate",
    caps: ["screen.capture", "ocr.extract", "visual.reasoning", "ui.detect"]
  },
  "forge_dev_agent": {
    id: "forge_dev_agent",
    name: "Forge",
    role: "Rapid Prototyping & Code Dev Agent",
    icon: "🔨",
    icon_type: "tool",
    status: "ONLINE",
    status_color: "#eab308",
    color: "#eab308",
    glow: "rgba(234, 179, 8, 0.6)",
    parent: "NR-AI",
    children: "None",
    project: "Scaffold Engineering",
    task: "Standing by for code generation and project scaffolding",
    stage: "READY",
    safety: "Deterministic Verification",
    caps: ["code.scaffold", "ast.transform", "syntax.check", "test.generate"]
  },
  "pixel_ui_agent": {
    id: "pixel_ui_agent",
    name: "Pixel",
    role: "UI/UX Design & Frontend Agent",
    icon: "🎨",
    icon_type: "palette",
    status: "ONLINE",
    status_color: "#d946ef",
    color: "#d946ef",
    glow: "rgba(217, 70, 239, 0.6)",
    parent: "NR-AI",
    children: "None",
    project: "Design System",
    task: "Standing by for CSS, DOM animations, and layout generation",
    stage: "READY",
    safety: "Visual Integrity Gate",
    caps: ["css.layout", "component.style", "a11y.audit", "motion.design"]
  }
};
window.AGENT_CATALOG = AGENT_CATALOG;

function resolveAgentCatalog(query) {
  if (!query) return AGENT_CATALOG["nr_ai_central_intelligence"];
  if (typeof query === "object") {
    if (query.id && AGENT_CATALOG[query.id]) return AGENT_CATALOG[query.id];
    if (query.agent_id && AGENT_CATALOG[query.agent_id]) return AGENT_CATALOG[query.agent_id];
    if (query.name) query = query.name;
    else if (query.friendly_name) query = query.friendly_name;
    else if (query.agent_id) query = query.agent_id;
  }

  const q = String(query).toLowerCase().replace(/[.!?]+$/, "").trim();

  // 1. Direct key match
  if (AGENT_CATALOG[q]) return AGENT_CATALOG[q];

  // 2. Central NR-AI
  if (q === "nr-ai" || q === "nrai" || q === "nr ai" || q === "central" || q === "root") {
    return AGENT_CATALOG["nr_ai_central_intelligence"];
  }

  // 3. Aliases
  const aliasMap = {
    "droid": "android_unified_agent",
    "android": "android_unified_agent",
    "android agent": "android_unified_agent",
    "android studio": "android_unified_agent",
    "scout": "droid_scout",
    "droid scout": "droid_scout",
    "guardian": "droid_guardian",
    "droid guardian": "droid_guardian",
    "unity": "unity_autonomous_agent",
    "unreal": "unreal_autonomous_agent",
    "studio": "vs_unified_agent",
    "visual studio": "vs_unified_agent",
    "vs": "vs_unified_agent",
    "knowledge": "universal_knowledge_engine",
    "oracle": "universal_knowledge_engine",
    "nova": "nova_discovery_agent",
    "aegis": "aegis_verification_agent",
    "skyshield": "security_agent",
    "shield": "security_agent",
    "security": "security_agent",
    "sentinel": "computer_control_agent",
    "computer": "computer_control_agent",
    "nexus": "nexus_coordinator",
    "coordinator": "nexus_coordinator",
    "quest": "research_agent",
    "research": "research_agent",
    "vision": "vision_agent",
    "forge": "forge_dev_agent",
    "pixel": "pixel_ui_agent"
  };

  if (aliasMap[q] && AGENT_CATALOG[aliasMap[q]]) {
    return AGENT_CATALOG[aliasMap[q]];
  }

  // 4. Case-insensitive search by id or name
  for (const agent of Object.values(AGENT_CATALOG)) {
    if (agent.id.toLowerCase() === q || agent.name.toLowerCase() === q) {
      return agent;
    }
  }

  // 5. Check live galaxy nodes if dynamically registered
  if (state.galaxy && state.galaxy.nodes) {
    const found = state.galaxy.nodes.find(n => (n.agent_id && n.agent_id.toLowerCase() === q) || (n.friendly_name && n.friendly_name.toLowerCase() === q));
    if (found) {
      return {
        id: found.agent_id,
        name: found.friendly_name || found.agent_id,
        role: found.role || "Specialist Agent",
        icon: getIconGlyph(found.icon_type) || "🤖",
        icon_type: found.icon_type || "robot",
        status: found.status || "ONLINE",
        status_color: found.status_color || "#10b981",
        color: found.color || "#10b981",
        glow: found.glow || "rgba(16, 185, 129, 0.6)",
        parent: found.parent_agent || "NR-AI",
        children: found.child_agents ? found.child_agents.join(", ") : "None",
        project: found.project_name || "Specialist Workspace",
        task: (found.current_task && (found.current_task.description || found.current_task.task_name)) || "Standing by",
        stage: "READY",
        safety: "ModelIsolationGate (Deterministic)",
        caps: ["agent.task", "agent.execute"]
      };
    }
  }

  return null;
}
window.resolveAgentCatalog = resolveAgentCatalog;

function updateAgentInfoCard(agent) {
  if (!agent) return;
  const avatar = document.getElementById("infoAgentAvatar");
  if (avatar) {
    avatar.textContent = agent.icon;
    avatar.style.borderColor = agent.color || "#10b981";
    avatar.style.boxShadow = `0 0 12px ${agent.glow || "rgba(16, 185, 129, 0.4)"}`;
  }

  const nameElem = document.getElementById("infoAgentName");
  if (nameElem) nameElem.textContent = agent.name;

  const roleElem = document.getElementById("infoAgentRole");
  if (roleElem) roleElem.textContent = agent.role;

  const statusElem = document.getElementById("infoAgentStatus");
  if (statusElem) {
    statusElem.textContent = `● ${agent.status || "ONLINE"}`;
    statusElem.style.color = agent.status_color || "#10b981";
    statusElem.style.borderColor = agent.status_color || "#10b981";
    statusElem.style.background = `${agent.status_color || "#10b981"}18`;
  }

  const parentElem = document.getElementById("infoParentAgent");
  if (parentElem) parentElem.textContent = agent.parent || "NR-AI";

  const childElem = document.getElementById("infoChildAgents");
  if (childElem) childElem.textContent = agent.children || "None";

  const projElem = document.getElementById("infoActiveProject");
  if (projElem) projElem.textContent = agent.project || "NR-AI Ecosystem";

  const taskElem = document.getElementById("infoCurrentTask");
  if (taskElem) taskElem.textContent = agent.task || "Standing by in workspace";

  const stageElem = document.getElementById("infoCurrentStage");
  if (stageElem) stageElem.textContent = agent.stage || "READY";

  const safetyElem = document.getElementById("infoSafetyState");
  if (safetyElem) safetyElem.textContent = agent.safety || "ModelIsolationGate (Deterministic)";

  const capsElem = document.getElementById("infoCapabilitiesPills");
  if (capsElem && agent.caps && Array.isArray(agent.caps)) {
    capsElem.innerHTML = agent.caps.map(c => `<span class="cap-pill">${c}</span>`).join("");
  }
}
window.updateAgentInfoCard = updateAgentInfoCard;

function updateChatHeader(agent) {
  if (!agent) return;
  const chatTitle = document.getElementById("dedicatedChatTitle");
  if (chatTitle) {
    const rolePart = agent.role ? ` (${agent.role})` : "";
    chatTitle.textContent = `ACTIVE CHAT: ${agent.name}${rolePart}`;
  }

  const chatSub = document.getElementById("dedicatedChatSub");
  if (chatSub) {
    const roleUpper = agent.role ? agent.role.toUpperCase() : "SPECIALIST AGENT";
    chatSub.textContent = `● ${agent.status || "ONLINE"} • ${roleUpper}`;
  }

  const chatAvatar = document.getElementById("dedicatedChatAvatar");
  if (chatAvatar) chatAvatar.textContent = agent.icon || "🌌";

  const chatInput = document.getElementById("dedicatedChatInput");
  if (chatInput) {
    chatInput.placeholder = agent.name === "NR-AI" 
      ? "Talk to NR-AI Central Intelligence (or call 'Droid', 'Unity', 'Unreal')..."
      : `Talk to ${agent.name}...`;
  }
}
window.updateChatHeader = updateChatHeader;

function renderChatMessageElement(msg) {
  const container = document.getElementById("dedicatedChatHistory");
  if (!container) return;

  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${msg.role || "agent"}`;
  if (msg.message_id) bubble.dataset.messageId = msg.message_id;

  const authorSpan = document.createElement("span");
  authorSpan.className = "bubble-author";
  authorSpan.textContent = msg.author || (msg.role === "user" ? "USER" : (msg.agent_id ? msg.agent_id.toUpperCase() : "AGENT"));

  const textNode = document.createElement("div");
  textNode.className = "bubble-text";
  const safeText = String(msg.content || msg.text || "");
  textNode.innerHTML = safeText.replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br/>");

  bubble.appendChild(authorSpan);
  bubble.appendChild(textNode);

  if (msg.timestamp) {
    const timeSpan = document.createElement("span");
    timeSpan.className = "bubble-timestamp";
    timeSpan.style.cssText = "font-size: 0.68rem; opacity: 0.5; margin-top: 4px; display: block;";
    try {
      timeSpan.textContent = new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
      timeSpan.textContent = "";
    }
    bubble.appendChild(timeSpan);
  }

  container.appendChild(bubble);
}
window.renderChatMessageElement = renderChatMessageElement;

async function renderActiveWorkspaceChat(targetAgentId) {
  const canonId = canonicalizeAgentId(targetAgentId || getActiveCanonicalAgentId());
  const container = document.getElementById("dedicatedChatHistory");
  if (!container) return;

  container.innerHTML = "";

  if (!state.conversationStore[canonId]) {
    state.conversationStore[canonId] = [];
    try {
      const res = await fetch(`/api/agent/${canonId}/chat`);
      if (res.ok) {
        const data = await res.json();
        if (data.history && Array.isArray(data.history)) {
          state.conversationStore[canonId] = data.history.map(m => ({
            message_id: m.message_id || `msg_${Date.now()}_${Math.random().toString(36).substr(2, 5)}`,
            agent_id: canonId,
            role: m.role || "agent",
            author: m.role === "user" ? "USER" : (m.author || (canonId === "nr_ai" ? "NR-AI" : canonId.toUpperCase())),
            content: m.content || m.text || "",
            text: m.content || m.text || "",
            timestamp: m.timestamp || new Date().toISOString()
          }));
        }
      }
    } catch (e) {
      console.warn(`[Workspace Chat] Failed to fetch chat history for ${canonId}:`, e);
    }
  }

  const msgs = state.conversationStore[canonId] || [];
  msgs.forEach(msg => {
    renderChatMessageElement(msg);
  });

  if (state.workspaceScrollPositions[canonId] !== undefined) {
    container.scrollTop = state.workspaceScrollPositions[canonId];
  } else {
    container.scrollTop = container.scrollHeight;
  }
}
window.renderActiveWorkspaceChat = renderActiveWorkspaceChat;

function syncActiveAgent(agentIdOrName) {
  const outgoingId = getActiveCanonicalAgentId();
  const container = document.getElementById("dedicatedChatHistory");
  if (container && outgoingId) {
    state.workspaceScrollPositions[outgoingId] = container.scrollTop;
  }

  const isCentral = !agentIdOrName 
    || agentIdOrName === "NR-AI" 
    || agentIdOrName === "nr_ai_central_intelligence" 
    || agentIdOrName === "central"
    || agentIdOrName === "nr_ai"
    || (typeof agentIdOrName === "object" && (agentIdOrName.agent_id === "nr_ai_central_intelligence" || agentIdOrName.agent_id === "nr_ai" || agentIdOrName.friendly_name === "NR-AI"));

  let agent = null;

  if (isCentral) {
    state.activeAgent = "NR-AI";
    state.focusAgent = null;
    state.galaxyMode = "NORMAL";
    state.activeConversationAgent = "nr_ai";
    state.activeConversationAgentName = "NR-AI";
    state.selectedNode = null;
    state.selectedAgent = null;

    if (state.galaxy) {
      state.galaxy.focus_mode = false;
      state.galaxy.focus_agent_id = null;
      state.galaxy.selected_node_id = "nr_ai_central_intelligence";
    }

    agent = AGENT_CATALOG["nr_ai_central_intelligence"] || {
      id: "nr_ai",
      name: "NR-AI",
      role: "Universal Central Intelligence",
      status: "ONLINE",
      icon: "🌌"
    };
  } else {
    const query = typeof agentIdOrName === "object" ? (agentIdOrName.agent_id || agentIdOrName.friendly_name) : agentIdOrName;
    agent = resolveAgentCatalog(query);
    if (!agent) return null;

    const canonId = canonicalizeAgentId(agent.id);
    state.activeAgent = agent.name;
    state.focusAgent = (agent.id === "droid_scout" || agent.id === "droid_guardian") ? "android_unified_agent" : agent.id;
    state.galaxyMode = "FOCUS";
    state.activeConversationAgent = canonId;
    state.activeConversationAgentName = agent.name;
    state.selectedNode = agent;
    state.selectedAgent = agent;

    if (state.galaxy) {
      state.galaxy.focus_mode = true;
      state.galaxy.focus_agent_id = state.focusAgent;
      state.galaxy.selected_node_id = agent.id;
    }
  }

  updateAgentInfoCard(agent);
  updateChatHeader(agent);
  updateCoreLabelVisibility();
  updateVoiceUI();

  renderActiveWorkspaceChat(getActiveCanonicalAgentId());

  return agent;
}
window.syncActiveAgent = syncActiveAgent;

function syncAgentInfoPanel(node) {
  return syncActiveAgent(node);
}

function appendChatMessage(arg1, arg2, arg3, targetAgent) {
  const container = document.getElementById("dedicatedChatHistory");
  const oldContainer = document.getElementById("panelChatHistory");

  let author = "Agent";
  let role = "agent";
  let text = "";

  if (arg3 !== undefined && typeof arg2 === "string" && (arg2 === "user" || arg2 === "agent" || arg2 === "scout" || arg2 === "guardian" || arg2 === "system")) {
    author = arg1 || "Agent";
    role = arg2;
    text = (arg3 !== undefined && arg3 !== null) ? String(arg3) : "";
  } else {
    role = arg1 || "agent";
    text = (arg2 !== undefined && arg2 !== null) ? String(arg2) : "";
    author = role === "user" ? "USER" : (role === "agent" ? (state.selectedAgent ? state.selectedAgent.name : "Agent") : String(role).toUpperCase());
  }

  const canonId = canonicalizeAgentId(targetAgent || getActiveCanonicalAgentId());

  if (!state.conversationStore[canonId]) {
    state.conversationStore[canonId] = [];
  }

  const msgObj = {
    message_id: `msg_${Date.now()}_${Math.random().toString(36).substr(2, 5)}`,
    agent_id: canonId,
    role: role,
    author: author,
    content: text,
    text: text,
    timestamp: new Date().toISOString()
  };

  state.conversationStore[canonId].push(msgObj);
  if (state.conversationStore[canonId].length > 50) {
    state.conversationStore[canonId] = state.conversationStore[canonId].slice(-50);
  }

  // Strictly isolated: ONLY render to DOM if this message belongs to currently active agent workspace!
  if (canonId === getActiveCanonicalAgentId()) {
    renderChatMessageElement(msgObj);
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }

  // Also sync to legacy container if exists
  if (oldContainer) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;
    bubble.innerHTML = `<span class="bubble-author">${author}:</span> ${String(text).replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br/>")}`;
    oldContainer.appendChild(bubble);
    oldContainer.scrollTop = oldContainer.scrollHeight;
  }
}
window.appendChatMessage = appendChatMessage;

function updateDroidProgress(stage, desc, pct) {
  const banner = document.getElementById("droidProgressBanner");
  if (banner) banner.style.display = "block";
  const stageElem = document.getElementById("droidProgressStage");
  if (stageElem) stageElem.textContent = stage;
  const descElem = document.getElementById("droidProgressDesc");
  if (descElem) descElem.textContent = desc;
  const pctElem = document.getElementById("droidProgressPct");
  if (pctElem) pctElem.textContent = `${pct}%`;
  const barFill = document.getElementById("droidProgressBarFill");
  if (barFill) barFill.style.width = `${pct}%`;
}
window.updateDroidProgress = updateDroidProgress;

async function sendDedicatedChatMessage() {
  const input = document.getElementById("dedicatedChatInput");
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  await dispatchVoiceUtterance(text, true);
}
window.sendDedicatedChatMessage = sendDedicatedChatMessage;

// =============================================================================
// MANUAL VOICE ACTIVATION ENGINE & CONTINUOUS CONVERSATION PIPELINE
// =============================================================================

function getLocalTimeGreeting(isResume = false) {
  if (isResume) {
    return "Welcome back, Boss.";
  }
  const h = new Date().getHours();
  if (h >= 5 && h < 12) return "Good morning, Boss.";
  if (h >= 12 && h < 18) return "Good afternoon, Boss.";
  return "Good evening, Boss.";
}
window.getLocalTimeGreeting = getLocalTimeGreeting;

async function startManualVoiceSession() {
  console.log("🎙️ Manual voice session activated by user.");
  state.voiceStopRequested = false;
  state.voiceSessionActive = true;

  updateVoiceTelemetry({
    speech: "STARTING",
    mic: "CHECKING...",
    recognition: "INITIALIZING",
    audio: "NO AUDIO",
    event: "startManualVoiceSession",
    error: "NONE"
  });

  // 1. Activate NR-AI
  syncActiveAgent("NR-AI");
  setVoiceSessionState("THINKING", "NR-AI");

  // Ensure microphone permissions
  const micOk = await ensureMicrophone();
  if (!micOk) {
    state.voiceSessionActive = false;
    setVoiceSessionState("MICROPHONE ERROR");
    updateVoiceTelemetry({ speech: "ERROR", mic: "DENIED", recognition: "STOPPED", error: "mic-inaccessible" });
    return;
  }

  // 2. Determine current local time greeting
  const isResume = sessionStorage.getItem("nrai_session_activated") === "true";
  sessionStorage.setItem("nrai_session_activated", "true");

  let greeting = getLocalTimeGreeting(isResume);
  try {
    const res = await fetch(`/api/session/greeting${isResume ? "?resume=1" : ""}`);
    if (res.ok) {
      const data = await res.json();
      if (data && data.greeting) {
        greeting = data.greeting;
      }
    }
  } catch (err) {
    console.warn("Session greeting fetch error, fallback to local:", err);
  }

  // Clear any interim notices
  const notice = document.getElementById("chatLiveInterimNotice");
  if (notice) notice.style.display = "none";

  // 3. Greeting appears in chat
  appendChatMessage("NR-AI", "agent", greeting);

  // 4. Greeting spoken through TTS, then safeStartRecognition() will transition to LISTENING when onstart fires
  if (state.ttsEnabled) {
    setVoiceSessionState("SPEAKING", "NR-AI");
    speakText(greeting, () => {
      if (state.voiceSessionActive && !state.voiceStopRequested) {
        safeStartRecognition();
      }
    });
  } else {
    safeStartRecognition();
  }

  pollGalaxyState();
}
window.startManualVoiceSession = startManualVoiceSession;

function stopVoiceCommunication() {
  console.log("⏹️ Stopping Voice Communication session...");
  state.voiceStopRequested = true;
  state.voiceSessionActive = false;
  state.isRecognitionActive = false;
  state.isRecognitionStarting = false;

  updateVoiceTelemetry({
    speech: "STOPPED",
    mic: "INACTIVE",
    recognition: "STOPPED",
    audio: "NO AUDIO",
    event: "stopVoiceCommunication",
    error: "NONE"
  });

  const synth = window.speechSynthesis || state.speechSynth;
  if (synth) {
    synth.cancel();
  }
  state.isSpeakingAudio = false;
  clearTimeout(state.introTimer);

  if (state.recognition) {
    try {
      state.recognition.abort();
    } catch (e) {}
  }

  setVoiceSessionState("INACTIVE");
  syncActiveAgent("NR-AI");
  updateVoiceUI();
  pollGalaxyState();
}
window.stopVoiceCommunication = stopVoiceCommunication;

function resolveAgentFromUtterance(text) {
  if (!text) return null;
  const c = text.toLowerCase().replace(/[.!?]+$/, "").trim();

  // Engineering commands must NOT be intercepted as simple greeting switches
  if (c.includes("android studio") || c.includes("visual studio") || c.includes("make gradle") || c.includes("run gradle")) {
    return null;
  }

  const aliases = {
    "droid": { id: "android_unified_agent", name: "Droid" },
    "android": { id: "android_unified_agent", name: "Droid" },
    "android agent": { id: "android_unified_agent", name: "Droid" },
    "droid scout": { id: "droid_scout", name: "Droid Scout" },
    "scout": { id: "droid_scout", name: "Droid Scout" },
    "droid guardian": { id: "droid_guardian", name: "Droid Guardian" },
    "guardian": { id: "droid_guardian", name: "Droid Guardian" },
    "unity": { id: "unity_autonomous_agent", name: "Unity" },
    "unreal": { id: "unreal_autonomous_agent", name: "Unreal" },
    "visual studio": { id: "vs_unified_agent", name: "Studio" },
    "studio": { id: "vs_unified_agent", name: "Studio" },
    "vs": { id: "vs_unified_agent", name: "Studio" },
    "knowledge": { id: "universal_knowledge_engine", name: "Knowledge" },
    "nova": { id: "nova_discovery_agent", name: "Nova" },
    "aegis": { id: "aegis_verification_agent", name: "Aegis" },
    "sentinel": { id: "computer_control_agent", name: "Sentinel" },
    "skyshield": { id: "security_agent", name: "SkyShield" },
    "shield": { id: "security_agent", name: "SkyShield" },
    "security": { id: "security_agent", name: "SkyShield" },
    "nexus": { id: "nexus_coordinator", name: "Nexus" },
    "quest": { id: "research_agent", name: "Quest" },
    "vision": { id: "vision_agent", name: "Vision" },
    "forge": { id: "forge_dev_agent", name: "Forge" },
    "pixel": { id: "pixel_ui_agent", name: "Pixel" },
  };

  if (state.galaxy && state.galaxy.nodes) {
    for (const node of state.galaxy.nodes) {
      if (node.friendly_name) {
        aliases[node.friendly_name.toLowerCase()] = { id: node.agent_id, name: node.friendly_name };
      }
      if (node.agent_id) {
        aliases[node.agent_id.toLowerCase()] = { id: node.agent_id, name: node.friendly_name || node.agent_id };
      }
    }
  }

  // Sort aliases by length descending so longer aliases match first
  const sortedEntries = Object.entries(aliases).sort((a, b) => b[0].length - a[0].length);

  const preambleRegex = /^(?:(?:hey|hi|hello|yo|please|can you|could you|activate|switch to|select|talk to|go to|wake up)\s+)+/;
  const cStripped = c.replace(preambleRegex, "").trim();

  for (const [alias, info] of sortedEntries) {
    if (alias.includes("studio") || alias.includes("android studio")) continue;
    if (c === alias || cStripped === alias) {
      return info;
    }
  }
  return null;
}
window.resolveAgentFromUtterance = resolveAgentFromUtterance;

async function dispatchVoiceUtterance(text, isManual = false, alreadyInChat = false) {
  if (!text || !text.trim()) return;
  const rawText = text.trim();
  const cLower = rawText.toLowerCase().replace(/[.!?]+$/, "").trim();

  // CRITICAL REQUIREMENT: If voice session is inactive and this is not a manual text input, drop utterance
  if (!isManual && (!state.voiceSessionActive || state.voiceStopRequested)) {
    console.log(`[Voice Pipeline] Dropped utterance "${rawText}": voice session is INACTIVE.`);
    return;
  }

  console.log(`[Voice Pipeline] Dispatching: "${rawText}" (activeAgent: ${state.activeAgent}, session: ${state.voiceSession})`);

  // 1. Natural conversation / Barge-in: interrupt ongoing speech immediately
  if (state.isSpeakingAudio || (state.speechSynth && state.speechSynth.speaking)) {
    interruptSpeech();
  }

  // Ensure user utterance is visible in active chat
  if (!alreadyInChat) {
    appendChatMessage("USER", "user", rawText);
  }

  // 2. Explicit Stop Communication Triggers
  const stopTriggers = ["stop communication", "stop listening", "stop voice session", "end voice session", "stop voice", "end session"];
  if (stopTriggers.some(trig => cLower === trig || cLower.startsWith(trig))) {
    appendChatMessage("System", "agent", "Voice communication session stopped, Boss. Standing by.");
    stopVoiceCommunication();
    return;
  }

  // Set to THINKING / PROCESSING while executing
  setVoiceSessionState("PROCESSING");

  // 3. Central NR-AI return triggers ("wake up NR-AI" / "wake NR-AI")
  const centralTriggers = [
    "wake up nr-ai", "wake nr-ai", "wake up nrai", "wake nrai",
    "wake up nr ai", "wake nr ai", "nr-ai", "hey nr-ai", "central",
    "back to nr-ai", "let nr-ai handle this", "return to nr-ai", "return to central", "reset"
  ];
  if (centralTriggers.includes(cLower)) {
    await returnToCentral();
    return;
  }

  // 4. Check for direct agent addressing (e.g. "Droid", "Unity", "Unreal", "Visual Studio", "Knowledge", etc.)
  const addressedInfo = resolveAgentFromUtterance(rawText);
  if (addressedInfo) {
    syncActiveAgent(addressedInfo.name);
    setVoiceSessionState("PROCESSING", addressedInfo.name);

    try {
      const res = await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: rawText, agent_id: getActiveCanonicalAgentId() }),
      });
      const data = await res.json();
      const reply = data.text || `Yes Boss, I'm ${addressedInfo.name}. Ready to assist.`;
      appendChatMessage(addressedInfo.name, "agent", reply);

      if (state.ttsEnabled && state.voiceSessionActive && !state.voiceStopRequested) {
        setVoiceSessionState("SPEAKING", addressedInfo.name);
        speakText(reply, () => {
          if (state.voiceSessionActive && !state.voiceStopRequested) {
            safeStartRecognition();
          }
        });
      } else if (state.voiceSessionActive && !state.voiceStopRequested) {
        safeStartRecognition();
      }
    } catch (err) {
      appendChatMessage("System", "agent", `Error: ${err.message}`);
      if (state.voiceSessionActive && !state.voiceStopRequested) {
        safeStartRecognition();
      }
    }
    return;
  }

  // 5. Active Agent Command Execution (or General NR-AI Command)
  // The active agent remains center, other agents remain hidden, response is spoken, resumes listening!
  const currentActiveName = state.activeAgent || state.activeConversationAgentName || "NR-AI";

  const isAndroidIntent = cLower.includes("android") || cLower.includes("studio") || cLower.includes("gradle") || cLower.includes("apk");
  const isEngineering = isAndroidIntent || cLower.includes("open") || cLower.includes("build") || cLower.includes("run") || cLower.includes("project") || cLower.includes("create") || cLower.includes("modify") || cLower.includes("activity");
  const progBanner = document.getElementById("droidProgressBanner");
  if (isEngineering && progBanner && (isAndroidIntent || currentActiveName === "Droid" || isDroidFocused())) {
    progBanner.style.display = "block";
    updateDroidProgress("EXECUTING", `Executing: "${rawText}"`, 25);
  }

  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: rawText, agent_id: getActiveCanonicalAgentId() }),
    });
    const data = await res.json();
    const reply = data.text || "Operation completed.";

    const respAgentId = data.data?.active_conversation_agent || (data.routed_to === "Droid" ? "droid" : null);
    const respAgentName = data.data?.agent_name || (data.routed_to === "Droid" ? "Droid" : (data.routed_to || currentActiveName));

    if (respAgentId && canonicalizeAgentId(respAgentId) !== getActiveCanonicalAgentId()) {
      syncActiveAgent(respAgentName || respAgentId);
    }
    const finalAuthor = (data.routed_to === "Droid" || respAgentId === "droid") ? "Droid" : (respAgentName || currentActiveName);
    const finalTargetId = respAgentId ? canonicalizeAgentId(respAgentId) : getActiveCanonicalAgentId();
    appendChatMessage(finalAuthor, "agent", reply, finalTargetId);

    if (progBanner && (data.routed_to === "Droid" || respAgentId === "droid" || currentActiveName === "Droid" || isDroidFocused())) {
      updateDroidProgress("COMPLETED", "Execution completed successfully.", 100);
      setTimeout(() => {
        if (progBanner) progBanner.style.display = "none";
      }, 3500);
    }

    const speakerName = (data.routed_to === "Droid" || respAgentId === "droid") ? "Droid" : (respAgentName || currentActiveName);
    if (state.ttsEnabled && state.voiceSessionActive && !state.voiceStopRequested) {
      setVoiceSessionState("SPEAKING", speakerName);
      speakText(reply, () => {
        if (state.voiceSessionActive && !state.voiceStopRequested) {
          safeStartRecognition();
        }
      });
    } else if (state.voiceSessionActive && !state.voiceStopRequested) {
      safeStartRecognition();
    }
  } catch (err) {
    console.error("Voice command execution error:", err);
    appendChatMessage("System", "agent", `Error: ${err.message}`);
    if (progBanner) {
      updateDroidProgress("FAILED", `Error: ${err.message}`, 100);
    }
    if (state.voiceSessionActive && !state.voiceStopRequested) {
      safeStartRecognition();
    }
  }

  pollGalaxyState();
}
window.dispatchVoiceUtterance = dispatchVoiceUtterance;

window.getVoiceSessionState = function() {
  return {
    voiceSession: state.voiceSession || "INACTIVE",
    activeAgent: state.activeAgent || (isDroidFocused() ? "Droid" : "NR-AI"),
    focusAgent: state.focusAgent,
    isSpeaking: Boolean(state.isSpeakingAudio || (state.speechSynth && state.speechSynth.speaking)),
    isListening: state.voiceSession === "LISTENING",
  };
};

// Use syncActiveAgent as authoritative handler for selectAgent
const origSelectAgent = window.selectAgent;
window.selectAgent = async function(nodeOrId) {
  const agent = syncActiveAgent(nodeOrId);
  if (origSelectAgent && typeof nodeOrId === "object" && nodeOrId.agent_id !== "nr_ai_central_intelligence") {
    try {
      await origSelectAgent(nodeOrId);
    } catch (e) {}
  }
  return agent;
};

// Initialize active agent, speech recognition, and UI on load
document.addEventListener("DOMContentLoaded", () => {
  syncActiveAgent("NR-AI");
  initSpeechRecognition();
  updateVoiceUI();
});
