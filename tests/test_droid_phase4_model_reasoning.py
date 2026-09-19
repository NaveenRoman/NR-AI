"""
Tests for Droid Phase 4 Component 12: Model-Assisted Engineering Reasoning & Multi-Tier Confidence.
"""
import unittest
from app.agent.android_model_reasoning import (
    AndroidModelReasoningEngine,
    EngineeringConfidence,
    EvidenceSignal,
    EvidenceSignalKind,
)

class TestDroidPhase4ModelReasoning(unittest.TestCase):
    def setUp(self):
        self.engine = AndroidModelReasoningEngine()

    def test_model_hypothesis_alone_cannot_confirm(self):
        signals = [
            EvidenceSignal(
                kind=EvidenceSignalKind.MODEL_ADVISORY,
                description="Model thinks the bug is in MainActivity",
                is_authoritative=False,
                weight=0.5,
            )
        ]
        conf = self.engine.evaluate_confidence(signals)
        self.assertEqual(conf, EngineeringConfidence.POSSIBLE)

    def test_authoritative_runtime_and_source_confirms(self):
        signals = [
            EvidenceSignal(
                kind=EvidenceSignalKind.RUNTIME_LOGCAT,
                description="FATAL EXCEPTION at MainActivity.kt:38",
                is_authoritative=True,
                weight=1.5,
            ),
            EvidenceSignal(
                kind=EvidenceSignalKind.SOURCE_AST,
                description="Method divideByZero verified in MainActivity",
                is_authoritative=True,
                weight=1.0,
            ),
        ]
        conf = self.engine.evaluate_confidence(signals)
        self.assertEqual(conf, EngineeringConfidence.CONFIRMED)

    def test_converging_static_signals(self):
        signals = [
            EvidenceSignal(
                kind=EvidenceSignalKind.COMPOSE_SEMANTICS,
                description="CALLBACK_DISCONNECTED in MainScreen",
                is_authoritative=True,
                weight=1.0,
            ),
            EvidenceSignal(
                kind=EvidenceSignalKind.SOURCE_AST,
                description="Empty lambda detected in onClick",
                is_authoritative=True,
                weight=1.0,
            ),
        ]
        conf = self.engine.evaluate_confidence(signals)
        self.assertEqual(conf, EngineeringConfidence.STRONGLY_SUPPORTED)
