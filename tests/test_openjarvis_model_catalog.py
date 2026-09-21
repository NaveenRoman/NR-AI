"""
Dedicated Unit & Integration Tests: Model Catalog Intelligence & Compatibility Scorer.
Tests provenance tracking, hardware detection, and compatibility evaluation.
"""

import unittest
from app.config.model_catalog import (
    AvailabilityStatus,
    CatalogModelMetadata,
    HardwareProbe,
    HardwareSpecs,
    ModelCatalogRegistry,
    ModelCompatibilityEvaluator,
    ProvenanceEnum,
    ProvenanceField,
    QuantizationType,
    global_model_catalog,
)
from app.agent.model_router import ModelRouter


class TestModelCatalogIntelligence(unittest.TestCase):

    def setUp(self):
        self.catalog = ModelCatalogRegistry()
        self.evaluator = ModelCompatibilityEvaluator(self.catalog)

    def test_provenance_tracking_integrity(self):
        """Every field in metadata must contain explicit provenance."""
        meta = self.catalog.get("gpt-6-astra")
        self.assertIsNotNone(meta)
        self.assertIsInstance(meta.display_name.provenance, ProvenanceEnum)
        self.assertEqual(meta.display_name.provenance, ProvenanceEnum.CONFIGURED)
        self.assertEqual(meta.display_name.value, "GPT-6 Astra")

        # Unknown provenance must not be hallucinated
        self.assertEqual(meta.parameter_count_b.provenance, ProvenanceEnum.UNKNOWN)
        self.assertIsNone(meta.parameter_count_b.value)

    def test_provider_reported_provenance(self):
        """Official provider specs must be labeled PROVIDER_REPORTED."""
        gemini = self.catalog.get("gemini-3.7-flash")
        self.assertIsNotNone(gemini)
        self.assertEqual(gemini.context_length.provenance, ProvenanceEnum.PROVIDER_REPORTED)
        self.assertEqual(gemini.context_length.value, 1048576)

    def test_verified_hardware_provenance(self):
        """Empirically verified local model limits must be marked VERIFIED."""
        qwen = self.catalog.get("qwen2.5-coder-7b")
        self.assertIsNotNone(qwen)
        self.assertEqual(qwen.vram_requirement_mb.provenance, ProvenanceEnum.VERIFIED)
        self.assertEqual(qwen.vram_requirement_mb.value, 5500)

    def test_hardware_probe_live_detection(self):
        """HardwareProbe must return non-zero real RAM and CPU cores."""
        hw = HardwareProbe.probe()
        self.assertGreater(hw.total_ram_mb, 1024)
        self.assertGreater(hw.cpu_cores_logical, 0)
        self.assertIsInstance(hw.cpu_name, str)

    def test_compatibility_cloud_models(self):
        """Cloud models should evaluate compatible for routine tasks regardless of local GPU."""
        hw = HardwareSpecs(
            total_ram_mb=4096,
            available_ram_mb=2048,
            cpu_cores_physical=2,
            cpu_cores_logical=2,
            cpu_name="Mock CPU",
            gpu_available=False,
            vram_total_mb=0,
            vram_available_mb=0,
        )
        res = self.evaluator.evaluate("gpt-5.6-terra", hardware=hw)
        self.assertTrue(res.is_compatible)
        self.assertGreaterEqual(res.score, 0.8)

    def test_compatibility_local_gpu_model_on_cpu_host(self):
        """A local model requiring more RAM than available must fail compatibility."""
        hw = HardwareSpecs(
            total_ram_mb=8192,
            available_ram_mb=4000,  # Less than the 9000MB needed for CPU Qwen
            cpu_cores_physical=4,
            cpu_cores_logical=8,
            cpu_name="Mock CPU",
            gpu_available=False,
            vram_total_mb=0,
            vram_available_mb=0,
        )
        res = self.evaluator.evaluate("qwen2.5-coder-7b", hardware=hw)
        self.assertFalse(res.is_compatible)
        self.assertFalse(res.hardware_sufficient)
        self.assertIsNotNone(res.suggested_alternative)

    def test_compatibility_missing_capability(self):
        """Evaluation must penalize missing capabilities."""
        # gpt-5.6-luna does not have DEFENSIVE_SECURITY
        res = self.evaluator.evaluate("gpt-5.6-luna", required_capabilities={"DEFENSIVE_SECURITY"})
        self.assertFalse(res.capability_sufficient)

    def test_unknown_model_handling(self):
        """Unknown model ID must return non-compatible with safe fallback."""
        res = self.evaluator.evaluate("non-existent-super-model-9000")
        self.assertFalse(res.is_compatible)
        self.assertEqual(res.score, 0.0)
        self.assertIn("unknown", res.reasons[0].lower())

    def test_model_router_advisory_integration(self):
        """ModelRouter must expose evaluate_model_compatibility."""
        router = ModelRouter()
        res = router.evaluate_model_compatibility("gpt-6-astra", required_capabilities=["CODING"])
        self.assertTrue(res.is_compatible)


if __name__ == "__main__":
    unittest.main()
