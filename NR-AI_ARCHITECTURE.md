# NR-AI — System Architecture Blueprint
**Status**: Universal Engineering Workflow Operational  
**Last Updated**: September 19, 2026 Continuum  

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

3. **Droid Production Stack (Phases 1–5)**:
   - Phase 1: Dynamic Project Registry, Gradle TOML Catalog, Kotlin/Java AST, XML Resource Graph, Compose Intelligence, JUnit/Lint Parser, SQLite Task Store.
   - Phase 2: 16-State Device Lifecycle Controller, 6-Stage Verified Deployment Pipeline, Compose Preview Analysis, Runtime Compose Semantics, Visual Verifier.
   - Phase 3: Failure Reproduction Engine, Approved UI Actions, Multi-Domain Evidence Collection, Root Cause Analysis, Bounded Repair Orchestrator (max 2 attempts).
   - Phase 4: Android Studio Intel, Unified Knowledge Graph (20 nodes, 36 edges), Kotlin AST (8 symbols), Test Intel, UI Debugger, Performance Diagnostics, Project Memory Store, Impact & Blast Radius Analyzer, Model Reasoning.
   - Phase 5: Android Studio Workspace Engine, AndroidManifest Merge Engine, Deep Accessibility & UI Quality Auditor, Runtime Diagnostics Pro (Jank & StrictMode), Multi-Project Manager, Authoritative 26-Dimension Readiness Auditor.
