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
4. **Phase 3 Live Validation Subsystem (Empirically Verified)**:
   - Real Device Target: `Pixel_6_API_34` (`emulator-5554`, Android 14 / API 34)
   - Real Execution Path: Boot -> Clean Build -> Deploy -> Reproduce -> Multi-Domain Ingest -> Diagnose (CONFIRMED) -> Bounded Repair (MAX_ATTEMPTS=2) -> Rebuild/Redeploy -> Retest (0 Crashes, 100% Pass) -> Visual Screen Capture (126 KB)
   - Invariant Boundaries: Zero `shell=True`, zero `eval()`, zero `exec()`, zero unvetted ADB calls, automated secret scrubbing, strict `Pixel_6_API_35` blocking.
   - Status: 100% LIVE_VERIFIED (456/456 tests passing across all suites).
   - Hard Stop: Droid Phase 4, Visual Studio, Unity, and Unreal are strictly NOT started.
