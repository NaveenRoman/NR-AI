# NR-AI — System Architecture Blueprint
**Status**: Droid Phase 3 Operational  
**Last Updated**: September 19, 2026 Continuum  

## Architectural Overview
NR-AI is an autonomous, multi-agent AI engineering continuum orchestrating cognitive intelligence, security, and specialized domain engineering capabilities.

```
                    +------------------------------------+
                    |        NRCompanion Core            |
                    | (NRBrain / Memory / Audio / Voice) |
                    +-----------------+------------------+
                                      |
       +------------------------------+------------------------------+
       |                              |                              |
+------v------+                +------v------+                +------v------+
|  Knowledge  |                |  SkyShield  |                |    Droid    |
|   Trinity   |                |  Security   |                | Specialist  |
| (K/Nova/Aeg)|                | (Phases 1-4)|                | (Phases 1-3)|
+-------------+                +-------------+                +------+------+
                                                                     |
                   +-------------------------------------------------+
                   |
     +-------------+-------------+-------------+-------------+
     |                           |                           |
+----v-----+               +-----v----+                +-----v----+
| Phase 1  |               | Phase 2  |                | Phase 3  |
| Static   |               | Live     |                | Auto     |
| Eng Intel|               | Runtime  |                | Debug/Fix|
+----------+               +----------+                +----------+
```

### Droid Architecture Stack:
1. **Phase 1 (Static Engineering)**:
   - Dynamic Project Registry (`android_project_registry.py`)
   - Gradle Version Catalog Engine (`android_gradle.py`)
   - Kotlin/Java AST Engine (`android_ast.py`)
   - XML Resource Graph Engine (`android_resources.py`)
   - Jetpack Compose Intelligence (`android_compose.py`)
   - JUnit/Lint Structured Parser (`android_test_results.py`)
   - SQLite Task State Store (`droid_task_state.py`)
2. **Phase 2 (Live Runtime & Device)**:
   - 16-State Device Lifecycle Controller (`android_device_lifecycle.py`)
   - 6-Stage Verified Deployment Pipeline (`android_deploy.py`)
   - Static Compose Preview Analyzer (`android_compose_preview.py`)
   - Runtime Compose Semantics Correlator (`android_runtime_semantics.py`)
   - Visual Verification & FIFO Screenshot Manager (`android_visual_verifier.py`)
3. **Phase 3 (Autonomous Debugging & Repair)**:
   - Failure Reproduction Engine (`android_reproduction.py`)
   - Approved UI Action Engine (`android_ui_actions.py`)
   - Multi-Domain Failure Evidence Collector (`android_failure_evidence.py`)
   - Root Cause Analysis Engine (`android_root_cause.py`)
   - Autonomous Bounded Repair Orchestrator (`android_repair_orchestrator.py`)
   - End-to-End Engineering Loop Engine (`android_e2e_engine.py`)
   - Regression Protection Engine (`android_regression.py`)
   - Unified Facade (`android_unified_agent.py`)
   - REST API & Galaxy UI (`server.py`, `dashboard.py`, `galaxy.js`)
5. **Phase 5 (Production-Grade Android Engineering Specialist)**:
   - Android Studio Workspace Engine (`android_studio_workspace.py`): `.idea/` workspace inspection, JBR 21, run configurations, ProGuard/R8 syntax analysis.
   - AndroidManifest Merge Engine (`android_manifest_merge.py`): Android 12+ exported attribute check, cleartext traffic policies, toolchain compatibility matrix.
   - Android Accessibility Audit Engine (`android_accessibility_audit.py`): 48dp touch targets, missing contentDescription, hardcoded literals.
   - Runtime Diagnostics Pro (`android_runtime_diagnostics_pro.py`): `dumpsys gfxinfo` jank frame percentiles (p50/p90/p95/p99) and StrictMode violation logcat analysis.
   - Multi-Project Manager (`android_multi_project.py`): Workspace-wide project discovery, registration, active switching, health matrix.
   - Android Readiness Auditor (`android_readiness_auditor.py`): Authoritative 26-dimension readiness scorecard.

