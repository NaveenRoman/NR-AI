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

import re
from typing import Any, Dict, List, Optional

from app.agent.model_provider import OpenAIProvider
from app.config.model_config import (
    ALL_MODELS,
    GEMINI_FLASH_LATEST,
    GEMINI_3_7_FLASH,
    GEMINI_3_6_FLASH,
    GEMINI_3_5_FLASH,
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


class ModelRouter:
    """
    Centralized model router for NR-AI.

    Directs tasks to the optimal intelligence and cost tier while allowing
    explicit developer overrides and automated degradation handling.
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
    }

    # Fallback ladder
    FALLBACK_LADDER: Dict[str, str] = {
        GPT_6_ASTRA: GPT_5_6_SOL,
        GPT_5_6_SOL: GPT_5_6_TERRA,
        GPT_5_6_TERRA: GPT_5_6_LUNA,
        GPT_5_6_LUNA: GPT_5_6_LUNA,
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
    # Route Resolution
    # -------------------------------------------------------------------------

    def route(
        self,
        task_type: Optional[str] = None,
        prompt: Optional[str] = None,
        explicit_model: Optional[str] = None,
    ) -> str:
        """
        Determine the appropriate OpenAI model ID.

        Routing Precedence:
        1. Explicit model override (`explicit_model`)
        2. Explicit `task_type` parameter ("routine", "normal", "complex", "highest")
        3. Automated classification of `prompt` content
        4. Default model configuration
        """
        # 1. Explicit Model Override
        if explicit_model:
            normalized = explicit_model.strip().lower()
            if normalized in self.MODEL_ALIASES:
                return self.MODEL_ALIASES[normalized]
            if normalized in ALL_MODELS:
                return normalized

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
        system_prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Routes the task and invokes the central OpenAI provider.
        """
        selected_model = self.route(
            task_type=task_type,
            prompt=prompt or (messages[-1]["content"] if messages else ""),
            explicit_model=explicit_model,
        )

        return self.provider.generate(
            prompt=prompt,
            model=selected_model,
            system_prompt=system_prompt,
            messages=messages,
            **kwargs,
        )
