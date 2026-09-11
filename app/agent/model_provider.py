"""
NR AI Centralized OpenAI Model Provider.

Manages direct API communication with OpenAI for the new model family:
- GPT-6 Astra (gpt-6-astra)
- GPT-5.6 Sol (gpt-5.6-sol)
- GPT-5.6 Terra (gpt-5.6-terra)
- GPT-5.6 Luna (gpt-5.6-luna)

Features:
- Standard library implementation (urllib.request) with zero required external dependencies
- Safe environment-based credential loading (no hardcoded secrets)
- Transparent error handling and automated fallback chains
- Clear distinction between CONFIGURED state and LIVE API VERIFIED status
"""

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from app.config.model_config import (
    ALL_MODELS,
    GEMINI_1_5_FLASH,
    GEMINI_2_5_FLASH,
    GEMINI_3_6_FLASH,
    GPT_5_6_LUNA,
    GPT_5_6_SOL,
    GPT_5_6_TERRA,
    GPT_6_ASTRA,
    MODEL_REGISTRY,
    PROVIDER_GOOGLE,
    PROVIDER_OPENAI,
    ModelConfig,
    ModelSpec,
)

logger = logging.getLogger("NRAI.ModelProvider")


class OpenAIProvider:
    """
    Central OpenAI API Provider for NR-AI.

    Provides robust, unified inference for reasoning, coding, autonomous execution,
    and conversational workflows across the GPT-6 and GPT-5.6 model family.
    """

    def __init__(self, config: Optional[ModelConfig] = None):
        self.config = config or ModelConfig.from_env()

    @property
    def api_key(self) -> Optional[str]:
        return self.config.api_key

    @property
    def api_base(self) -> str:
        return self.config.api_base

    def is_available(self) -> bool:
        """Checks if API credentials are provided in the configuration."""
        return self.config.has_credentials()

    def get_model_spec(self, model_id: str) -> Optional[ModelSpec]:
        """Retrieve model metadata specification from the registry."""
        return MODEL_REGISTRY.get(model_id)

    # -------------------------------------------------------------------------
    # Core Inference
    # -------------------------------------------------------------------------

    def generate(
        self,
        prompt: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
        allow_fallback: bool = True,
    ) -> Dict[str, Any]:
        """
        Execute an inference request against the OpenAI API.

        Args:
            prompt: User message prompt string (used if messages is not provided)
            model: Explicit model ID override (e.g. gpt-6-astra)
            system_prompt: Optional system instruction prompt
            messages: Pre-formatted chat history list of dicts [{'role': '...', 'content': '...'}]
            max_tokens: Maximum completion tokens
            temperature: Sampling temperature
            timeout: Request timeout in seconds
            allow_fallback: Whether to attempt lower-tier models on quota or rate limit failures

        Returns:
            Dict containing success flag, content, model, error diagnostics, and fallback info.
        """
        target_model = model or self.config.default_model

        # Build message payload
        payload_messages: List[Dict[str, str]] = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})

        if messages:
            payload_messages.extend(messages)
        elif prompt is not None:
            payload_messages.append({"role": "user", "content": prompt})
        else:
            return {
                "success": False,
                "content": "",
                "model": target_model,
                "error": "No prompt or messages provided.",
                "fallback_used": False,
            }

        if not self.is_available():
            return {
                "success": False,
                "content": "",
                "model": target_model,
                "error": "NO_CREDENTIALS: OpenAI API key is not configured in environment.",
                "fallback_used": False,
            }

        models_to_try = [target_model]
        if allow_fallback:
            for fb in self.config.fallback_chain:
                if fb != target_model and fb not in models_to_try:
                    models_to_try.append(fb)

        last_error = None
        for attempt_model in models_to_try:
            res = self._execute_chat_completion(
                model=attempt_model,
                messages=payload_messages,
                max_tokens=max_tokens or self.config.max_tokens,
                temperature=temperature if temperature is not None else self.config.temperature,
                timeout=timeout or self.config.timeout,
            )

            if res["success"]:
                res["fallback_used"] = (attempt_model != target_model)
                res["original_requested_model"] = target_model
                return res

            last_error = res.get("error", "Unknown error")
            # If error is invalid auth, quota exhausted, or model not found, don't keep looping blindly
            err_lower = str(last_error).lower()
            if "invalid_api_key" in err_lower or "insufficient_quota" in err_lower or "quota" in err_lower or not allow_fallback:
                break

        return {
            "success": False,
            "content": "",
            "model": target_model,
            "status_code": res.get("status_code"),
            "error_code": res.get("error_code"),
            "error": last_error or "Inference failed across all attempted models.",
            "latency_s": res.get("latency_s", 0.0),
            "fallback_used": False,
        }

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Convenience wrapper for multi-turn chat completions."""
        return self.generate(messages=messages, model=model, **kwargs)

    # -------------------------------------------------------------------------
    # Internal HTTP Request Execution
    # -------------------------------------------------------------------------

    def _execute_chat_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        timeout: float = 45.0,
    ) -> Dict[str, Any]:
        url = f"{self.api_base}/chat/completions"

        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "NR-AI-Agent/1.0",
        }
        if self.config.organization_id:
            headers["OpenAI-Organization"] = self.config.organization_id

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_bytes = resp.read()
                elapsed = time.time() - t0
                parsed = json.loads(resp_bytes.decode("utf-8"))

                choices = parsed.get("choices", [])
                if not choices:
                    return {
                        "success": False,
                        "content": "",
                        "model": model,
                        "error": "No choices returned in OpenAI response.",
                        "latency_s": elapsed,
                    }

                msg = choices[0].get("message", {})
                content = msg.get("content", "")
                finish_reason = choices[0].get("finish_reason", "stop")
                usage = parsed.get("usage", {})

                return {
                    "success": True,
                    "content": content,
                    "model": parsed.get("model", model),
                    "finish_reason": finish_reason,
                    "usage": usage,
                    "latency_s": elapsed,
                    "error": None,
                }

        except urllib.error.HTTPError as e:
            elapsed = time.time() - t0
            raw_err = ""
            try:
                raw_err = e.read().decode("utf-8")
                err_json = json.loads(raw_err)
                err_msg = err_json.get("error", {}).get("message", raw_err)
                err_code = err_json.get("error", {}).get("code", "")
            except Exception:
                err_msg = raw_err or str(e)
                err_code = ""

            return {
                "success": False,
                "content": "",
                "model": model,
                "status_code": e.code,
                "error_code": err_code,
                "error": f"HTTP {e.code}: {err_msg}",
                "latency_s": elapsed,
            }
        except urllib.error.URLError as e:
            return {
                "success": False,
                "content": "",
                "model": model,
                "error": f"Connection error: {e.reason}",
            }
        except Exception as e:
            return {
                "success": False,
                "content": "",
                "model": model,
                "error": f"Unexpected error during API call: {e}",
            }

    # -------------------------------------------------------------------------
    # Verification & Diagnostics
    # -------------------------------------------------------------------------

    def verify_live_api(self, model: str = GPT_6_ASTRA) -> Dict[str, Any]:
        """
        Verify the live status of credentials and connectivity for a model.
        Distinguishes CONFIGURED status from LIVE API VERIFIED status.
        """
        has_key = self.is_available()
        spec = self.get_model_spec(model)

        report = {
            "model": model,
            "display_name": spec.display_name if spec else model,
            "configured": True,
            "has_credentials": has_key,
            "live_api_verified": False,
            "status": "UNVERIFIED",
            "details": "",
        }

        if not has_key:
            report["status"] = "NO_CREDENTIALS"
            report["details"] = "OpenAI API key is missing from environment."
            return report

        # Test live lightweight completion
        probe_res = self._execute_chat_completion(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=3,
            timeout=10.0,
        )

        if probe_res["success"]:
            report["live_api_verified"] = True
            report["status"] = "LIVE_VERIFIED"
            report["details"] = f"Model {model} successfully responded live in {probe_res.get('latency_s', 0):.2f}s."
        else:
            err = probe_res.get("error", "")
            status_code = probe_res.get("status_code")
            if status_code == 429 or "insufficient_quota" in err or "credit_balance_exhausted" in err:
                report["status"] = "CREDENTIALS_EXHAUSTED_OR_RATE_LIMITED"
                report["details"] = (
                    f"API credentials are authentic and accepted by OpenAI, but returned HTTP {status_code} "
                    f"({err}). Account has zero credits or active rate limit."
                )
            elif status_code == 404 or "model_not_found" in err:
                report["status"] = "MODEL_NOT_FOUND"
                report["details"] = f"OpenAI reported model {model} not found or access denied."
            else:
                report["status"] = "API_ERROR"
                report["details"] = f"OpenAI returned error: {err}"

        return report


class GeminiProvider:
    """
    Google Gemini API Provider for NR-AI.

    Provides robust inference for Gemini 3.6 Flash and its fallbacks
    (Gemini 2.5 Flash, Gemini 1.5 Flash).
    Zero external dependencies: uses standard library urllib.request.
    """

    def __init__(self, config: Optional[ModelConfig] = None):
        self.config = config or ModelConfig.from_env()

    @property
    def api_key(self) -> Optional[str]:
        return self.config.gemini_api_key

    @property
    def api_base(self) -> str:
        return self.config.gemini_api_base

    def is_available(self) -> bool:
        """Checks if Gemini API credentials are provided in configuration/environment."""
        return self.config.has_gemini_credentials()

    def get_model_spec(self, model_id: str) -> Optional[ModelSpec]:
        """Retrieve model metadata specification from the registry."""
        return MODEL_REGISTRY.get(model_id)

    def generate(
        self,
        prompt: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
        allow_fallback: bool = True,
    ) -> Dict[str, Any]:
        """
        Execute an inference or verification request against the Google Gemini API.
        """
        target_model = model or self.config.gemini_default_model

        if not self.is_available():
            return {
                "success": False,
                "content": "",
                "model": target_model,
                "error": "NO_CREDENTIALS: GEMINI_API_KEY / GOOGLE_API_KEY is not configured in environment.",
                "fallback_used": False,
            }

        # Build contents payload
        parts = []
        if system_prompt:
            parts.append({"text": f"System Instruction: {system_prompt}\n\n"})

        if messages:
            for m in messages:
                role_prefix = "User: " if m.get("role") == "user" else "Model: "
                parts.append({"text": f"{role_prefix}{m.get('content', '')}\n"})
        elif prompt is not None:
            parts.append({"text": prompt})
        else:
            return {
                "success": False,
                "content": "",
                "model": target_model,
                "error": "No prompt or messages provided.",
                "fallback_used": False,
            }

        models_to_try = [target_model]
        if allow_fallback:
            for fb in self.config.gemini_fallback_chain:
                if fb != target_model and fb not in models_to_try:
                    models_to_try.append(fb)

        last_error = None
        for attempt_model in models_to_try:
            res = self._execute_gemini_generate(
                model=attempt_model,
                parts=parts,
                max_tokens=max_tokens or self.config.max_tokens,
                temperature=temperature if temperature is not None else self.config.temperature,
                timeout=timeout or self.config.timeout,
            )

            if res["success"]:
                res["fallback_used"] = (attempt_model != target_model)
                res["original_requested_model"] = target_model
                return res

            last_error = res.get("error", "Unknown error")
            if "invalid_api_key" in str(last_error).lower() or not allow_fallback:
                break

        return {
            "success": False,
            "content": "",
            "model": target_model,
            "status_code": res.get("status_code") if 'res' in locals() else None,
            "error": last_error or "Inference failed across all attempted Gemini models.",
            "latency_s": res.get("latency_s", 0.0) if 'res' in locals() else 0.0,
            "fallback_used": False,
        }

    def _execute_gemini_generate(
        self,
        model: str,
        parts: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        timeout: float = 45.0,
        _is_retry: bool = False,
    ) -> Dict[str, Any]:
        url = f"{self.api_base}/models/{model}:generateContent?key={self.api_key}"

        body: Dict[str, Any] = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": temperature,
            },
        }
        if max_tokens:
            body["generationConfig"]["maxOutputTokens"] = max(max_tokens, 1000)

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "NR-AI-Gemini-Agent/1.0",
        }

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_bytes = resp.read()
                elapsed = time.time() - t0
                parsed = json.loads(resp_bytes.decode("utf-8"))

                candidates = parsed.get("candidates", [])
                if not candidates:
                    return {
                        "success": False,
                        "content": "",
                        "model": model,
                        "status_code": 200,
                        "error": "No candidates returned in Gemini response.",
                        "latency_s": elapsed,
                    }

                content_parts = candidates[0].get("content", {}).get("parts", [])
                text_content = "".join(p.get("text", "") for p in content_parts)
                finish_reason = candidates[0].get("finishReason", "STOP")

                return {
                    "success": True,
                    "content": text_content,
                    "model": model,
                    "status_code": 200,
                    "finish_reason": finish_reason,
                    "latency_s": elapsed,
                    "error": None,
                }

        except urllib.error.HTTPError as e:
            elapsed = time.time() - t0
            raw_err = ""
            try:
                raw_err = e.read().decode("utf-8")
                err_json = json.loads(raw_err)
                err_msg = err_json.get("error", {}).get("message", raw_err)
                err_code = err_json.get("error", {}).get("code", "")
            except Exception:
                err_msg = raw_err or str(e)
                err_code = ""

            if e.code == 429 and not _is_retry:
                import re
                m = re.search(r"retry in ([0-9.]+)s", err_msg.lower())
                if m:
                    retry_sec = float(m.group(1))
                    if retry_sec <= 25.0:
                        time.sleep(retry_sec + 0.5)
                        return self._execute_gemini_generate(
                            model=model,
                            parts=parts,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            timeout=timeout,
                            _is_retry=True,
                        )

            return {
                "success": False,
                "content": "",
                "model": model,
                "status_code": e.code,
                "error_code": err_code,
                "error": f"HTTP {e.code}: {err_msg}",
                "latency_s": elapsed,
            }
        except urllib.error.URLError as e:
            return {
                "success": False,
                "content": "",
                "model": model,
                "status_code": None,
                "error": f"Connection error: {e.reason}",
            }
        except Exception as e:
            return {
                "success": False,
                "content": "",
                "model": model,
                "status_code": None,
                "error": f"Unexpected error during Gemini API call: {e}",
            }

    def verify_live_api(self, model: str = GEMINI_3_6_FLASH) -> Dict[str, Any]:
        """
        Verify the live status of credentials and connectivity for a Gemini model.
        Distinguishes CONFIGURED status from LIVE API VERIFIED status.
        """
        has_key = self.is_available()
        spec = self.get_model_spec(model)

        report = {
            "model": model,
            "display_name": spec.display_name if spec else model,
            "provider": PROVIDER_GOOGLE,
            "configured": True,
            "has_credentials": has_key,
            "live_api_verified": False,
            "status": "UNVERIFIED",
            "details": "",
        }

        if not has_key:
            report["status"] = "NO_CREDENTIALS"
            report["details"] = "GEMINI_API_KEY / GOOGLE_API_KEY is not configured in environment."
            return report

        probe_res = self._execute_gemini_generate(
            model=model,
            parts=[{"text": "ping"}],
            max_tokens=1000,
            timeout=self.config.timeout,
        )

        if probe_res["success"]:
            report["live_api_verified"] = True
            report["status"] = "LIVE_VERIFIED"
            report["details"] = f"Model {model} successfully responded live in {probe_res.get('latency_s', 0):.2f}s."
        else:
            err = probe_res.get("error", "")
            status_code = probe_res.get("status_code")
            if status_code == 404 or "not found" in err.lower():
                report["status"] = "MODEL_NOT_FOUND"
                report["details"] = f"Google API reported model {model} not found or access denied."
            elif status_code == 429:
                report["status"] = "RATE_LIMITED_OR_QUOTA_EXHAUSTED"
                report["details"] = f"Google API returned HTTP 429 rate limit or quota exhausted."
            else:
                report["status"] = "API_ERROR"
                report["details"] = f"Google API returned error: {err}"

        return report


class UnifiedModelProvider:
    """
    Centralized Multi-Model Provider for NR-AI.

    Coordinates both OpenAI and Google Gemini providers, routing requests
    seamlessly based on model identifiers or explicit provider selection.
    """

    def __init__(self, config: Optional[ModelConfig] = None):
        self.config = config or ModelConfig.from_env()
        self.openai = OpenAIProvider(config=self.config)
        self.gemini = GeminiProvider(config=self.config)

    def get_provider_for_model(self, model_id: str) -> Any:
        spec = MODEL_REGISTRY.get(model_id)
        if spec and spec.provider == PROVIDER_GOOGLE:
            return self.gemini
        if "gemini" in model_id.lower():
            return self.gemini
        return self.openai

    def generate(
        self,
        prompt: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Route to appropriate provider and generate response."""
        target_model = model or self.config.default_model
        allow_fallback = kwargs.get("allow_fallback", True)

        if provider == PROVIDER_GOOGLE or "gemini" in target_model.lower():
            res = self.gemini.generate(prompt=prompt, model=target_model, **kwargs)
            res.setdefault("provider", "Google Gemini")
            if res.get("success") and "status_code" not in res:
                res["status_code"] = 200

            # If Gemini fails and OpenAI is available, execute seamless cross-provider fallback
            if not res.get("success") and allow_fallback and self.openai.is_available():
                status_code = res.get("status_code")
                err = str(res.get("error", ""))
                if status_code in {429, 500, 502, 503, 504} or any(
                    k in err.lower() for k in ["quota", "credits", "timeout", "connection", "rate limit", "no_credentials"]
                ):
                    openai_model = self.config.default_model
                    oai_res = self.openai.generate(
                        prompt=prompt,
                        model=openai_model,
                        system_prompt=kwargs.get("system_prompt"),
                        messages=kwargs.get("messages"),
                        max_tokens=kwargs.get("max_tokens"),
                        temperature=kwargs.get("temperature"),
                        timeout=kwargs.get("timeout"),
                        allow_fallback=True,
                    )
                    if oai_res.get("success"):
                        oai_res["fallback_used"] = True
                        oai_res["original_requested_model"] = target_model
                        oai_res["provider"] = "OpenAI"
                        oai_res["status_code"] = 200
                        oai_res["gemini_error"] = err
                        return oai_res
            return res

        # Attempt OpenAI
        res = self.openai.generate(prompt=prompt, model=target_model, **kwargs)
        res.setdefault("provider", "OpenAI")

        # If OpenAI fails due to quota exhausted (429), timeout, connection, or auth error,
        # and Gemini is available, execute seamless cross-provider fallback
        if not res.get("success") and allow_fallback and self.gemini.is_available():
            status_code = res.get("status_code")
            err = str(res.get("error", ""))
            if status_code in {429, 500, 502, 503, 504} or any(
                k in err.lower() for k in ["quota", "credits", "timeout", "connection", "rate limit", "no_credentials"]
            ):
                gemini_model = self.config.gemini_default_model
                gem_res = self.gemini.generate(
                    prompt=prompt,
                    model=gemini_model,
                    system_prompt=kwargs.get("system_prompt"),
                    messages=kwargs.get("messages"),
                    max_tokens=kwargs.get("max_tokens"),
                    temperature=kwargs.get("temperature"),
                    timeout=kwargs.get("timeout"),
                    allow_fallback=True,
                )
                if gem_res.get("success"):
                    gem_res["fallback_used"] = True
                    gem_res["original_requested_model"] = target_model
                    gem_res["provider"] = "Google Gemini"
                    gem_res["status_code"] = 200
                    gem_res["openai_error"] = err
                    return gem_res

        return res

    def verify_all_models(self) -> Dict[str, Any]:
        """
        Probe all configured models across OpenAI and Google and return
        their exact availability status and fallback reasons.
        """
        results = {}
        for m in [GPT_6_ASTRA, GPT_5_6_SOL, GPT_5_6_TERRA, GPT_5_6_LUNA]:
            results[m] = self.openai.verify_live_api(m)
        for m in self.config.gemini_fallback_chain:
            results[m] = self.gemini.verify_live_api(m)
        return results