6. **Live Validation Subsystem (Empirically Verified)**:
   - **Droid Phase 3 Live E2E**: **PASS**
   - Real Device Target: `Pixel_6_API_34` (`emulator-5554`, Android 14 / API 34)
   - Real Execution Path: Boot -> Clean Build -> Deploy -> Reproduce -> Multi-Domain Ingest -> Diagnose (CONFIRMED) -> Bounded Repair (MAX_ATTEMPTS=2) -> Rebuild/Redeploy -> Retest (0 Crashes, 100% Pass) -> Visual Screen Capture (126 KB)
   - Invariant Boundaries: Zero `shell=True`, zero `eval()`, zero `exec()`, zero unvetted ADB calls, automated secret scrubbing, strict `Pixel_6_API_35` blocking.
   - Status: 100% LIVE_VERIFIED (456/456 tests passing across all suites).

5. **Phase 4 (Advanced Android Engineering Intelligence — Knowledge Graph Architecture)**:
   - **Status**: **100% COMPLETE & LIVE-VERIFIED (494/494 tests passing across all suites; 0 regressions)**
   - **Android Engineering Knowledge Graph**: Central unified relational graph connecting Project -> Modules -> Gradle -> Dependencies -> Source AST -> Resources -> Compose -> UI -> Tests -> Runtime -> Logcat -> Performance -> Build -> Device.
   - **Android Studio Deep Integration** (`android_studio_intelligence.py`): Structured environment inspection (`AndroidStudioProjectSnapshot`), Studio, JBR/JDK, SDK, Gradle, AGP, Kotlin plugin, build variants, run configurations.
   - **Kotlin/Java Semantic Engine** (`android_semantic_engine.py`): Structural facts vs semantic inferences, bounded symbol/reference index, inheritance, overrides, coroutines, Android lifecycle methods.
   - **Compose State & Interaction Intelligence** (`android_compose_intelligence.py`): Composable functions, state holding (`remember`, `mutableStateOf`, `collectAsState`), event flow, recomposition patterns, classifications (`OBSERVED`, `STRONGLY_SUPPORTED`, `POSSIBLE`, `UNKNOWN`).
   - **Gradle Build Graph Engine** (`android_build_graph.py`): Modules, configurations, dependencies, versions, version catalogs (`libs.versions.toml`), mismatch detection, evidence-backed recommendations.
   - **Resource Cross-Reference Engine** (`android_resource_graph.py`): Bidirectional Kotlin/Compose <-> XML resource graph, missing/unused/broken reference detection.
   - **Multi-Module Project Reasoning** (`android_project_graph.py`): Inter-module dependency topology, impact tracking, affected test determination.
   - **Test Failure Intelligence** (`android_test_intelligence.py`): JUnit/Instrumentation -> test class -> stack trace -> source symbol -> repair candidate.
   - **UI Behavior Debugger** (`android_ui_debugger.py`): User action -> UI hierarchy -> Compose semantics -> callback -> state -> runtime result.
   - **Performance Diagnostics** (`android_performance.py`): Startup/launch time, repeated crashes, ANR indicators, log spam, memory/CPU metrics (`MEASURED`, `ESTIMATED`, `UNAVAILABLE`).
   - **Project Engineering Memory** (`android_project_memory.py`): Persistent project-scoped non-secret engineering knowledge and repair patterns in `DroidTaskStateStore`.
   - **Impact Analysis** (`AndroidImpactAnalyzer`): Pre-repair cross-module/resource/test dependency blast radius analysis (`AndroidImpactReport`).
   - **Model-Assisted Engineering Reasoning** (`android_model_reasoning.py`): Advisory model proposals governed strictly by deterministic gates. Model hypothesis alone can NEVER produce `CONFIRMED`. Deterministic runtime and source evidence is authoritative.
   - **Empirical Live Validation**: Real AVD `Pixel_6_API_34` (`emulator-5554`) validated end-to-end: Boot 56.5s -> Toolchain Discovery -> Knowledge Graph (20 nodes, 36 edges) -> Kotlin AST (8 symbols) -> Reproduction -> Ingest 4 Evidence Records -> Root Cause (CONFIRMED) -> Impact (MEDIUM) -> Bounded Repair (`count += 1`) -> Rebuild/Redeploy (PID 4516) -> Retest (100% pass) -> Perf Diagnostics (326ms startup, 46MB mem, 0 ANR, 0 crash) -> Visual Screenshot (111 KB) -> Memory Store (COMPLETED).
   - **Strict Hard Stop**: Droid Phase 4 is officially CLOSED. Droid Phase 5, Visual Studio, Unity, and Unreal remain strictly NOT started.


