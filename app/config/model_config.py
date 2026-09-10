"""
NR AI Model Configuration & Registry.

Defines the supported OpenAI model architecture:
- GPT-6 Astra (gpt-6-astra): Highest intelligence / difficult reasoning / complex coding / autonomous workflows
- GPT-5.6 Sol (gpt-5.6-sol): Complex professional tasks
- GPT-5.6 Terra (gpt-5.6-terra): Balanced intelligence and cost
- GPT-5.6 Luna (gpt-5.6-luna): Fast, high-volume, cost-sensitive tasks
"""

from dataclasses import dataclass, field
import os
from typing import Any, Dict, List, Optional


# =============================================================================
# MODEL IDENTIFIERS
# =============================================================================
GPT_6_ASTRA = "gpt-6-astra"
GPT_5_6_SOL = "gpt-5.6-sol"
GPT_5_6_TERRA = "gpt-5.6-terra"
GPT_5_6_LUNA = "gpt-5.6-luna"

# Google / Gemini Model Identifiers
GEMINI_FLASH_LATEST = "gemini-flash-latest"
GEMINI_3_7_FLASH = "gemini-3.7-flash"
GEMINI_3_6_FLASH = "gemini-3.6-flash"
GEMINI_3_5_FLASH = "gemini-3.5-flash"
GEMINI_3_5_FLASH_LITE = "gemini-3.5-flash-lite"
GEMINI_2_5_FLASH = "gemini-2.5-flash"
GEMINI_1_5_FLASH = "gemini-1.5-flash"
GEMINI_1_5_PRO = "gemini-1.5-pro"

PROVIDER_OPENAI = "openai"
PROVIDER_GOOGLE = "google"

ALL_OPENAI_MODELS = [
    GPT_6_ASTRA,
    GPT_5_6_SOL,
    GPT_5_6_TERRA,
    GPT_5_6_LUNA,
]

ALL_GEMINI_MODELS = [
    GEMINI_FLASH_LATEST,
    GEMINI_3_7_FLASH,
    GEMINI_3_6_FLASH,
    GEMINI_3_5_FLASH,
    GEMINI_3_5_FLASH_LITE,
    GEMINI_2_5_FLASH,
    GEMINI_1_5_FLASH,
    GEMINI_1_5_PRO,
]

ALL_MODELS = ALL_OPENAI_MODELS + ALL_GEMINI_MODELS


# =============================================================================
# TASK TIERS
# =============================================================================
TIER_ROUTINE = "routine"      # Simple, high-volume, cost-sensitive tasks -> Luna
TIER_NORMAL = "normal"        # Standard coding, file inspection, basic edits -> Terra
TIER_COMPLEX = "complex"      # Complex reasoning, professional tasks -> Sol
TIER_HIGHEST = "highest"      # Architecture, deep debugging, autonomous loops -> Astra


@dataclass
class ModelSpec:
    """Specification and capabilities of an AI model."""

    model_id: str
    display_name: str
    role: str
    tier: str
    provider: str = PROVIDER_OPENAI
    context_window: int = 128000
    supports_vision: bool = True
    supports_function_calling: bool = True
    supports_reasoning: bool = True
    cost_tier: str = "medium"  # low, balanced, high, premium


