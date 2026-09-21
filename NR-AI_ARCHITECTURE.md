# NR-AI — System Architecture Blueprint
**Status**: Universal Engineering Workflow Operational  
**Last Updated**: September 20, 2026 Continuum  

## Architectural Overview
NR-AI is an autonomous, multi-agent AI engineering continuum orchestrating cognitive intelligence, security, unified engineering intent, and specialized domain engineering capabilities.

```
                    +------------------------------------+
                    |        NRCompanion Core            |
                    | (NRBrain / Memory / Audio / Voice) |
                    +-----------------+------------------+
                                      |
                     +----------------v-----------------+
                     |   Universal Engineering Intent   |
                     |       & Continuity Engine        |
                     |  - EngineeringIntentParser (16)  |
                     |  - ActiveProjectContextManager   |
                     |  - High-Level Concept Resolver   |
                     |  - Command Injection Defense     |
                     +----------------+-----------------+
                                      |
        +-----------------------------+-----------------------------+
        |                             |                             |
+-------v-------+             +-------v-------+             +-------v-------+
|   Knowledge   |             |   SkyShield   |             |     Droid     |
|    Trinity    |             |   Security    |             |  Specialist   |
|  (K/Nova/Aeg) |             |  (Phases 1-4) |             | (Phases 1-5)  |
+---------------+             +---------------+             +-------+-------+
                                                                    |
                   +------------------------------------------------+
                   |
     +-------------+-------------+-------------+-------------+-------------+
     |                           |                           |             |
+----v-----+               +-----v----+                +-----v----+  +-----v----+
| Phase 1  |               | Phase 2  |                | Phase 3  |  | Phase 4  |
| Static   |               | Live     |                | Auto     |  | Advanced |
| Eng Intel|               | Runtime  |                | Debug/Fix|  | Intel/KG |
+----------+               +----------+                +----------+  +----------+
                                                                           |
                                                                     +-----v----+
                                                                     | Phase 5  |
                                                                     | Prod Pro |
                                                                     | Readiness|
                                                                     +----------+
```

