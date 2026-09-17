"""
NR AI Centralized Model Routing Engine.

Implements the multi-tier model routing policy across the OpenAI model hierarchy:
- Routine / simple tasks                                     → GPT-5.6 Luna  (gpt-5.6-luna)
- Normal tasks                                               → GPT-5.6 Terra (gpt-5.6-terra)
- Complex coding / reasoning                                 → GPT-5.6 Sol   (gpt-5.6-sol)
- Highest difficulty, autonomous tasks, architecture, debug  → GPT-6 Astra   (gpt-6-astra)

Features:
- Explicit model override support
- Heuristic and keyword task classification
- Transparent fallback ladder
- Direct integration with central OpenAIProvider
"""

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from app.agent.model_provider import OpenAIProvider
from app.config.model_config import (
    ALL_MODELS,
    DEEPSEEK_CODER_V2_LITE,
    GEMMA_2_9B,
    GEMINI_FLASH_LATEST,
    GEMINI_3_7_FLASH,
    GEMINI_3_6_FLASH,
    GEMINI_3_5_FLASH,
    GEMINI_2_5_FLASH,
    GEMINI_1_5_FLASH,
    GPT_5_6_LUNA,
    GPT_5_6_SOL,
    GPT_5_6_TERRA,
    GPT_6_ASTRA,
    MODEL_REGISTRY,
    PROVIDER_GOOGLE,
    PROVIDER_OPENAI,
    QWEN_2_5_CODER_7B,
    QWEN2_VL_7B,
    TIER_COMPLEX,
    TIER_HIGHEST,
    TIER_NORMAL,
    TIER_ROUTINE,
    ModelCapability,
    ModelConfig,
    ModelSpec,
)

logger = logging.getLogger("NRAI.ModelRouter")


class CapabilityUnavailableError(Exception):
    """Raised when no enabled model in the registry satisfies the requested capabilities."""
    pass


