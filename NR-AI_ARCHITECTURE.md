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

4. **Droid Production Stack (Phases 1–5)**:
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
   - **`ProgressTracker` (`app/agent/progress.py`)**: Thread-safe singleton capturing in-flight task stages (`UNDERSTANDING`, `CONFIGURING_GRADLE`, `BUILDING`, `OPENING_STUDIO`, `VERIFYING_EMULATOR`, `INSTALLING_APK`, `LAUNCHING_APP`, `VERIFYING_RUNTIME`, `CAPTURING_SCREEN`), progress percentages (0–100%), stage states (`QUEUED`, `EXECUTING`, `VERIFYING`, `COMPLETED`, `FAILED`), human-readable messages, and evidence arrays.
   - **`/api/engineering/progress` Endpoint (`app/ui/dashboard.py`)**: High-performance HTTP endpoint returning serialized active task state with zero locking overhead.
   - **`#droidProgressBanner` Component (`app/ui/static/galaxy.js`)**: Interactive DOM banner mounted in the Direct Agent Dialogue panel. Automatically begins 500ms polling upon command dispatch, updates percentage bar, stage badge, and evidence text in real-time, and cleanly tears down when the final agent chat bubble renders.
   - **Audio Subsystem Hardening**: Headless and non-voice modes bypass Windows PortAudio C drivers entirely using `DummyVoiceListener` (`app/voice/listener.py`). Microphone device enumeration in `/api/galaxy/state` is shielded by a 30-second TTL cache, preventing asynchronous memory collisions (`STATUS_ACCESS_VIOLATION 0xc0000005`) when browser WebAudio contexts initialize.