# Central Model Registry
MODEL_REGISTRY: Dict[str, ModelSpec] = {
    GPT_6_ASTRA: ModelSpec(
        model_id=GPT_6_ASTRA,
        display_name="GPT-6 Astra",
        role="highest intelligence / difficult reasoning / complex coding / autonomous workflows",
        tier=TIER_HIGHEST,
        provider=PROVIDER_OPENAI,
        context_window=200000,
        supports_vision=True,
        supports_function_calling=True,
        supports_reasoning=True,
        cost_tier="premium",
    ),
    GPT_5_6_SOL: ModelSpec(
        model_id=GPT_5_6_SOL,
        display_name="GPT-5.6 Sol",
        role="complex professional tasks",
        tier=TIER_COMPLEX,
        provider=PROVIDER_OPENAI,
        context_window=128000,
        supports_vision=True,
        supports_function_calling=True,
        supports_reasoning=True,
        cost_tier="high",
    ),
    GPT_5_6_TERRA: ModelSpec(
        model_id=GPT_5_6_TERRA,
        display_name="GPT-5.6 Terra",
        role="balanced intelligence and cost",
        tier=TIER_NORMAL,
        provider=PROVIDER_OPENAI,
        context_window=128000,
        supports_vision=True,
        supports_function_calling=True,
        supports_reasoning=True,
        cost_tier="balanced",
    ),
    GPT_5_6_LUNA: ModelSpec(
        model_id=GPT_5_6_LUNA,
        display_name="GPT-5.6 Luna",
        role="fast, high-volume, cost-sensitive tasks",
        tier=TIER_ROUTINE,
        provider=PROVIDER_OPENAI,
        context_window=128000,
        supports_vision=True,
        supports_function_calling=True,
        supports_reasoning=False,
        cost_tier="low",
    ),
    GEMINI_FLASH_LATEST: ModelSpec(
        model_id=GEMINI_FLASH_LATEST,
        display_name="Gemini Flash Latest",
        role="latest production flash model for rapid verification and synthesis",
        tier=TIER_NORMAL,
        provider=PROVIDER_GOOGLE,
        context_window=1000000,
        supports_vision=True,
        supports_function_calling=True,
        supports_reasoning=True,
        cost_tier="low",
    ),
    GEMINI_3_7_FLASH: ModelSpec(
        model_id=GEMINI_3_7_FLASH,
        display_name="Gemini 3.7 Flash",
        role="high-speed reasoning and deep verification",
        tier=TIER_COMPLEX,
        provider=PROVIDER_GOOGLE,
        context_window=1000000,
        supports_vision=True,
        supports_function_calling=True,
        supports_reasoning=True,
        cost_tier="low",
    ),
    GEMINI_3_6_FLASH: ModelSpec(
        model_id=GEMINI_3_6_FLASH,
        display_name="Gemini 3.6 Flash",
        role="independent verification and multi-modal evidence review",
        tier=TIER_NORMAL,
        provider=PROVIDER_GOOGLE,
        context_window=1000000,
        supports_vision=True,
        supports_function_calling=True,
        supports_reasoning=True,
        cost_tier="low",
    ),
    GEMINI_3_5_FLASH: ModelSpec(
        model_id=GEMINI_3_5_FLASH,
        display_name="Gemini 3.5 Flash",
        role="fallback independent verification and fast review",
        tier=TIER_NORMAL,
        provider=PROVIDER_GOOGLE,
        context_window=1000000,
        supports_vision=True,
        supports_function_calling=True,
        supports_reasoning=True,
        cost_tier="low",
    ),
    GEMINI_2_5_FLASH: ModelSpec(
        model_id=GEMINI_2_5_FLASH,
        display_name="Gemini 2.5 Flash",
        role="legacy fallback independent verification",
        tier=TIER_NORMAL,
        provider=PROVIDER_GOOGLE,
        context_window=1000000,
        supports_vision=True,
        supports_function_calling=True,
        supports_reasoning=True,
        cost_tier="low",
    ),
    GEMINI_1_5_FLASH: ModelSpec(
        model_id=GEMINI_1_5_FLASH,
        display_name="Gemini 1.5 Flash",
        role="general fast verification",
        tier=TIER_NORMAL,
        provider=PROVIDER_GOOGLE,
        context_window=1000000,
        supports_vision=True,
        supports_function_calling=True,
        supports_reasoning=False,
        cost_tier="low",
    ),
}


@dataclass
class ModelConfig:
    """Configuration for NR AI Model Providers and Routers."""

    # OpenAI API Credentials & Base URL (Never hardcoded; loaded strictly from environment)
    api_key: Optional[str] = None
    api_base: str = "https://api.openai.com/v1"
    organization_id: Optional[str] = None

    # Google / Gemini API Credentials & Base URL
    gemini_api_key: Optional[str] = None
    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_default_model: str = GEMINI_FLASH_LATEST

    # Routing Defaults
    default_model: str = GPT_5_6_TERRA
    fallback_model: str = GPT_5_6_LUNA
    autonomous_model: str = GPT_5_6_SOL
    complex_model: str = GPT_5_6_SOL

    # Runtime Parameters
    timeout: float = 45.0
    max_retries: int = 2
    temperature: float = 0.2
    max_tokens: Optional[int] = None

    # Fallback Chain Orders
    fallback_chain: List[str] = field(
        default_factory=lambda: [
            GPT_5_6_SOL,
            GPT_5_6_TERRA,
            GPT_5_6_LUNA,
        ]
    )
    gemini_fallback_chain: List[str] = field(
        default_factory=lambda: [
            GEMINI_FLASH_LATEST,
            GEMINI_3_7_FLASH,
            GEMINI_3_6_FLASH,
            GEMINI_3_5_FLASH,
        ]
    )

    @classmethod
    def from_env(cls) -> "ModelConfig":
        """
        Dynamically load configuration from standard environment variables.
        Precedence:
          1. NR_OPENAI_API_KEY / OPENAI_API_KEY
          2. GEMINI_API_KEY / GOOGLE_API_KEY / NR_GEMINI_API_KEY
        """
        api_key = (
            os.getenv("NR_OPENAI_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        api_base = (
            os.getenv("NR_OPENAI_API_BASE")
            or os.getenv("OPENAI_API_BASE")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        org_id = (
            os.getenv("NR_OPENAI_ORG_ID")
            or os.getenv("OPENAI_ORG_ID")
        )
        gemini_key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or os.getenv("NR_GEMINI_API_KEY")
        )
        gemini_base = (
            os.getenv("NR_GEMINI_API_BASE")
            or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")
        default_model = os.getenv("NR_DEFAULT_MODEL", GPT_5_6_TERRA)
        timeout = float(os.getenv("NR_MODEL_TIMEOUT", "45.0"))
        max_retries = int(os.getenv("NR_MODEL_MAX_RETRIES", "2"))

        return cls(
            api_key=api_key,
            api_base=api_base,
            organization_id=org_id,
            gemini_api_key=gemini_key,
            gemini_api_base=gemini_base,
            default_model=default_model,
            timeout=timeout,
            max_retries=max_retries,
        )

    def has_credentials(self) -> bool:
        """Returns True if a valid OpenAI API key string is present."""
        return bool(self.api_key and self.api_key.strip())

    def has_gemini_credentials(self) -> bool:
        """Returns True if a valid Gemini API key string is present."""
        return bool(self.gemini_api_key and self.gemini_api_key.strip())
