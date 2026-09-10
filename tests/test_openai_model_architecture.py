"""
Tests for NR-AI OpenAI Model Architecture.

Verifies:
1. Model configuration and specifications for:
   - GPT-6 Astra (gpt-6-astra)
   - GPT-5.6 Sol (gpt-5.6-sol)
   - GPT-5.6 Terra (gpt-5.6-terra)
   - GPT-5.6 Luna (gpt-5.6-luna)
2. Central routing policy (routine -> Luna, normal -> Terra, complex -> Sol, highest -> Astra)
3. Explicit model override capability
4. Dynamic configuration & credential handling (no hardcoded keys)
5. Fallback mechanisms when models or credentials encounter limits
6. Structural verification vs live API verification distinction
7. Integration with RecoveryEngine, CodeAgent, and NRBrain
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from app.agent.code_agent import CodeAgent
from app.agent.model_provider import OpenAIProvider
from app.agent.model_router import ModelRouter
from app.agent.recovery_engine import RecoveryEngine
from app.brain.brain import NRBrain
from app.config.model_config import (
    ALL_MODELS,
    GPT_5_6_LUNA,
    GPT_5_6_SOL,
    GPT_5_6_TERRA,
    GPT_6_ASTRA,
    MODEL_REGISTRY,
    TIER_COMPLEX,
    TIER_HIGHEST,
    TIER_NORMAL,
    TIER_ROUTINE,
    ModelConfig,
    ModelSpec,
)


class TestOpenAIModelArchitecture(unittest.TestCase):

    def setUp(self):
        self.config = ModelConfig(
            api_key="sk-test-mock-key-for-validation",
            api_base="https://api.openai.com/v1",
            default_model=GPT_5_6_TERRA,
        )
        self.provider = OpenAIProvider(config=self.config)
        self.router = ModelRouter(config=self.config, provider=self.provider)

    # -------------------------------------------------------------------------
    # 1. Model IDs and Specifications
    # -------------------------------------------------------------------------

    def test_model_identifiers_and_specs(self):
        """Verify exact model IDs and roles conform to system requirements."""
        self.assertEqual(GPT_6_ASTRA, "gpt-6-astra")
        self.assertEqual(GPT_5_6_SOL, "gpt-5.6-sol")
        self.assertEqual(GPT_5_6_TERRA, "gpt-5.6-terra")
        self.assertEqual(GPT_5_6_LUNA, "gpt-5.6-luna")

        # GPT-6 Astra
        astra = MODEL_REGISTRY[GPT_6_ASTRA]
        self.assertEqual(astra.model_id, "gpt-6-astra")
        self.assertEqual(astra.tier, TIER_HIGHEST)
        self.assertIn("highest intelligence", astra.role)
        self.assertIn("autonomous workflows", astra.role)

        # GPT-5.6 Sol
        sol = MODEL_REGISTRY[GPT_5_6_SOL]
        self.assertEqual(sol.model_id, "gpt-5.6-sol")
        self.assertEqual(sol.tier, TIER_COMPLEX)
        self.assertIn("complex professional tasks", sol.role)

        # GPT-5.6 Terra
        terra = MODEL_REGISTRY[GPT_5_6_TERRA]
        self.assertEqual(terra.model_id, "gpt-5.6-terra")
        self.assertEqual(terra.tier, TIER_NORMAL)
        self.assertIn("balanced intelligence and cost", terra.role)

        # GPT-5.6 Luna
        luna = MODEL_REGISTRY[GPT_5_6_LUNA]
        self.assertEqual(luna.model_id, "gpt-5.6-luna")
        self.assertEqual(luna.tier, TIER_ROUTINE)
        self.assertIn("fast, high-volume, cost-sensitive", luna.role)

    # -------------------------------------------------------------------------
    # 2. Configuration & Environment Handling (No hardcoding)
    # -------------------------------------------------------------------------

    def test_configuration_no_hardcoded_secrets(self):
        """Verify credentials are not hardcoded and resolve dynamically from env."""
        with patch.dict(os.environ, {}, clear=True):
            clean_config = ModelConfig.from_env()
            self.assertIsNone(clean_config.api_key)
            self.assertFalse(clean_config.has_credentials())

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-proj-dynamic-test-key"}):
            env_config = ModelConfig.from_env()
            self.assertEqual(env_config.api_key, "sk-proj-dynamic-test-key")
            self.assertTrue(env_config.has_credentials())

    # -------------------------------------------------------------------------
    # 3. Model Routing Policy
    # -------------------------------------------------------------------------

    def test_routing_policy_by_task_tier(self):
        """Verify routing policy by task type parameter."""
        self.assertEqual(self.router.route(task_type="routine"), GPT_5_6_LUNA)
        self.assertEqual(self.router.route(task_type="simple"), GPT_5_6_LUNA)
        self.assertEqual(self.router.route(task_type="normal"), GPT_5_6_TERRA)
        self.assertEqual(self.router.route(task_type="complex"), GPT_5_6_SOL)
        self.assertEqual(self.router.route(task_type="highest"), GPT_6_ASTRA)
        self.assertEqual(self.router.route(task_type="architecture"), GPT_6_ASTRA)
        self.assertEqual(self.router.route(task_type="debugging"), GPT_6_ASTRA)

    def test_routing_policy_by_prompt_classification(self):
        """Verify automatic prompt classification adheres to policy."""
        # Routine -> Luna
        self.assertEqual(self.router.route(prompt="What time is it?"), GPT_5_6_LUNA)
        self.assertEqual(self.router.route(prompt="Hello NR AI"), GPT_5_6_LUNA)
        self.assertEqual(self.router.route(prompt="Show project status"), GPT_5_6_LUNA)

        # Normal -> Terra
        self.assertEqual(
            self.router.route(prompt="Read contents of lines.txt and print line count"),
            GPT_5_6_TERRA,
        )

        # Complex -> Sol
        self.assertEqual(
            self.router.route(prompt="Implement Dijkstra shortest path algorithm in Python"),
            GPT_5_6_SOL,
        )
        self.assertEqual(
            self.router.route(prompt="Create a Spring Boot REST controller with authentication"),
            GPT_5_6_SOL,
        )

        # Highest -> Astra
        self.assertEqual(
            self.router.route(prompt="Architect autonomous development lifecycle and system design"),
            GPT_6_ASTRA,
        )
        self.assertEqual(
            self.router.route(prompt="Diagnose CS0103 compiler diagnostics and execute deep debugging"),
            GPT_6_ASTRA,
        )
        self.assertEqual(
            self.router.route(prompt="Self-healing recovery loop with multi-step plan execution"),
            GPT_6_ASTRA,
        )

    # -------------------------------------------------------------------------
    # 4. Explicit Model Override
    # -------------------------------------------------------------------------

    def test_explicit_model_override(self):
        """Verify the router honors explicit model override regardless of prompt."""
        # Force Luna on complex architecture task
        routed = self.router.route(
            prompt="Architect autonomous lifecycle with deep debugging",
            explicit_model=GPT_5_6_LUNA,
        )
        self.assertEqual(routed, GPT_5_6_LUNA)

        # Force Astra on simple routine task
        routed = self.router.route(
            prompt="What time is it?",
            explicit_model=GPT_6_ASTRA,
        )
        self.assertEqual(routed, GPT_6_ASTRA)

        # Force via alias
        self.assertEqual(self.router.route(explicit_model="astra"), GPT_6_ASTRA)
        self.assertEqual(self.router.route(explicit_model="sol"), GPT_5_6_SOL)
        self.assertEqual(self.router.route(explicit_model="terra"), GPT_5_6_TERRA)
        self.assertEqual(self.router.route(explicit_model="luna"), GPT_5_6_LUNA)

    # -------------------------------------------------------------------------
    # 5. Fallback Behavior
    # -------------------------------------------------------------------------

    def test_fallback_ladder(self):
        """Verify structured degradation path across the model hierarchy."""
        self.assertEqual(self.router.fallback_for(GPT_6_ASTRA), GPT_5_6_SOL)
        self.assertEqual(self.router.fallback_for(GPT_5_6_SOL), GPT_5_6_TERRA)
        self.assertEqual(self.router.fallback_for(GPT_5_6_TERRA), GPT_5_6_LUNA)
        self.assertEqual(self.router.fallback_for(GPT_5_6_LUNA), GPT_5_6_LUNA)

    def test_provider_fallback_execution(self):
        """Verify provider gracefully tries fallback models on rate limits or failures."""
        calls = []

        def mock_completion(model, messages, **kwargs):
            calls.append(model)
            if model == GPT_6_ASTRA:
                # Simulate 429 Too Many Requests / Quota on Astra
                return {
                    "success": False,
                    "model": model,
                    "status_code": 429,
                    "error": "HTTP 429: insufficient_quota",
                }
            # Fallback model succeeds
            return {
                "success": True,
                "model": model,
                "content": "Recovered response from fallback",
                "finish_reason": "stop",
                "usage": {},
            }

        with patch.object(self.provider, "_execute_chat_completion", side_effect=mock_completion):
            res = self.provider.generate(
                prompt="Complex architecture request",
                model=GPT_6_ASTRA,
                allow_fallback=True,
            )
            self.assertTrue(res["success"])
            self.assertTrue(res["fallback_used"])
            self.assertEqual(res["original_requested_model"], GPT_6_ASTRA)
            self.assertIn(GPT_6_ASTRA, calls)
            self.assertIn(GPT_5_6_SOL, calls)

    # -------------------------------------------------------------------------
    # 6. Structural vs Live API Distinction
    # -------------------------------------------------------------------------

    def test_structural_vs_live_verification_distinction(self):
        """Verify verify_live_api distinguishes configured from live verified."""
        # 1. No credentials
        empty_provider = OpenAIProvider(config=ModelConfig(api_key=None))
        res_no_creds = empty_provider.verify_live_api(GPT_6_ASTRA)
        self.assertTrue(res_no_creds["configured"])
        self.assertFalse(res_no_creds["has_credentials"])
        self.assertFalse(res_no_creds["live_api_verified"])
        self.assertEqual(res_no_creds["status"], "NO_CREDENTIALS")

        # 2. Authentic credentials with 429 credit exhaustion
        with patch.object(
            self.provider,
            "_execute_chat_completion",
            return_value={
                "success": False,
                "status_code": 429,
                "error": "HTTP 429: credit_balance_exhausted",
            },
        ):
            res_quota = self.provider.verify_live_api(GPT_6_ASTRA)
            self.assertTrue(res_quota["configured"])
            self.assertTrue(res_quota["has_credentials"])
            self.assertFalse(res_quota["live_api_verified"])
            self.assertEqual(res_quota["status"], "CREDENTIALS_EXHAUSTED_OR_RATE_LIMITED")

        # 3. Successful live response
        with patch.object(
            self.provider,
            "_execute_chat_completion",
            return_value={
                "success": True,
                "content": "pong",
                "latency_s": 0.35,
            },
        ):
            res_live = self.provider.verify_live_api(GPT_6_ASTRA)
            self.assertTrue(res_live["configured"])
            self.assertTrue(res_live["has_credentials"])
            self.assertTrue(res_live["live_api_verified"])
            self.assertEqual(res_live["status"], "LIVE_VERIFIED")

    # -------------------------------------------------------------------------
    # 7. Integration with RecoveryEngine, CodeAgent, and Brain
    # -------------------------------------------------------------------------

    def test_recovery_engine_with_openai_reasoner(self):
        """Verify RecoveryEngine attaches Astra reasoning with rule-based fallback."""
        recovery = RecoveryEngine()

        # Mock AI reasoning success
        mock_provider = MagicMock()
        mock_provider.is_available.return_value = True
        mock_provider.generate.return_value = {
            "success": True,
            "content": "def add(x, y):\n    return x + y\n",
        }

        recovery.attach_openai_reasoner(provider=mock_provider, model=GPT_6_ASTRA)
        err_info = {"type": "SyntaxError", "line": 1, "message": "invalid syntax", "language": "python"}
        fix = recovery.generate_fix(err_info, "def add(x, y)\n    return x + y\n")

        self.assertTrue(fix["success"])
        self.assertEqual(fix["strategy"], "ai_reasoning")
        self.assertEqual(fix["fixed_code"], "def add(x, y):\n    return x + y\n")

        # Mock AI reasoning failure -> graceful fallback to rule_based
        mock_provider.generate.return_value = {"success": False, "error": "Quota error"}
        fix_fallback = recovery.generate_fix(err_info, "def add(x, y)\n    return x + y\n")
        self.assertTrue(fix_fallback["success"])
        self.assertEqual(fix_fallback["strategy"], "rule_based")

    def test_code_agent_and_brain_initialization(self):
        """Verify CodeAgent and NRBrain initialize cleanly with router/provider."""
        agent = CodeAgent(provider=self.provider, router=self.router)
        self.assertIsNotNone(agent.provider)
        self.assertIsNotNone(agent.router)

        brain = NRBrain()
        self.assertIsNotNone(brain.provider)
        self.assertIsNotNone(brain.router)
        self.assertEqual(brain.router.get_model_spec(GPT_6_ASTRA).tier, TIER_HIGHEST)


if __name__ == "__main__":
    unittest.main()
