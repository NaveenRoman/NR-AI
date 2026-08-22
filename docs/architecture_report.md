# NR-AI System Architecture Report
**Release Candidate Baseline — Checkpoint v1.0.0**

## 1. System Overview & Data Flow

```
+-----------------------------------------------------------------------------------+
|                               USER INTERFACE                                      |
|            Voice Microphone Input (STT)  |  Typed CLI / Command Line              |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                           app/voice/listener.py                                   |
|   - VoiceListener: Audio capture, noise calibration, filler phrase normalization  |
|   - ListeningState: Explicit state tracking (IDLE, LISTENING, PROCESSING, etc.)   |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                            app/brain/brain.py                                     |
|   - NRBrain: Unified cognitive hub, intent routing, conversational handling       |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                         app/agent/task_planner.py                                 |
|   - TaskPlanner: Intent classification & multi-action plan decomposition          |
|     (Visual GUI plans, single-file code, full-stack architectures, terminal tasks)|
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                       app/agent/action_dispatcher.py                              |
|   - ActionDispatcher: Dispatches planned actions to specialized execution engines |
+-----------------------------------------------------------------------------------+
            |                               |                              |
            v                               v                              v
+-----------------------+     +-----------------------+     +-----------------------+
| app/agent/code_agent  |     | app/agent/computer_   |     |   app/vision/screen   |
| .py                   |     | control.py            |     |   app/vision/menu_    |
| - CodeWriter: Safe    |     | - 20 Generic computer |     |   popup.py            |
|   file ops, rollback  |     |   control primitives  |     | - RapidOCR ONNX       |
| - CodeRunner: Multi-  |     | - TerminalManager:    |     |   text recognition    |
|   language execution  |     |   Subprocesses, safe  |     | - MenuPopupVision:    |
| - ErrorAnalyzer:      |     |   command guardrails  |     |   Dropdown resolution |
|   Traceback diagnostic|     | - ProjectInspector:   |     | - StateVerifier:      |
| - RecoveryEngine:     |     |   Tree & multi-file   |     |   OCR text match      |
|   Self-healing patches|     | - Service Smoke Test  |     | - WindowManager:      |
+-----------------------+     +-----------------------+     |   Foreground focus    |
            |                               |               +-----------------------+
            +-------------------------------+------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                           app/voice/speaker.py                                    |
|   - VoiceSpeaker: Concise text & TTS audio response synthesis (Pyttsx3 / Memory)  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Module Responsibilities Matrix

| Module | Responsibility | Key Classes & Functions |
| :--- | :--- | :--- |
| **`app/brain/brain.py`** | Central command router and conversation coordinator. Routes commands between Time, System, AppLauncher, TaskPlanner, and CodeAgent. | `NRBrain`, `NRBrain.think()` |
| **`app/agent/task_planner.py`** | Intent classification and multi-step plan generation for visual, terminal, code, and full-stack software requirements. | `TaskPlanner`, `_plan_complex_command()`, `_plan_fullstack_task_manager()`, `plan()` |
| **`app/agent/action_dispatcher.py`** | Routes planned actions to `CodeAgent`, `ComputerControl`, `VisualAgent`, or `ActionEngine`. | `ActionDispatcher`, `execute()` |
| **`app/agent/code_agent.py`** | End-to-end self-healing coding pipeline orchestrator: `WRITE → RUN → ERROR → ANALYZE → FIX → RUN → VERIFY`. | `CodeAgent`, `execute()`, `fix_existing_file()` |
| **`app/agent/code_writer.py`** | Atomic file operations with timestamped checkpoints (`data/checkpoints/`), diff modification, and rollback capability. | `CodeWriter`, `write_file()`, `modify_file()`, `restore_backup()` |
| **`app/agent/code_runner.py`** | Multi-language isolated process execution (Python, Node.js, Java javac/JVM, C GCC/Clang) with timeout enforcement. | `CodeRunner`, `run()` |
| **`app/agent/error_analyzer.py`** | Parses stderr, compiler logs, and stack traces into structured diagnostics (failing line, error category, diagnostic suggestion). | `ErrorAnalyzer`, `analyze()` |
| **`app/agent/recovery_engine.py`** | Heuristic and rule-based self-healing engine with bounded retries (max 3) and patch loop detection. | `RecoveryEngine`, `generate_patch()` |
| **`app/agent/computer_control.py`** | 20 unified generic computer control primitives (mouse, keyboard, shortcuts, clipboard, tabs, windows, explorer, terminal, server smoke testing). | `ComputerControl`, `smoke_test_server()`, `run_terminal_command()` |
| **`app/agent/project_inspector.py`** | Directory tree inspection, recursive search, grep search, and multi-file batch find-and-replace with framework protection. | `ProjectInspector`, `inspect_structure()`, `batch_replace()` |
| **`app/agent/terminal_manager.py`** | Safe terminal execution manager with command history, output capture, and dangerous command guardrails (`rmdir /s`, `format`). | `TerminalManager`, `run_command()` |
| **`app/vision/screen.py`** | RapidOCR ONNX screenshot analysis and lazy-loaded text coordinate detection. | `ScreenVision` |
| **`app/vision/menu_popup.py`** | Specialized OCR detector for dropdown menus and contextual popups. | `MenuPopupVision`, `find_menu_and_item()` |
| **`app/agent/window_manager.py`** | Windows OS window title detection and foreground window activation. | `WindowManager`, `activate_window()` |
| **`app/voice/listener.py`** | Speech capture, microphone start/stop control, ambient noise calibration, filler word normalization, and listening state tracking. | `VoiceListener`, `ListeningState`, `MockSpeechRecognizer` |
| **`app/voice/speaker.py`** | Text-to-speech audio feedback generator with speech sanitization (preventing raw code/debug dumps from being spoken). | `VoiceSpeaker`, `Pyttsx3TTS`, `SilentTTS`, `MemoryTTS` |
| **`app/config/voice_config.py`** | Centralized configuration for speech recognition and TTS parameters. | `VoiceConfig` |
| **`main.py`** | Production executable entry point supporting `--cli`, `--command "<task>"`, and interactive voice loop. | `main()` |