class ModelRouter:
    """
    Centralized model router for NR-AI.

    Directs tasks to the optimal intelligence and cost tier while allowing
    explicit developer overrides, capability-based matching, and automated degradation handling.
    """

    # Aliases for explicit model override convenience
    MODEL_ALIASES: Dict[str, str] = {
        "astra": GPT_6_ASTRA,
        "gpt6": GPT_6_ASTRA,
        "gpt-6": GPT_6_ASTRA,
        "gpt-6-astra": GPT_6_ASTRA,
        "sol": GPT_5_6_SOL,
        "gpt5.6-sol": GPT_5_6_SOL,
        "gpt-5.6-sol": GPT_5_6_SOL,
        "terra": GPT_5_6_TERRA,
        "gpt5.6-terra": GPT_5_6_TERRA,
        "gpt-5.6-terra": GPT_5_6_TERRA,
        "luna": GPT_5_6_LUNA,
        "gpt5.6-luna": GPT_5_6_LUNA,
        "gpt-5.6-luna": GPT_5_6_LUNA,
        "gemini": GEMINI_FLASH_LATEST,
        "gemini-flash": GEMINI_FLASH_LATEST,
        "gemini-flash-latest": GEMINI_FLASH_LATEST,
        "gemini-3.7-flash": GEMINI_3_7_FLASH,
        "gemini-3.6-flash": GEMINI_3_6_FLASH,
        "gemini-3.5-flash": GEMINI_3_5_FLASH,
        "gemini-2.5-flash": GEMINI_2_5_FLASH,
        "gemini-1.5-flash": GEMINI_1_5_FLASH,
        "qwen-coder": QWEN_2_5_CODER_7B,
        "qwen": QWEN_2_5_CODER_7B,
        "deepseek-coder": DEEPSEEK_CODER_V2_LITE,
        "deepseek": DEEPSEEK_CODER_V2_LITE,
        "gemma": GEMMA_2_9B,
        "qwen-vl": QWEN2_VL_7B,
    }

    # Fallback ladder
    FALLBACK_LADDER: Dict[str, str] = {
        GPT_6_ASTRA: GPT_5_6_SOL,
        GPT_5_6_SOL: GPT_5_6_TERRA,
        GPT_5_6_TERRA: GPT_5_6_LUNA,
        GPT_5_6_LUNA: GEMINI_3_6_FLASH,
        GEMINI_3_7_FLASH: GEMINI_3_6_FLASH,
        GEMINI_3_6_FLASH: GEMINI_3_5_FLASH,
        GEMINI_3_5_FLASH: GEMINI_2_5_FLASH,
        GEMINI_2_5_FLASH: GEMINI_1_5_FLASH,
        GEMINI_1_5_FLASH: GPT_5_6_LUNA,
    }

    def __init__(
        self,
        config: Optional[ModelConfig] = None,
        provider: Optional[Any] = None,
    ):
        self.config = config or ModelConfig.from_env()
        if provider is not None:
            self.provider = provider
        else:
            from app.agent.model_provider import UnifiedModelProvider
            self.provider = UnifiedModelProvider(config=self.config)

    # -------------------------------------------------------------------------
    # Capability Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def normalize_capability(cap: Union[ModelCapability, str]) -> ModelCapability:
        """Normalizes a string or enum to ModelCapability."""
        if isinstance(cap, ModelCapability):
            return cap
        raw = str(cap).strip().upper()
        if hasattr(ModelCapability, raw):
            return getattr(ModelCapability, raw)
        for mc in ModelCapability:
            if mc.value.upper() == raw:
                return mc
        raise ValueError(f"Unknown model capability: {cap}")

    def infer_required_capabilities(
        self,
        task_type: Optional[str] = None,
        prompt: Optional[str] = None,
        explicit_capabilities: Optional[Iterable[Union[ModelCapability, str]]] = None,
    ) -> Set[ModelCapability]:
        """
        Determines the set of required model capabilities based on explicit requirements,
        task type, or heuristic keyword analysis of the user prompt.
        """
        if explicit_capabilities:
            return {self.normalize_capability(c) for c in explicit_capabilities}

        caps: Set[ModelCapability] = {ModelCapability.GENERAL}

        text = ""
        if prompt:
            text += " " + prompt.lower()
        if task_type:
            text += " " + task_type.lower()

        # Coding
        coding_keywords = [
            "code", "coding", "implement", "refactor", "algorithm", "function",
            "class", "method", "unit test", "integration test", "script", "syntax",
            "debug", "fastapi", "flask", "django", "react", "spring boot", "c#", "python",
        ]
        if any(kw in text for kw in coding_keywords):
            caps.add(ModelCapability.CODING)

        # Vision
        vision_keywords = [
            "vision", "screen", "image", "screenshot", "ocr", "visual",
            "window", "inspect_screen", "see", "ui layout", "crop", "bounding box",
        ]
        if any(kw in text for kw in vision_keywords):
            caps.add(ModelCapability.VISION)

        # Web
        web_keywords = [
            "web", "browser", "navigate", "url", "website", "scrape", "html",
            "dom", "page", "tab", "http", "click button", "form",
        ]
        if any(kw in text for kw in web_keywords):
            caps.add(ModelCapability.WEB)
            caps.add(ModelCapability.REASONING)

        # Verification / Judge
        verification_keywords = [
            "verify", "verification", "judge", "critic", "review", "assert",
            "consensus", "audit", "check result", "validate state",
        ]
        if any(kw in text for kw in verification_keywords):
            caps.add(ModelCapability.VERIFICATION)

        # Math
        math_keywords = [
            "math", "calculate", "equation", "formula", "arithmetic", "calculus",
            "algebra", "matrix", "integral",
        ]
        if any(kw in text for kw in math_keywords):
            caps.add(ModelCapability.MATH)

        # Defensive Security
        security_keywords = [
            "security", "defensive", "vulnerability", "cve", "firewall", "sanitize",
            "exploit prevention", "sandbox", "audit trail",
        ]
        if any(kw in text for kw in security_keywords):
            caps.add(ModelCapability.DEFENSIVE_SECURITY)

        # Research
        research_keywords = [
            "research", "paper", "literature", "documentation", "survey", "cite",
        ]
        if any(kw in text for kw in research_keywords):
            caps.add(ModelCapability.RESEARCH)

        # Reasoning
        reasoning_keywords = [
            "reasoning", "architecture", "root cause", "self-healing", "autonomous",
            "multi-step", "system design", "diagnose", "complex",
        ]
        if any(kw in text for kw in reasoning_keywords):
            caps.add(ModelCapability.REASONING)

        return caps

    def find_models_for_capabilities(
        self,
        required_capabilities: Iterable[Union[ModelCapability, str]],
        include_disabled: bool = False,
    ) -> List[ModelSpec]:
        """
        Finds all models in the registry that support every requested capability.
        Ignores disabled models unless include_disabled is explicitly True.
        """
        req_set = {self.normalize_capability(c) for c in required_capabilities}
        matches: List[ModelSpec] = []
        for spec in MODEL_REGISTRY.values():
            if not include_disabled and not spec.enabled:
                continue
            if req_set.issubset(spec.capabilities):
                matches.append(spec)
        return matches

    def select_best_model(
        self,
        candidates: List[ModelSpec],
        cost_preference: Optional[str] = None,
    ) -> Optional[ModelSpec]:
        """
        Selects the optimal model from a list of capability-matching candidates.
        Selection policy:
        1. Provider configured / available
        2. Reliability score (descending)
        3. Latency tier preference ("fast" > "normal" > "slow")
        4. Cost tier preference (matches cost_preference if specified)
        """
        if not candidates:
            return None

        def score(spec: ModelSpec) -> float:
            base = spec.reliability_score * 100.0
            # Prefer providers that have configured API keys
            if spec.provider == PROVIDER_OPENAI and self.config.has_credentials():
                base += 20.0
            elif spec.provider == PROVIDER_GOOGLE and self.config.has_gemini_credentials():
                base += 20.0

            # Latency preference
            if spec.latency_tier == "fast":
                base += 5.0
            elif spec.latency_tier == "normal":
                base += 2.0

            # Cost preference
            if cost_preference:
                if spec.cost_tier == cost_preference:
                    base += 10.0
            else:
                if spec.cost_tier in {"low", "balanced"}:
                    base += 3.0

            return base

        sorted_candidates = sorted(candidates, key=score, reverse=True)
        return sorted_candidates[0]

    def get_verified_model_for_capabilities(
        self,
        required_capabilities: Iterable[Union[ModelCapability, str]],
        cost_preference: Optional[str] = None,
        require_live_api: bool = True,
    ) -> Tuple[Optional[str], str]:
        """
        Selects an enabled model matching capabilities and validates live API verification state.
        Returns (model_id, status_message).
        If highest candidate is unverified (e.g. GPT-6 Astra without live credits),
        either returns verified fallback or returns MODEL_UNVERIFIED.
        """
        try:
            candidates = self.find_models_for_capabilities(required_capabilities)
        except (ValueError, Exception) as e:
            return None, f"NO_CANDIDATE_MATCHES_CAPABILITIES: {e}"

        if not candidates:
            return None, "NO_CANDIDATE_MATCHES_CAPABILITIES"

        best = self.select_best_model(candidates, cost_preference=cost_preference)
        if not best:
            return None, "NO_MODEL_SELECTED"

        # Check live API verification if requested
        if require_live_api and best.model_id == GPT_6_ASTRA:
            if hasattr(self.provider, "verify_live_api"):
                live_stat = self.provider.verify_live_api(GPT_6_ASTRA)
                if not live_stat.get("live_api_verified"):
                    # Find alternative candidate that is verified
                    for alt in candidates:
                        if alt.model_id != GPT_6_ASTRA:
                            return alt.model_id, f"FALLBACK_VERIFIED: '{GPT_6_ASTRA}' is unverified, selected fallback '{alt.model_id}'."
                    return None, f"MODEL_UNVERIFIED: '{GPT_6_ASTRA}' live API verification failed and no verified fallback available."

        return best.model_id, f"VERIFIED_AVAILABLE: Selected '{best.model_id}'."

    def route_with_capabilities(
        self,
        required_capabilities: Optional[Iterable[Union[ModelCapability, str]]] = None,
        task_type: Optional[str] = None,
        prompt: Optional[str] = None,
        explicit_model: Optional[str] = None,
        cost_preference: Optional[str] = None,
    ) -> ModelSpec:
        """
        Routes to the best matching ModelSpec based on capabilities.
        Raises CapabilityUnavailableError if no enabled model satisfies requirements.
        """
        # 1. Explicit Model Override
        if explicit_model:
            normalized = explicit_model.strip().lower()
            model_id = self.MODEL_ALIASES.get(normalized, normalized)
            spec = MODEL_REGISTRY.get(model_id)
            if not spec:
                raise CapabilityUnavailableError(f"CAPABILITY_UNAVAILABLE: Unknown model '{explicit_model}'.")
            if not spec.enabled:
                raise CapabilityUnavailableError(
                    f"CAPABILITY_UNAVAILABLE: Model '{spec.display_name}' ({spec.model_id}) is currently disabled."
                )
            if required_capabilities:
                req_set = {self.normalize_capability(c) for c in required_capabilities}
                if not req_set.issubset(spec.capabilities):
                    missing = [c.value for c in (req_set - spec.capabilities)]
                    raise CapabilityUnavailableError(
                        f"CAPABILITY_UNAVAILABLE: Model '{spec.model_id}' lacks requested capabilities: {missing}"
                    )
            return spec

        # 2. Capability matching
        req_caps = self.infer_required_capabilities(
            task_type=task_type,
            prompt=prompt,
            explicit_capabilities=required_capabilities,
        )

        candidates = self.find_models_for_capabilities(req_caps, include_disabled=False)
        if not candidates:
            req_names = sorted([c.value for c in req_caps])
            raise CapabilityUnavailableError(
                f"CAPABILITY_UNAVAILABLE: No enabled model satisfies required capabilities: {req_names}"
            )

        best = self.select_best_model(candidates, cost_preference=cost_preference)
        if not best:
            raise CapabilityUnavailableError("CAPABILITY_UNAVAILABLE: Selection returned no eligible model.")
        return best

    # -------------------------------------------------------------------------
    # Route Resolution
    # -------------------------------------------------------------------------

    def route(
        self,
        task_type: Optional[str] = None,
        prompt: Optional[str] = None,
        explicit_model: Optional[str] = None,
        required_capabilities: Optional[Iterable[Union[ModelCapability, str]]] = None,
    ) -> str:
        """
        Determine the appropriate model ID.

        Routing Precedence:
        1. Explicit `required_capabilities` when provided
        2. Explicit model override (`explicit_model`)
        3. Capability-aware routing for specialized prompts (Vision, Web, Verification)
        4. Explicit `task_type` parameter ("routine", "normal", "complex", "highest")
        5. Automated classification of `prompt` content
        6. Default model configuration
        """
        # If required_capabilities is explicitly supplied, route by capabilities
        if required_capabilities is not None:
            spec = self.route_with_capabilities(
                required_capabilities=required_capabilities,
                task_type=task_type,
                prompt=prompt,
                explicit_model=explicit_model,
            )
            return spec.model_id

        # 1. Explicit Model Override
        if explicit_model:
            normalized = explicit_model.strip().lower()
            target_id = self.MODEL_ALIASES.get(normalized, normalized)
            spec = MODEL_REGISTRY.get(target_id)
            if spec and not spec.enabled:
                raise CapabilityUnavailableError(
                    f"CAPABILITY_UNAVAILABLE: Model '{spec.display_name}' is disabled."
                )
            if target_id in ALL_MODELS or target_id in MODEL_REGISTRY:
                return target_id

        # Check if prompt or task_type suggests specialized capabilities
        inferred = self.infer_required_capabilities(task_type=task_type, prompt=prompt)
        specialized = inferred - {ModelCapability.GENERAL}
        if specialized:
            try:
                spec = self.route_with_capabilities(
                    required_capabilities=inferred,
                    task_type=task_type,
                    prompt=prompt,
                    explicit_model=explicit_model,
                )
                return spec.model_id
            except CapabilityUnavailableError:
                pass  # Fall through to tier-based default

        # 2. Task Type
        if task_type:
            tt = task_type.strip().lower()
            if tt in {TIER_ROUTINE, "simple", "voice", "greeting", "time", "status", "cancel"}:
                return GPT_5_6_LUNA
            if tt in {TIER_NORMAL, "standard", "file_ops", "read_code", "single_edit"}:
                return GPT_5_6_TERRA
            if tt in {TIER_COMPLEX, "reasoning", "coding", "refactor", "tests", "api_design"}:
                return GPT_5_6_SOL
            if tt in {TIER_HIGHEST, "autonomous", "architecture", "debugging", "self_healing", "lifecycle", "multi_step"}:
                return GPT_5_6_SOL

        # 3. Prompt-based Classification
        if prompt:
            tier = self.classify_task(prompt)
            if tier == TIER_HIGHEST:
                return GPT_5_6_SOL
            if tier == TIER_COMPLEX:
                return GPT_5_6_SOL
            if tier == TIER_ROUTINE:
                return GPT_5_6_LUNA
            return GPT_5_6_TERRA

        # 4. Fallback Default
        return self.config.default_model

    def classify_task(self, prompt: str) -> str:
        """
        Classifies an input command or prompt into a task tier:
        - TIER_HIGHEST: Autonomous multi-step, architecture, advanced debugging, deep self-healing
        - TIER_COMPLEX: Professional coding, algorithms, full-stack endpoints, test suites
        - TIER_ROUTINE: Simple queries, greetings, time, basic conversational status
        - TIER_NORMAL: General single-file edits, standard code writing, helper scripts
        """
        text = prompt.lower().strip()

        # Routine / Simple triggers
        routine_patterns = [
            r"^(?:hello|hi|hey|good morning|good evening)(?:\s+.*)?$",
            r"^what time is it",
            r"^what is the date",
            r"(?:show\s+)?(?:project\s+|session\s+)?status",
            r"^(?:stop|cancel|abort|halt)$",
            r"^(?:undo|rollback|revert)$",
            r"^who are you",
            r"^what can you do",
        ]
        if any(re.search(pat, text) for pat in routine_patterns):
            return TIER_ROUTINE

        # Highest Difficulty / Autonomous / Architecture / Deep Debugging triggers
        highest_keywords = [
            "architecture",
            "architect",
            "autonomous",
            "lifecycle",
            "end-to-end",
            "multi-step",
            "deep debugging",
            "diagnose error",
            "root cause",
            "self-healing",
            "recovery",
            "compiler diagnostics",
            "syntaxerror",
            "cs0103",
            "nullreferenceexception",
            "system design",
            "distributed",
            "concurrency",
            "deadlock",
            "memory leak",
            "benchmark suite",
        ]
        if any(kw in text for kw in highest_keywords):
            return TIER_HIGHEST

        # Complex Coding / Reasoning triggers
        complex_keywords = [
            "implement",
            "algorithm",
            "spring boot",
            "rest api",
            "fastapi",
            "flask",
            "react component",
            "database schema",
            "sqlite",
            "postgresql",
            "unit test",
            "integration test",
            "refactor",
            "optimize",
            "data structure",
            "authentication",
            "jwt",
            "dockerfile",
            "game loop",
            "shader",
        ]
        if any(kw in text for kw in complex_keywords):
            return TIER_COMPLEX

        # Normal tasks default
        return TIER_NORMAL

    # -------------------------------------------------------------------------
    # Fallback & Degradation
    # -------------------------------------------------------------------------

    def fallback_for(self, model_id: str) -> str:
        """Returns the next degraded fallback model in the hierarchy."""
        return self.FALLBACK_LADDER.get(model_id, GPT_5_6_LUNA)

    def get_model_spec(self, model_id: str) -> Optional[ModelSpec]:
        """Fetch specification for a model."""
        return MODEL_REGISTRY.get(model_id)

    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------

    def execute(
        self,
        prompt: Optional[str] = None,
        task_type: Optional[str] = None,
        explicit_model: Optional[str] = None,
        required_capabilities: Optional[Iterable[Union[ModelCapability, str]]] = None,
        system_prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Routes the task based on capability requirements and invokes the provider.
        Catches CapabilityUnavailableError and returns structured failure.
        """
        try:
            selected_model = self.route(
                task_type=task_type,
                prompt=prompt or (messages[-1]["content"] if messages else ""),
                explicit_model=explicit_model,
                required_capabilities=required_capabilities,
            )
        except CapabilityUnavailableError as e:
            return {
                "success": False,
                "content": "",
                "model": None,
                "error": str(e),
                "status_code": 404,
                "capability_unavailable": True,
                "fallback_used": False,
            }

        return self.provider.generate(
            prompt=prompt,
            model=selected_model,
            system_prompt=system_prompt,
            messages=messages,
            **kwargs,
        )
