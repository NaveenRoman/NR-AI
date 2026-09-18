# NR-AI — System Architecture Blueprint

## Knowledge Trinity Subsystem Architecture
```
[User Utterance / Galaxy UI]
         │
         ▼
[NRCompanion / CompanionDashboard]
         │
         ▼
[UniversalKnowledgeEngine]
         │
         ▼
[KnowledgeTrinityCoordinator]
   ├── Step 1: Coreference & Memory Resolution
   ├── Step 2: Local Knowledge Fabric Search
   ├── Step 3: Nova Discovery (Multi-source Web / arXiv / Wiki / Media)
   ├── Step 4: Knowledge Draft Synthesis
   ├── Step 5: Aegis Claim Decomposition & Verification Gatekeeper
   ├── Step 6: Bounded Review Loop (Max 2 Cycles)
   └── Step 7: Final Epistemic Badge & Rich Payload Generation
         │
         ▼
[Galaxy HUD & Rich Chat Interface]
   ├── Epistemic Badges (Verified, Inferred, Contested, etc.)
   ├── Copyable Code Blocks & Expandable Provenance Accordion
   ├── Multi-media Resource Cards (Videos, Repos, Docs)
   └── Real-time In-flight Trinity Telemetry
```
