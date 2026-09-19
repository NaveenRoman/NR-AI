# NR-AI — Official Test Results
**Run Date**: September 19, 2026  
**Execution Environment**: Python 3.11 (.venv) on Windows 11 + Authorized AVD `Pixel_6_API_34` (`emulator-5554`, Android 14 / API 34)  

## Empirical Live AVD Validation Results (`LIVE_VERIFIED`)
- **Live Device Boot & Probing**: **PASS** (`sys.boot_completed=1`, `pm path android` responsive)
- **Live Gradle Clean Build (`assembleDebug`)**: **PASS** (Returncode: 0, Duration: 2.26s, Size: 827 KB)
- **Live Device APK Deployment & Launch**: **PASS** (Package verified by PM, PID 6355 / 6575, Foreground verified)
- **Controlled Bug Reproduction (Device)**: **REPRODUCED** (`ArithmeticException: divide by zero` at `ControlledBugFixture.kt:8` captured in live Logcat)
- **Controlled Bug Reproduction (Test Runner)**: **REPRODUCED** (`ControlledBugFixtureTest > testControlledBugExecution FAILED`)
- **Multi-Domain Evidence Ingestion**: **PASS** (7 records ingested across Logcat, JUnit, Source AST, Device State; secret scrubbing CLEAN)
- **Root Cause Correlation Analysis**: **CONFIRMED** (Confidence: 0.95, causal link isolated to `ControlledBugFixture.kt:8`)
- **Bounded Autonomous Code Repair**: **PASS** (Attempt 1 of `MAX_REPAIR_ATTEMPTS = 2`, target SHA matched, backup `.bak` created, build passed)
- **Rebuild & Live Redeployment**: **PASS** (Fresh APK built and redeployed to `emulator-5554`, PID 6575)
- **Live Bug Elimination Retest (Device)**: **VERIFIED** (Re-triggered intent; 0 crashes in Logcat, process remained continuously alive)
- **Live Unit Test Runner Retest (Gradle)**: **1 / 1 PASS (100%)** (`ControlledBugFixtureTest` PASSED in 4.75s)
- **Live Visual Screen Verification**: **PASS** (Captured valid 126,069-byte PNG screenshot of running app)

## Automated Subsystem Test Suites:
- `tests.test_droid_phase3_reproduction`: **6 / 6 PASS (100%)**
- `tests.test_droid_phase3_ui_actions`: **5 / 5 PASS (100%)**
- `tests.test_droid_phase3_evidence`: **4 / 4 PASS (100%)**
- `tests.test_droid_phase3_root_cause`: **5 / 5 PASS (100%)**
- `tests.test_droid_phase3_repair`: **2 / 2 PASS (100%)**
- `tests.test_droid_phase3_e2e`: **2 / 2 PASS (100%)**
- `tests.test_droid_phase3_regression`: **3 / 3 PASS (100%)**
- `tests.test_droid_phase3_task_recovery`: **3 / 3 PASS (100%)**
- `tests.test_droid_phase3_security`: **4 / 4 PASS (100%)**
- **Dedicated Droid Phase 3 Total**: **34 / 34 PASS (100%)**
- **Dedicated Droid Phase 2 Total**: **28 / 28 PASS (100%)**
- **Dedicated Droid Phase 1 Total**: **51 / 51 PASS (100%)**
- **Pre-Droid Foundation Hardening Total**: **17 / 17 PASS (100%)**
- **Step 6 Android Core (Phases 1–7)**: **175 / 175 PASS (100%)**
- **SkyShield Security Command Center (Phases 1–4)**: **119 / 119 PASS (100%)**
- **Knowledge Trinity Subsystem**: **32 / 32 PASS (100%)**
- **Full Project Regression Battery Total**: **456 / 456 PASS (100% Zero Regressions)**
