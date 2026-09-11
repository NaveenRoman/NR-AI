"""
Step 5 Phase 1 Test Suite — Multi-Model Intelligence Foundation.

Covers tests A through P:
A. Capability enum validation
B. Model registry validation
C. Disabled model rejection
D. Capability matching
E. Explicit model override
F. Coding routing
G. Vision routing
H. Web + reasoning routing
I. Verification routing
J. Provider fallback
K. HTTP 429 fallback
L. Timeout fallback
M. No-capability failure
N. No direct OS execution
O. ConsensusEngine integration
P. Step 4E regression

All tests use mocks. No real paid APIs or external network calls are performed.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from app.agent.consensus_engine import (
    ConsensusDecision,
    ConsensusEngine,
    DecisionCase,
    ExecutionEvidence,
    VerificationReview,
)
from app.agent.model_provider import (
    GeminiProvider,
    OpenAIProvider,
    UnifiedModelProvider,
)
from app.agent.model_router import (
    CapabilityUnavailableError,
    ModelRouter,
)
from app.config.model_config import (
    ALL_LOCAL_MODELS,
    ALL_MODELS,
    GEMINI_3_6_FLASH,
    GEMINI_3_7_FLASH,
    GEMINI_FLASH_LATEST,
    GPT_5_6_LUNA,
    GPT_5_6_SOL,
    GPT_5_6_TERRA,
    GPT_6_ASTRA,
    MODEL_REGISTRY,
    PROVIDER_GOOGLE,
    PROVIDER_LLAMACPP,
    PROVIDER_OLLAMA,
    PROVIDER_OPENAI,
    PROVIDER_VLLM,
    QWEN_2_5_CODER_7B,
    TIER_COMPLEX,
    TIER_HIGHEST,
    TIER_NORMAL,
    TIER_ROUTINE,
    ModelCapability,
    ModelConfig,
    ModelSpec,
)


class TestStep5Phase1Models(unittest.TestCase):
    """Test suite for Step 5 Phase 1 Multi-Model Intelligence Foundation."""

    def setUp(self):
        # Create test config with mock credentials
        self.config = ModelConfig(
            api_key="mock-openai-key",
            gemini_api_key="mock-gemini-key",
            default_model=GPT_5_6_TERRA,
            gemini_default_model=GEMINI_3_6_FLASH,
            timeout=10.0,
            max_retries=2,
        )
        self.router = ModelRouter(config=self.config)

    # -------------------------------------------------------------------------
    # Test A: Capability enum validation
    # -------------------------------------------------------------------------
    def test_A_capability_enum_validation(self):
        expected_caps = {
            "GENERAL",
            "CODING",
            "REASONING",
            "VISION",
            "WEB",
            "RESEARCH",
            "MATH",
            "DEFENSIVE_SECURITY",
            "VERIFICATION",
        }
        actual_caps = {c.value for c in ModelCapability}
        self.assertEqual(actual_caps, expected_caps, "All 9 standard capabilities must be present.")
        for name in expected_caps:
            cap = getattr(ModelCapability, name)
            self.assertEqual(cap.value, name)

    # -------------------------------------------------------------------------
    # Test B: Model registry validation
    # -------------------------------------------------------------------------
    def test_B_model_registry_validation(self):
        self.assertGreaterEqual(len(MODEL_REGISTRY), 10, "Registry must contain cloud and local models.")
        for model_id, spec in MODEL_REGISTRY.items():
            self.assertEqual(model_id, spec.model_id)
            self.assertTrue(spec.display_name)
            self.assertIn(spec.provider, {PROVIDER_OPENAI, PROVIDER_GOOGLE, PROVIDER_OLLAMA, PROVIDER_LLAMACPP, PROVIDER_VLLM})
            self.assertIsInstance(spec.capabilities, set)
            for c in spec.capabilities:
                self.assertIsInstance(c, ModelCapability)
            self.assertGreater(spec.context_window, 0)
            self.assertIsInstance(spec.supports_vision, bool)
            self.assertIsInstance(spec.supports_function_calling, bool)
            self.assertIsInstance(spec.supports_reasoning, bool)
            self.assertIn(spec.cost_tier, {"low", "balanced", "high", "medium", "premium"})
            self.assertIn(spec.latency_tier, {"fast", "normal", "slow"})
            self.assertIsInstance(spec.enabled, bool)
            self.assertTrue(0.0 <= spec.reliability_score <= 1.0)
            self.assertIn(spec.local_or_cloud, {"cloud", "local"})

        # Verify local model placeholders are disabled by default
        for loc_id in ALL_LOCAL_MODELS:
            self.assertIn(loc_id, MODEL_REGISTRY)
            loc_spec = MODEL_REGISTRY[loc_id]
            self.assertFalse(loc_spec.enabled, f"Local model {loc_id} must be disabled until configured.")
            self.assertEqual(loc_spec.local_or_cloud, "local")

    # -------------------------------------------------------------------------
    # Test C: Disabled model rejection
    # -------------------------------------------------------------------------
    def test_C_disabled_model_rejection(self):
        # Disabled models must NOT be returned in standard capability matching
        models = self.router.find_models_for_capabilities([ModelCapability.CODING], include_disabled=False)
        for m in models:
            self.assertTrue(m.enabled)
            self.assertNotIn(m.model_id, ALL_LOCAL_MODELS)

        # Explicitly requesting a disabled model must raise CapabilityUnavailableError
        with self.assertRaises(CapabilityUnavailableError) as ctx:
            self.router.route(explicit_model=QWEN_2_5_CODER_7B)
        self.assertIn("disabled", str(ctx.exception).lower())

        with self.assertRaises(CapabilityUnavailableError):
            self.router.route_with_capabilities(explicit_model="qwen-coder")

    # -------------------------------------------------------------------------
    # Test D: Capability matching
    # -------------------------------------------------------------------------
    def test_D_capability_matching(self):
        # Query for models supporting both CODING and VISION
        matches = self.router.find_models_for_capabilities(
            [ModelCapability.CODING, ModelCapability.VISION],
            include_disabled=False,
        )
        self.assertGreater(len(matches), 0)
        for m in matches:
            self.assertIn(ModelCapability.CODING, m.capabilities)
            self.assertIn(ModelCapability.VISION, m.capabilities)
            self.assertTrue(m.enabled)

        # Query using string aliases
        str_matches = self.router.find_models_for_capabilities(["coding", "vision"], include_disabled=False)
        self.assertEqual(len(matches), len(str_matches))

    # -------------------------------------------------------------------------
    # Test E: Explicit model override
    # -------------------------------------------------------------------------
    def test_E_explicit_model_override(self):
        # Override with full ID
        self.assertEqual(self.router.route(explicit_model=GPT_6_ASTRA), GPT_6_ASTRA)
        # Override with alias
        self.assertEqual(self.router.route(explicit_model="astra"), GPT_6_ASTRA)
        self.assertEqual(self.router.route(explicit_model="sol"), GPT_5_6_SOL)
        self.assertEqual(self.router.route(explicit_model="gemini"), GEMINI_FLASH_LATEST)

    # -------------------------------------------------------------------------
    # Test F: Coding routing
    # -------------------------------------------------------------------------
    def test_F_coding_routing(self):
        routed = self.router.route(required_capabilities=[ModelCapability.CODING])
        spec = MODEL_REGISTRY[routed]
        self.assertTrue(spec.enabled)
        self.assertIn(ModelCapability.CODING, spec.capabilities)

        # Inferred from prompt
        inferred = self.router.infer_required_capabilities(prompt="Please implement a fast sorting algorithm in python")
        self.assertIn(ModelCapability.CODING, inferred)

    # -------------------------------------------------------------------------
    # Test G: Vision routing
    # -------------------------------------------------------------------------
    def test_G_vision_routing(self):
        routed = self.router.route(required_capabilities=[ModelCapability.VISION])
        spec = MODEL_REGISTRY[routed]
        self.assertTrue(spec.enabled)
        self.assertIn(ModelCapability.VISION, spec.capabilities)
        self.assertTrue(spec.supports_vision)

        # Inferred from prompt
        inferred = self.router.infer_required_capabilities(prompt="Inspect the screen and tell me what window is open")
        self.assertIn(ModelCapability.VISION, inferred)

    # -------------------------------------------------------------------------
    # Test H: Web + reasoning routing
    # -------------------------------------------------------------------------
    def test_H_web_reasoning_routing(self):
        routed = self.router.route(required_capabilities=[ModelCapability.WEB, ModelCapability.REASONING])
        spec = MODEL_REGISTRY[routed]
        self.assertTrue(spec.enabled)
        self.assertIn(ModelCapability.WEB, spec.capabilities)
        self.assertIn(ModelCapability.REASONING, spec.capabilities)

        # Inferred from prompt
        inferred = self.router.infer_required_capabilities(prompt="Open the browser and navigate to the webpage")
        self.assertIn(ModelCapability.WEB, inferred)
        self.assertIn(ModelCapability.REASONING, inferred)

    # -------------------------------------------------------------------------
    # Test I: Verification routing
    # -------------------------------------------------------------------------
    def test_I_verification_routing(self):
        routed = self.router.route(required_capabilities=[ModelCapability.VERIFICATION])
        spec = MODEL_REGISTRY[routed]
        self.assertTrue(spec.enabled)
        self.assertIn(ModelCapability.VERIFICATION, spec.capabilities)

        # Inferred from prompt
        inferred = self.router.infer_required_capabilities(prompt="Verify the test results and review the evidence")
        self.assertIn(ModelCapability.VERIFICATION, inferred)

    # -------------------------------------------------------------------------
    # Test J: Provider fallback
    # -------------------------------------------------------------------------
    def test_J_provider_fallback(self):
        mock_provider = MagicMock()
        # Primary returns failure, secondary returns success
        mock_provider.openai.is_available.return_value = True
        mock_provider.gemini.is_available.return_value = True
        mock_provider.openai.generate.return_value = {
            "success": False,
            "error": "Server error",
            "status_code": 500,
        }
        mock_provider.gemini.generate.return_value = {
            "success": True,
            "content": "Fallback response from Gemini",
            "status_code": 200,
        }

        unified = UnifiedModelProvider(config=self.config)
        unified.openai = mock_provider.openai
        unified.gemini = mock_provider.gemini

        res = unified.generate(prompt="test fallback", model=GPT_5_6_SOL, allow_fallback=True)
        self.assertTrue(res["success"])
        self.assertTrue(res.get("fallback_used"))
        self.assertEqual(res["provider"], "Google Gemini")
        self.assertEqual(res["content"], "Fallback response from Gemini")

    # -------------------------------------------------------------------------
    # Test K: HTTP 429 fallback
    # -------------------------------------------------------------------------
    def test_K_http_429_fallback(self):
        mock_provider = MagicMock()
        mock_provider.openai.is_available.return_value = True
        mock_provider.gemini.is_available.return_value = True
        # OpenAI returns 429 rate limit
        mock_provider.openai.generate.return_value = {
            "success": False,
            "error": "HTTP 429: Rate limit exceeded or quota exhausted",
            "status_code": 429,
        }
        mock_provider.gemini.generate.return_value = {
            "success": True,
            "content": "Gemini response after 429",
            "status_code": 200,
        }

        unified = UnifiedModelProvider(config=self.config)
        unified.openai = mock_provider.openai
        unified.gemini = mock_provider.gemini

        res = unified.generate(prompt="test rate limit", model=GPT_5_6_SOL, allow_fallback=True)
        self.assertTrue(res["success"])
        self.assertTrue(res.get("fallback_used"))
        self.assertIn("openai_error", res)
        self.assertEqual(res["status_code"], 200)

    # -------------------------------------------------------------------------
    # Test L: Timeout fallback
    # -------------------------------------------------------------------------
    def test_L_timeout_fallback(self):
        mock_provider = MagicMock()
        mock_provider.openai.is_available.return_value = True
        mock_provider.gemini.is_available.return_value = True
        # OpenAI returns timeout
        mock_provider.openai.generate.return_value = {
            "success": False,
            "error": "Connection timed out after 10.0s",
            "status_code": None,
        }
        mock_provider.gemini.generate.return_value = {
            "success": True,
            "content": "Gemini response after timeout",
            "status_code": 200,
        }

        unified = UnifiedModelProvider(config=self.config)
        unified.openai = mock_provider.openai
        unified.gemini = mock_provider.gemini

        res = unified.generate(prompt="test timeout", model=GPT_5_6_SOL, allow_fallback=True)
        self.assertTrue(res["success"])
        self.assertTrue(res.get("fallback_used"))
        self.assertEqual(res["content"], "Gemini response after timeout")

    # -------------------------------------------------------------------------
    # Test M: No-capability failure
    # -------------------------------------------------------------------------
    def test_M_no_capability_failure(self):
        # Create a mock router where no models match
        mock_router = ModelRouter(config=self.config)
        mock_router.find_models_for_capabilities = MagicMock(return_value=[])

        with self.assertRaises(CapabilityUnavailableError):
            mock_router.route_with_capabilities(required_capabilities=[ModelCapability.CODING])

        # Execute should return structured CAPABILITY_UNAVAILABLE dictionary
        res = mock_router.execute(prompt="hello", required_capabilities=[ModelCapability.CODING])
        self.assertFalse(res["success"])
        self.assertTrue(res.get("capability_unavailable"))
        self.assertEqual(res.get("status_code"), 404)
        self.assertIn("CAPABILITY_UNAVAILABLE", res.get("error", ""))

    # -------------------------------------------------------------------------
    # Test N: No direct OS execution
    # -------------------------------------------------------------------------
    def test_N_no_direct_os_execution(self):
        # Inspect ModelRouter to verify it has NO OS execution capabilities
        prohibited_methods = [
            "execute_command",
            "subprocess",
            "os_system",
            "click",
            "type_text",
            "press_key",
            "move_mouse",
            "launch_app",
            "kill_process",
        ]
        for attr in prohibited_methods:
            self.assertFalse(hasattr(self.router, attr), f"ModelRouter must not have OS control method: {attr}")

        # Ensure ModelRouter only passes calls to provider
        mock_provider = MagicMock()
        mock_provider.generate.return_value = {"success": True, "content": "Advisory plan only"}
        router_with_mock = ModelRouter(config=self.config, provider=mock_provider)
        res = router_with_mock.execute(prompt="Write a plan to sort a list")
        self.assertTrue(res["success"])
        mock_provider.generate.assert_called_once()

    # -------------------------------------------------------------------------
    # Test O: ConsensusEngine integration (Case D overrides LLM claims)
    # -------------------------------------------------------------------------
    def test_O_consensus_engine_integration(self):
        engine = ConsensusEngine()

        # Both LLM verifiers claim PASS
        primary = VerificationReview(
            slot_id=6,
            verifier_name="CodeReviewer-OpenAI",
            model_id=GPT_5_6_SOL,
            passed=True,
            confidence=0.98,
            notes="LLM claims code is perfect.",
        )
        secondary = VerificationReview(
            slot_id=5,
            verifier_name="Verifier-Gemini",
            model_id=GEMINI_3_6_FLASH,
            passed=True,
            confidence=0.95,
            notes="LLM verifier claims all tests should pass.",
        )

        # Scenario 1: Real execution evidence says FAILED (exit code 1)
        failing_evidence = ExecutionEvidence(
            command="pytest tests/",
            exit_code=1,
            stdout="",
            stderr="AssertionError: Expected True but got False",
            tests_failed=1,
        )

        report = engine.evaluate(primary_review=primary, secondary_review=secondary, evidence=failing_evidence)
        self.assertEqual(report.decision, ConsensusDecision.REJECT)
        self.assertEqual(report.case, DecisionCase.CASE_D)
        self.assertIn("Real execution evidence failed", report.summary)

        # Scenario 2: Real execution evidence says SUCCESS (exit code 0, 0 failed)
        passing_evidence = ExecutionEvidence(
            command="pytest tests/",
            exit_code=0,
            stdout="10 passed in 0.5s",
            stderr="",
            tests_passed=10,
            tests_failed=0,
        )
        pass_report = engine.evaluate(primary_review=primary, secondary_review=secondary, evidence=passing_evidence)
        self.assertEqual(pass_report.decision, ConsensusDecision.ACCEPT)
        self.assertEqual(pass_report.case, DecisionCase.CASE_A)

    # -------------------------------------------------------------------------
    # Test P: Step 4E regression baseline
    # -------------------------------------------------------------------------
    def test_P_step4e_regression(self):
        # Confirm that Step 4E Unified Computer Agent classes and tools remain completely importable
        from app.agent.computer_agent import UnifiedComputerAgent
        from app.agent.computer_tools import ComputerToolRegistry
        from app.agent.input_controller import InputController
        from app.agent.window_manager import WindowManager
        from app.commands.app_launcher import AppLauncher
        from app.vision.vision_service import ScreenVisionService

        registry = ComputerToolRegistry()
        tools = registry.list_tools()
        self.assertEqual(len(tools), 12, "All 12 Step 4E computer tools must remain registered.")
        tool_names = [t["name"] for t in tools]
        self.assertIn("computer.click_target", tool_names)
        self.assertIn("computer.inspect_screen", tool_names)


if __name__ == "__main__":
    unittest.main()