### Universal Engineering Intent & Continuity Stack:
1. **Engineering Intent Parser (`app/agent/engineering_intent.py`)**:
   - 16 Standardized Actions: `OPEN`, `CREATE_PROJECT`, `CONFIGURE_PROJECT`, `BUILD`, `RUN`, `INSTALL`, `TEST`, `DEBUG`, `INSPECT`, `MODIFY`, `DESIGN`, `REFACTOR`, `FIX`, `REBUILD`, `VERIFY`, `CONTINUE_PROJECT`.
   - 5 Engineering Domains: `ANDROID`, `UNREAL`, `VISUAL_STUDIO`, `UNITY`, `GENERAL`.
   - Parameter & Language Extraction: Kotlin, Java, C++, C#, templates, build targets.
   - Safe verification levels: `NONE`, `SYNTAX`, `BUILD`, `TEST`, `RUNTIME`, `FULL`.
   - Comprehensive Injection Defense: Regex sanitization blocking shell metacharacters (`;`, `&&`, `|`), path traversals (`../`, `..\`), destructive commands.

2. **Active Project Context & Continuity Engine (`app/agent/engineering_context.py`)**:
   - `ActiveProjectContext`: Encapsulates `project_id`, `project_name`, `domain`, `canonical_path`, `active_feature`, `last_action`, `affected_files`, `parameters`, and `history`.
   - `ActiveProjectContextManager`: Manages active context lifecycle, persistence to JSON, memory isolation, and high-level concept resolution.
   - High-Level Concept-to-File Resolution: Automatically maps abstract feature concepts ("splash screen", "login", "auth", "logo", "main activity") to concrete source and resource files.

3. **Real Engineering Execution & Deterministic Toolchain Control (`app/agent/android_unified_agent.py`)**:
   - Studio Process Lifecycle: `_resolve_studio_executable()`, `_find_running_studio_process()`, `_launch_android_studio(project_path)` with live PID tracking via `psutil`.
   - Bounded Gradle Execution: Real `gradlew.bat assembleDebug` runs, exit code verification, physical APK size/hash verification.
   - Grounded Code/Resource Manipulation: Concrete disk modification for high-level features (`SplashActivity.kt`, `activity_splash.xml`, `AndroidManifest.xml`).
   - Verified ADB Deployment & Runtime Pipeline: Validates device connectivity, boots authorized `Pixel_6_API_34` on `emulator-5554`, checks APK staleness and triggers automatic `assembleDebug` builds if sources are newer, installs APK via `SafeAdbClient`, launches package, validates running PID via `pidof`, verifies foreground activity via window displays, and captures live PNG screenshots to disk.
   - Final Gate Actions Alignment: All 8 actions (`CREATE_PROJECT`, `OPEN`, `BUILD`, `RUN`, `MODIFY`, `CONTINUE_PROJECT`, `REBUILD`, `VERIFY`) guaranteed `LIVE_VERIFIED`.

4. **Droid Production Stack (Phases 1â€“5)**:
   - Phase 1: Dynamic Project Registry, Gradle TOML Catalog, Kotlin/Java AST, XML Resource Graph, Compose Intelligence, JUnit/Lint Parser, SQLite Task Store.
   - Phase 2: 16-State Device Lifecycle Controller, 6-Stage Verified Deployment Pipeline, Compose Preview Analysis, Runtime Compose Semantics, Visual Verifier.
   - Phase 3: Failure Reproduction Engine, Approved UI Actions, Multi-Domain Evidence Collection, Root Cause Analysis, Bounded Repair Orchestrator (max 2 attempts).
   - Phase 4: Android Studio Intel, Unified Knowledge Graph (20 nodes, 36 edges), Kotlin AST (8 symbols), Test Intel, UI Debugger, Performance Diagnostics, Project Memory Store, Impact & Blast Radius Analyzer, Model Reasoning.
   - Phase 5: Android Studio Workspace Engine, AndroidManifest Merge Engine, Deep Accessibility & UI Quality Auditor, Runtime Diagnostics Pro (Jank & StrictMode), Multi-Project Manager, Authoritative 26-Dimension Readiness Auditor.

5. **Real Galaxy UI to Engineering Toolchain Pipeline (`app/ui/static/galaxy.js`, `galaxy.html`)**:
   - Web-Based 3D Visual Force Graph: Interactive agent topology rendering all active agents.
   - Node Selection Bridge: `window.selectAgent(droidNode)` directly focuses Droid (`android_unified_agent`).
   - Direct Agent Dialogue: Natural language interaction via `#panelTextInput` and `#panelSendBtn`.
   - Real User to Device Flow:
     ```
     [REAL USER / BROWSER (http://127.0.0.1:8585/)]
             |
             v
     [GALAXY UI DIRECT AGENT DIALOGUE]
             |
             v
     [NR-AI CORE ROUTER & INTENT ENGINE]
             |
             v
     [DROID (android_unified_agent)]
             |
        +----+--------------------------------------------+
        |                                                 |
        v                                                 v
     [LOCAL FILESYSTEM & STUDIO]                    [GRADLE & ADB TOOLCHAIN]
       - C:\NR-AI\dev_projects\NR-AI                - gradlew.bat assembleDebug
       - studio64.exe (PID: 2828 / 16932)           - adb.exe -s emulator-5554
       - Source (.kt) & Layout (.xml) files            - Pixel_6_API_34 (PID: 14912)
        |                                                 |
        +--------------------+----------------------------+
                             |
                             v
     [EMPIRICAL VERIFICATION & FEEDBACK LOOP]
       - Live Process Verification (Get-Process)
       - Dumpsys Window Focus & Activity Stack
       - Framebuffer Screencap & Browser UI Screenshot Captures
       - Structured Response Bubble Rendered in Galaxy UI Chat
     ```

6. **Real-Time Engineering Progress Pipeline & Audio Hardening**:
   - **`ProgressTracker` (`app/agent/progress.py`)**: Thread-safe singleton capturing in-flight task stages (`UNDERSTANDING`, `CONFIGURING_GRADLE`, `BUILDING`, `OPENING_STUDIO`, `VERIFYING_EMULATOR`, `INSTALLING_APK`, `LAUNCHING_APP`, `VERIFYING_RUNTIME`, `CAPTURING_SCREEN`), progress percentages (0â€“100%), stage states (`QUEUED`, `EXECUTING`, `VERIFYING`, `COMPLETED`, `FAILED`), human-readable messages, and evidence arrays.
   - **`/api/engineering/progress` Endpoint (`app/ui/dashboard.py`)**: High-performance HTTP endpoint returning serialized active task state with zero locking overhead.
   - **`#droidProgressBanner` Component (`app/ui/static/galaxy.js`)**: Interactive DOM banner mounted in the Direct Agent Dialogue panel. Automatically begins 500ms polling upon command dispatch, updates percentage bar, stage badge, and evidence text in real-time, and cleanly tears down when the final agent chat bubble renders.
   - **Audio Subsystem Hardening**: Headless and non-voice modes bypass Windows PortAudio C drivers entirely using `DummyVoiceListener` (`app/voice/listener.py`). Microphone device enumeration in `/api/galaxy/state` is shielded by a 30-second TTL cache, preventing asynchronous memory collisions (`STATUS_ACCESS_VIOLATION 0xc0000005`) when browser WebAudio contexts initialize.

7. **Droid Child Specialist Hierarchy & Invariant Enforcement (`app/agent/droid_child_agents.py`)**:
   - **`DroidContext`**: Thread-safe shared state repository maintaining active project references, observation logs, failure records, and deterministic repair verification reports.
   - **Droid Scout (`droid_scout`)**:
     * Role: *Android Studio Watch & Development Assistant*.
     * Invariant: Strictly read-only observer. Prohibited from mutating files independently. Analyzes workspace files and advises Droid on improvements.
     * UI Representation: Planetary satellite orbiting Droid at radius $r = 150\text{px}$.
   - **Droid Guardian (`droid_guardian`)**:
     * Role: *Android Build & Verification Guardian*.
     * Invariant: Enforces deterministic build monitoring, compiler error extraction, runtime process/window verification, and closed-loop defect repair verification. Evidence takes absolute priority over speculative LLM text.
     * UI Representation: Planetary satellite orbiting Droid at radius $r = 185\text{px}$.
   - **Hierarchy Invariant**: Scout and Guardian NEVER connect directly to the Central Sun (`nr_ai_central_intelligence`). All connections originate exclusively from Droid (`android_unified_agent`). Validated empirically: 0 Sun links, 2 Droid links.

8. **4-Area Galaxy UI Architecture & Focus Mode Dynamics**:
   - **Top Navigation Bar**: System telemetry, connection status, global HUD.
   - **Left Panel Stack**: Agent Information Panel (`#agentInfoPanel`, $y=489$, $x=214$) positioned directly above System Overview Card (`.system-overview-card`, $y=784$, $x=214$).
   - **Central Canvas Viewport (`#galaxyCanvas`)**: 3D interactive celestial orbit graph. Focus Mode centers Droid at $(0, 0)$, orbits Scout at $r=150\text{px}$, orbits Guardian at $r=185\text{px}$, and gracefully hides unrelated agents.
   - **Right Side Panel (`#dedicatedChatPanel`)**: Dedicated conversational panel ($x=1136\text{px}$) with full message history and audio visualization.

9. **Voice & Acoustic Activation Subsystem (`app/voice/clap_detector.py`)**:
   - **Local Bounded Clap Detector**: High Crest Factor ($> 3.2$), rapid energy decay ($< 80\text{ms}$), 1.5s refractory debounce. Instantaneously triggers DOM banner `ðŸ‘ CLAP DETECTED â€¢ LISTENING...` with zero cloud latency.
   - **Time-Based Greetings (`/api/session/greeting`)**: Returns contextual morning/afternoon/evening greetings and session resume greetings.

10. **OpenJarvis Reference Integration Topology & Boundary Strategy**:
    - **Voice Provider Registry Abstraction (`app/voice/provider.py`)**: Inspired by OpenJarvis's decoupled backend registry pattern, providing pluggable STT and TTS backends with strict fallback resolution (`SpeechRecognitionSTTProvider` -> `MockSTTProvider`, `SAPI5TTSProvider` -> `MemoryTTSProvider` -> `SilentTTSProvider`).
    - **Architectural Boundary Invariant**: Voice is strictly an I/O channel into NR-AI; Central Intelligence (`app/brain/companion.py`) remains the sole authoritative brain and router.
    - **Prioritized Integration Strategy (Report 34 & Report 35)**:
      * Priority 1 (Implemented & Verified): Model catalog metadata (VRAM / context limits), prompt-level secret/PII guardrails, persistent SQLite task checkpointing, declarative agent specs, and 5-domain memory boundaries.
      * Priority 2 (Implemented & Verified): Google Agent-to-Agent (A2A) protocol endpoints and Model Context Protocol (MCP) safety bridge.
      * Priority 3 (Deferred to Phase 2): Optional local STT/TTS models (Faster-Whisper, Kokoro) and Tauri desktop wrapper.
      * Firm Non-Integration: OpenJarvis orchestrator, computer control, and voice loop rejected in favor of superior native NR-AI systems.

11. **OpenJarvis Integration Phase 1: Native Adapters & Safety Foundations**:
    - **Architectural Integration Topology**:
      ```
      [OpenJarvis Verified Capabilities (Apache 2.0)]
                             |
                             v
                 [NR-AI Native Adapters]
      - Model Catalog Intelligence (app/config/model_catalog.py)
      - SQLite Task Checkpoints (app/task/checkpoint_store.py)
      - Deterministic Guardrails (app/security/guardrails.py)
      - Local A2A Protocol Router (app/a2a/router.py)
      - MCP Safety Adapter (app/mcp/adapter.py)
      - Declarative Agent TOML (app/agent/definitions/declarative.py)
      - 5-Domain Memory Boundaries (app/memory/boundaries.py)
                             |
                             v
              [Existing NR-AI Central Brain]
      - NRCompanion / ModelRouter / MultiAgentOrchestrator
                             |
                             v
             [Existing Safety & Verification]
      - ModelIsolationGate / MCPSafetyGate / Shell Execution Barrier
      ```
    - **Model Catalog Intelligence (`app/config/model_catalog.py`)**: 20 standard models cataloged with verified hardware limits, provenance tracking (`VERIFIED`, `CONFIGURED`, `PROVIDER_REPORTED`, `UNKNOWN`), host hardware probe (RAM, CPU, CUDA GPU), and advisory `ModelCompatibilityEvaluator` integrated into `ModelRouter`.
    - **SQLite Task Checkpoints (`app/task/checkpoint_store.py`)**: Thread-safe WAL store (`data/task_checkpoints.db`), 9 lifecycle states, 64KB bounded payloads, recursive secret scrubbing, and deterministic recovery barrier blocking automatic resumption of destructive verbs.
    - **Prompt & Secret Guardrails (`app/security/guardrails.py`)**: Deterministic regex scanning for 11 secret patterns and 4 PII patterns with structured redaction tokens and pre-cloud transit scrubbing in `app/agent/model_provider.py`.
    - **Google A2A Local Router (`app/a2a/protocol.py`, `app/a2a/router.py`)**: Google A2A JSON-RPC 2.0 compliant agent-to-agent message broker, local in-process / 127.0.0.1 transport, `A2APermissionScope` authorization, and tamper-evident audit trail.
    - **MCP Safety Adapter (`app/mcp/adapter.py`)**: Tool invocation gateway mediated by `MCPSafetyGate`, strictly blocking direct LLM tool calls (`MODEL_ISOLATION_VIOLATION`), enforcing tool blocklists, and supporting instant emergency stop freezes.
    - **Declarative Agent Definitions (`app/agent/definitions/declarative.py`)**: Declarative TOML parsing (`from_toml`) with strict `allow_shell=False` safety invariant mapping to `AgentSpecification`.
    - **Explicit 5-Domain Memory Boundaries (`app/memory/boundaries.py`)**: Enforces strict boundaries across Knowledge, Conversation, Task, Project, and Agent domains with workspace leak prevention.

12. **OpenJarvis Integration Phase 2: Persistent Automation, Connectivity & Context Intelligence**:
    - **Architectural Topology**:
      ```
      [OpenJarvis Capabilities (Apache 2.0)]
                             |
                             v
                 [NR-AI Phase 2 Subsystems]
      - Persistent Automation Engine (app/automation/engine.py, models.py, scheduler.py)
      - Automation Safety & Recovery Barrier (app/automation/scheduler.py)
      - A2A Phase 2 Local Broker (app/a2a/protocol.py, router.py, client.py)
      - Controlled MCP Client (app/mcp/config.py, client.py, adapter.py)
      - Selective Personal Connectors (app/connectors/)
      - 7-Channel Context Intelligence (app/context/budget.py, manager.py)
      - Unified Memory Boundaries (app/memory/integration.py)
      - Sanitized Observability & Telemetry (app/telemetry/)
                             |
                             v
              [Existing NR-AI Central Brain]
      - NRCompanion / ModelRouter / MultiAgentOrchestrator
                             |
                             v
             [Existing Safety & Verification]
      - ModelIsolationGate / MCPSafetyGate / Shell Execution Barrier / E-Stop
      ```
    - **Persistent Automation Engine (`app/automation/`)**: SQLite WAL store (`data/automations.db`), 4 schedule types (`ONCE`, `INTERVAL`, `CRON`, `CONDITION`), rate limits (10/min), timeouts (1-120s), and `AutomationExecutionRecord` history.
    - **Automation Safety & Recovery Barrier**: Destructive verb detection requiring human confirmation tokens. Active destructive tasks paused across process restarts with `RECOVERY_BARRIER`.
    - **A2A Phase 2 Subsystem (`app/a2a/`)**: Google A2A JSON-RPC 2.0 broker with discovery, structured task delegation with checkpoint and evidence references, status polling, cancellation, and emergency stop freeze.
    - **Controlled MCP Client (`app/mcp/`)**: Explicit server trust states (`TRUSTED`, `PROBATION`, `QUARANTINED`), tool allowlists, `ModelIsolationGate` enforcement blocking direct LLM invocations, output sanitization, and audit logging.
    - **Selective Personal Connectors (`app/connectors/`)**: Strict read-only enforcement (`WRITE_PROHIBITED`), unconfigured fallback (`CONNECTOR_NOT_CONFIGURED`), `LocalFileConnector` with path traversal defense, and `SystemInfoConnector` without shell execution.
    - **Context Intelligence & Budget Manager (`app/context/`)**: 7-channel partitioning (`SYSTEM`, `TASK`, `PROJECT`, `KNOWLEDGE`, `CONVERSATION`, `EVIDENCE`, `AGENT`), channel caps, deterministic priority truncation, and secret/PII scrubbing.
    - **Unified Memory Boundaries (`app/memory/integration.py`)**: 5-domain boundary validation, authorized checkpoint restore, cross-workspace leakage block (`WORKSPACE_LEAKAGE_DENIED`), and agent scratchpad isolation (`AGENT_ISOLATION_DENIED`).
    - **Sanitized Observability & Telemetry (`app/telemetry/`)**: Structured event logging with recursive stripping of bearer tokens, cookies, auth headers, and session credentials.

13. **Hard Stop Rule Enforcement**:
    - Android Engineering Workflow, Droid, Child Agents, Voice, OpenJarvis Phase 1, and OpenJarvis Phase 2 are 100% complete and empirically verified.
    - Unreal Engine, Unity, Visual Studio, Voice Phase 2 (Whisper/Kokoro), and OpenJarvis Phase 3 remain strictly NOT started awaiting explicit user command.


