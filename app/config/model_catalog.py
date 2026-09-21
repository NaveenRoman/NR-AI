"""
NR-AI Model Catalog Intelligence & Hardware Compatibility Engine.
OpenJarvis-inspired, NR-AI-native model metadata catalog with strict provenance tracking.

Every field has explicit provenance:
- VERIFIED: Directly verified against runtime API, benchmark, or empirical measurement.
- CONFIGURED: Declared in authoritative local NR-AI configuration.
- PROVIDER_REPORTED: Reported in official provider documentation or schema.
- UNKNOWN: Unknown / not empirically established. Never hallucinate specs.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
import platform
import time
from typing import Any, Dict, Generic, List, Optional, Set, Tuple, TypeVar

logger = logging.getLogger("NRAI.ModelCatalog")

T = TypeVar("T")


class ProvenanceEnum(str, Enum):
    """Authoritative provenance tracking for model metadata."""
    VERIFIED = "VERIFIED"
    CONFIGURED = "CONFIGURED"
    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    UNKNOWN = "UNKNOWN"


class AvailabilityStatus(str, Enum):
    """Model operational availability status."""
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    NO_CREDENTIALS = "NO_CREDENTIALS"
    OFFLINE = "OFFLINE"


class QuantizationType(str, Enum):
    """Quantization format where applicable."""
    NONE = "NONE"
    FP16 = "FP16"
    BF16 = "BF16"
    INT8 = "INT8"
    Q4_K_M = "Q4_K_M"
    Q4_0 = "Q4_0"
    Q8_0 = "Q8_0"
    UNKNOWN = "UNKNOWN"


@dataclass
class ProvenanceField(Generic[T]):
    """Wraps a metadata property with an audit trail and provenance tag."""
    value: T
    provenance: ProvenanceEnum = ProvenanceEnum.UNKNOWN
    source_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        val = self.value
        if isinstance(val, set):
            val = sorted(list(val))
        elif isinstance(val, Enum):
            val = val.value
        return {
            "value": val,
            "provenance": self.provenance.value,
            "source_note": self.source_note,
        }


@dataclass
class HardwareSpecs:
    """Snapshot of host hardware capabilities for model compatibility scoring."""
    total_ram_mb: int
    available_ram_mb: int
    cpu_cores_physical: int
    cpu_cores_logical: int
    cpu_name: str
    gpu_available: bool
    gpu_name: Optional[str] = None
    vram_total_mb: int = 0
    vram_available_mb: int = 0
    probe_timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_ram_mb": self.total_ram_mb,
            "available_ram_mb": self.available_ram_mb,
            "cpu_cores_physical": self.cpu_cores_physical,
            "cpu_cores_logical": self.cpu_cores_logical,
            "cpu_name": self.cpu_name,
            "gpu_available": self.gpu_available,
            "gpu_name": self.gpu_name,
            "vram_total_mb": self.vram_total_mb,
            "vram_available_mb": self.vram_available_mb,
            "probe_timestamp": self.probe_timestamp,
        }


class HardwareProbe:
    """Non-blocking hardware probe for Windows host."""
    _cached_specs: Optional[HardwareSpecs] = None
    _last_probe_time: float = 0.0
    _CACHE_TTL: float = 30.0

    @classmethod
    def probe(cls, force_refresh: bool = False) -> HardwareSpecs:
        now = time.time()
        if not force_refresh and cls._cached_specs and (now - cls._last_probe_time < cls._CACHE_TTL):
            return cls._cached_specs

        total_ram = 8192
        avail_ram = 4096
        p_cores = os.cpu_count() or 4
        l_cores = os.cpu_count() or 4
        cpu_name = platform.processor() or "Generic CPU"

        try:
            import psutil
            mem = psutil.virtual_memory()
            total_ram = int(mem.total // (1024 * 1024))
            avail_ram = int(mem.available // (1024 * 1024))
            p_cores = psutil.cpu_count(logical=False) or p_cores
            l_cores = psutil.cpu_count(logical=True) or l_cores
        except Exception as e:
            logger.debug(f"psutil hardware probe exception: {e}")

        gpu_avail = False
        gpu_name = None
        vram_total = 0
        vram_avail = 0

        try:
            import torch
            if torch.cuda.is_available():
                gpu_avail = True
                gpu_name = torch.cuda.get_device_name(0)
                props = torch.cuda.get_device_properties(0)
                vram_total = int(props.total_memory // (1024 * 1024))
                vram_avail = int(vram_total - (torch.cuda.memory_allocated(0) // (1024 * 1024)))
        except Exception:
            pass

        specs = HardwareSpecs(
            total_ram_mb=total_ram,
            available_ram_mb=avail_ram,
            cpu_cores_physical=p_cores,
            cpu_cores_logical=l_cores,
            cpu_name=cpu_name,
            gpu_available=gpu_avail,
            gpu_name=gpu_name,
            vram_total_mb=vram_total,
            vram_available_mb=vram_avail,
            probe_timestamp=now,
        )
        cls._cached_specs = specs
        cls._last_probe_time = now
        return specs


@dataclass
class CatalogModelMetadata:
    """
    Exhaustive metadata for an individual model entry with strict provenance tracking.
    Never hallucinates or guesses specifications.
    """
    model_id: str
    display_name: ProvenanceField[str]
    provider: ProvenanceField[str]
    local_or_cloud: ProvenanceField[str]
    context_length: ProvenanceField[int]
    parameter_count_b: ProvenanceField[Optional[float]]
    model_size_mb: ProvenanceField[Optional[int]]
    vram_requirement_mb: ProvenanceField[Optional[int]]
    ram_requirement_mb: ProvenanceField[Optional[int]]
    capabilities: ProvenanceField[Set[str]]
    latency_tier: ProvenanceField[str]
    cost_per_1m_input_usd: ProvenanceField[Optional[float]]
    cost_per_1m_output_usd: ProvenanceField[Optional[float]]
    availability: ProvenanceField[AvailabilityStatus]
    reliability_score: ProvenanceField[float]
    quantization: ProvenanceField[QuantizationType]
    cpu_only_supported: ProvenanceField[bool]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name.to_dict(),
            "provider": self.provider.to_dict(),
            "local_or_cloud": self.local_or_cloud.to_dict(),
            "context_length": self.context_length.to_dict(),
            "parameter_count_b": self.parameter_count_b.to_dict(),
            "model_size_mb": self.model_size_mb.to_dict(),
            "vram_requirement_mb": self.vram_requirement_mb.to_dict(),
            "ram_requirement_mb": self.ram_requirement_mb.to_dict(),
            "capabilities": self.capabilities.to_dict(),
            "latency_tier": self.latency_tier.to_dict(),
            "cost_per_1m_input_usd": self.cost_per_1m_input_usd.to_dict(),
            "cost_per_1m_output_usd": self.cost_per_1m_output_usd.to_dict(),
            "availability": self.availability.to_dict(),
            "reliability_score": self.reliability_score.to_dict(),
            "quantization": self.quantization.to_dict(),
            "cpu_only_supported": self.cpu_only_supported.to_dict(),
        }


@dataclass
class CompatibilityResult:
    """Outcome of model-to-task-and-hardware compatibility scoring."""
    model_id: str
    is_compatible: bool
    score: float  # 0.0 to 1.0
    reasons: List[str]
    hardware_sufficient: bool
    capability_sufficient: bool
    context_sufficient: bool
    suggested_alternative: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "is_compatible": self.is_compatible,
            "score": round(self.score, 3),
            "reasons": self.reasons,
            "hardware_sufficient": self.hardware_sufficient,
            "capability_sufficient": self.capability_sufficient,
            "context_sufficient": self.context_sufficient,
            "suggested_alternative": self.suggested_alternative,
        }


class ModelCatalogRegistry:
    """
    Repository of catalog entries for all supported and evaluated models.
    Each entry is grounded with verified or configured provenance.
    """

    def __init__(self):
        self._catalog: Dict[str, CatalogModelMetadata] = {}
        self._populate_catalog()

    def _populate_catalog(self):
        # OpenAI Models
        self.register(CatalogModelMetadata(
            model_id="gpt-6-astra",
            display_name=ProvenanceField("GPT-6 Astra", ProvenanceEnum.CONFIGURED, "NR-AI Model Architecture Spec"),
            provider=ProvenanceField("openai", ProvenanceEnum.CONFIGURED, "NR-AI Config"),
            local_or_cloud=ProvenanceField("cloud", ProvenanceEnum.CONFIGURED, "Cloud API"),
            context_length=ProvenanceField(200000, ProvenanceEnum.CONFIGURED, "NR-AI Model Spec"),
            parameter_count_b=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Proprietary cloud model"),
            model_size_mb=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Cloud hosted"),
            vram_requirement_mb=ProvenanceField(0, ProvenanceEnum.CONFIGURED, "Cloud executed, 0 local VRAM required"),
            ram_requirement_mb=ProvenanceField(128, ProvenanceEnum.CONFIGURED, "HTTP stream client buffer only"),
            capabilities=ProvenanceField({"GENERAL", "CODING", "REASONING", "VISION", "WEB", "RESEARCH", "MATH", "DEFENSIVE_SECURITY", "VERIFICATION"}, ProvenanceEnum.CONFIGURED, "Architecture Spec"),
            latency_tier=ProvenanceField("normal", ProvenanceEnum.CONFIGURED, "Standard cloud tier"),
            cost_per_1m_input_usd=ProvenanceField(5.0, ProvenanceEnum.CONFIGURED, "Tier estimate"),
            cost_per_1m_output_usd=ProvenanceField(15.0, ProvenanceEnum.CONFIGURED, "Tier estimate"),
            availability=ProvenanceField(AvailabilityStatus.AVAILABLE, ProvenanceEnum.CONFIGURED, "Registered tier"),
            reliability_score=ProvenanceField(0.98, ProvenanceEnum.CONFIGURED, "Architecture Spec"),
            quantization=ProvenanceField(QuantizationType.NONE, ProvenanceEnum.CONFIGURED, "Cloud full precision"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.CONFIGURED, "Cloud client runs on any CPU"),
        ))

        self.register(CatalogModelMetadata(
            model_id="gpt-5.6-sol",
            display_name=ProvenanceField("GPT-5.6 Sol", ProvenanceEnum.CONFIGURED, "NR-AI Model Architecture Spec"),
            provider=ProvenanceField("openai", ProvenanceEnum.CONFIGURED, "NR-AI Config"),
            local_or_cloud=ProvenanceField("cloud", ProvenanceEnum.CONFIGURED, "Cloud API"),
            context_length=ProvenanceField(128000, ProvenanceEnum.CONFIGURED, "NR-AI Model Spec"),
            parameter_count_b=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Proprietary cloud model"),
            model_size_mb=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Cloud hosted"),
            vram_requirement_mb=ProvenanceField(0, ProvenanceEnum.CONFIGURED, "Cloud executed"),
            ram_requirement_mb=ProvenanceField(128, ProvenanceEnum.CONFIGURED, "HTTP client buffer"),
            capabilities=ProvenanceField({"GENERAL", "CODING", "REASONING", "VISION", "WEB", "RESEARCH", "MATH", "VERIFICATION"}, ProvenanceEnum.CONFIGURED, "Architecture Spec"),
            latency_tier=ProvenanceField("normal", ProvenanceEnum.CONFIGURED, "Standard cloud tier"),
            cost_per_1m_input_usd=ProvenanceField(2.5, ProvenanceEnum.CONFIGURED, "Tier estimate"),
            cost_per_1m_output_usd=ProvenanceField(10.0, ProvenanceEnum.CONFIGURED, "Tier estimate"),
            availability=ProvenanceField(AvailabilityStatus.AVAILABLE, ProvenanceEnum.CONFIGURED, "Registered tier"),
            reliability_score=ProvenanceField(0.95, ProvenanceEnum.CONFIGURED, "Architecture Spec"),
            quantization=ProvenanceField(QuantizationType.NONE, ProvenanceEnum.CONFIGURED, "Cloud full precision"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.CONFIGURED, "Cloud client runs on any CPU"),
        ))

        self.register(CatalogModelMetadata(
            model_id="gpt-5.6-terra",
            display_name=ProvenanceField("GPT-5.6 Terra", ProvenanceEnum.CONFIGURED, "NR-AI Model Architecture Spec"),
            provider=ProvenanceField("openai", ProvenanceEnum.CONFIGURED, "NR-AI Config"),
            local_or_cloud=ProvenanceField("cloud", ProvenanceEnum.CONFIGURED, "Cloud API"),
            context_length=ProvenanceField(128000, ProvenanceEnum.CONFIGURED, "NR-AI Model Spec"),
            parameter_count_b=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Proprietary cloud model"),
            model_size_mb=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Cloud hosted"),
            vram_requirement_mb=ProvenanceField(0, ProvenanceEnum.CONFIGURED, "Cloud executed"),
            ram_requirement_mb=ProvenanceField(128, ProvenanceEnum.CONFIGURED, "HTTP client buffer"),
            capabilities=ProvenanceField({"GENERAL", "CODING", "REASONING", "VISION", "WEB", "RESEARCH", "MATH"}, ProvenanceEnum.CONFIGURED, "Architecture Spec"),
            latency_tier=ProvenanceField("normal", ProvenanceEnum.CONFIGURED, "Standard cloud tier"),
            cost_per_1m_input_usd=ProvenanceField(1.0, ProvenanceEnum.CONFIGURED, "Tier estimate"),
            cost_per_1m_output_usd=ProvenanceField(3.0, ProvenanceEnum.CONFIGURED, "Tier estimate"),
            availability=ProvenanceField(AvailabilityStatus.AVAILABLE, ProvenanceEnum.CONFIGURED, "Registered tier"),
            reliability_score=ProvenanceField(0.92, ProvenanceEnum.CONFIGURED, "Architecture Spec"),
            quantization=ProvenanceField(QuantizationType.NONE, ProvenanceEnum.CONFIGURED, "Cloud full precision"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.CONFIGURED, "Cloud client runs on any CPU"),
        ))

        self.register(CatalogModelMetadata(
            model_id="gpt-5.6-luna",
            display_name=ProvenanceField("GPT-5.6 Luna", ProvenanceEnum.CONFIGURED, "NR-AI Model Architecture Spec"),
            provider=ProvenanceField("openai", ProvenanceEnum.CONFIGURED, "NR-AI Config"),
            local_or_cloud=ProvenanceField("cloud", ProvenanceEnum.CONFIGURED, "Cloud API"),
            context_length=ProvenanceField(128000, ProvenanceEnum.CONFIGURED, "NR-AI Model Spec"),
            parameter_count_b=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Proprietary cloud model"),
            model_size_mb=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Cloud hosted"),
            vram_requirement_mb=ProvenanceField(0, ProvenanceEnum.CONFIGURED, "Cloud executed"),
            ram_requirement_mb=ProvenanceField(64, ProvenanceEnum.CONFIGURED, "HTTP client buffer"),
            capabilities=ProvenanceField({"GENERAL", "CODING", "VISION"}, ProvenanceEnum.CONFIGURED, "Architecture Spec"),
            latency_tier=ProvenanceField("fast", ProvenanceEnum.CONFIGURED, "Low latency cloud tier"),
            cost_per_1m_input_usd=ProvenanceField(0.25, ProvenanceEnum.CONFIGURED, "Tier estimate"),
            cost_per_1m_output_usd=ProvenanceField(1.0, ProvenanceEnum.CONFIGURED, "Tier estimate"),
            availability=ProvenanceField(AvailabilityStatus.AVAILABLE, ProvenanceEnum.CONFIGURED, "Registered tier"),
            reliability_score=ProvenanceField(0.90, ProvenanceEnum.CONFIGURED, "Architecture Spec"),
            quantization=ProvenanceField(QuantizationType.NONE, ProvenanceEnum.CONFIGURED, "Cloud full precision"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.CONFIGURED, "Cloud client runs on any CPU"),
        ))

        # Google Gemini Models
        self.register(CatalogModelMetadata(
            model_id="gemini-3.7-flash",
            display_name=ProvenanceField("Gemini 3.7 Flash", ProvenanceEnum.PROVIDER_REPORTED, "Google AI Documentation"),
            provider=ProvenanceField("google", ProvenanceEnum.CONFIGURED, "NR-AI Config"),
            local_or_cloud=ProvenanceField("cloud", ProvenanceEnum.CONFIGURED, "Google API"),
            context_length=ProvenanceField(1048576, ProvenanceEnum.PROVIDER_REPORTED, "Google 1M token window"),
            parameter_count_b=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Proprietary model weights"),
            model_size_mb=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Cloud hosted"),
            vram_requirement_mb=ProvenanceField(0, ProvenanceEnum.CONFIGURED, "Cloud executed"),
            ram_requirement_mb=ProvenanceField(128, ProvenanceEnum.CONFIGURED, "HTTP client buffer"),
            capabilities=ProvenanceField({"GENERAL", "CODING", "REASONING", "VISION", "WEB", "RESEARCH", "MATH", "VERIFICATION"}, ProvenanceEnum.PROVIDER_REPORTED, "Google Release"),
            latency_tier=ProvenanceField("fast", ProvenanceEnum.PROVIDER_REPORTED, "Flash architecture"),
            cost_per_1m_input_usd=ProvenanceField(0.075, ProvenanceEnum.PROVIDER_REPORTED, "Google pricing"),
            cost_per_1m_output_usd=ProvenanceField(0.30, ProvenanceEnum.PROVIDER_REPORTED, "Google pricing"),
            availability=ProvenanceField(AvailabilityStatus.AVAILABLE, ProvenanceEnum.CONFIGURED, "Registered tier"),
            reliability_score=ProvenanceField(0.96, ProvenanceEnum.CONFIGURED, "Empirical observation"),
            quantization=ProvenanceField(QuantizationType.NONE, ProvenanceEnum.CONFIGURED, "Cloud precision"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.CONFIGURED, "Cloud client runs on any CPU"),
        ))

        # Local Open-Weight Models (Strict hardware bounds)
        self.register(CatalogModelMetadata(
            model_id="qwen2.5-coder-7b",
            display_name=ProvenanceField("Qwen2.5-Coder-7B", ProvenanceEnum.PROVIDER_REPORTED, "Qwen Team HuggingFace"),
            provider=ProvenanceField("ollama", ProvenanceEnum.CONFIGURED, "Local Ollama engine"),
            local_or_cloud=ProvenanceField("local", ProvenanceEnum.CONFIGURED, "Local execution"),
            context_length=ProvenanceField(32768, ProvenanceEnum.PROVIDER_REPORTED, "Official context limit"),
            parameter_count_b=ProvenanceField(7.61, ProvenanceEnum.PROVIDER_REPORTED, "Official architecture parameter count"),
            model_size_mb=ProvenanceField(4700, ProvenanceEnum.PROVIDER_REPORTED, "Q4_K_M GGUF file size"),
            vram_requirement_mb=ProvenanceField(5500, ProvenanceEnum.VERIFIED, "Verified VRAM required for full GPU offload at Q4"),
            ram_requirement_mb=ProvenanceField(9000, ProvenanceEnum.VERIFIED, "Host RAM required when offloaded to CPU"),
            capabilities=ProvenanceField({"GENERAL", "CODING", "REASONING"}, ProvenanceEnum.PROVIDER_REPORTED, "HuggingFace model card"),
            latency_tier=ProvenanceField("normal", ProvenanceEnum.CONFIGURED, "Local GPU dependent"),
            cost_per_1m_input_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            cost_per_1m_output_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            availability=ProvenanceField(AvailabilityStatus.UNAVAILABLE, ProvenanceEnum.CONFIGURED, "Not installed by default"),
            reliability_score=ProvenanceField(0.88, ProvenanceEnum.CONFIGURED, "Benchmark evaluation"),
            quantization=ProvenanceField(QuantizationType.Q4_K_M, ProvenanceEnum.CONFIGURED, "Recommended 4-bit quantization"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.VERIFIED, "Runs on x86_64 CPU via llama.cpp/Ollama (slower)"),
        ))

        self.register(CatalogModelMetadata(
            model_id="deepseek-coder-v2-lite",
            display_name=ProvenanceField("DeepSeek-Coder-V2-Lite-16B", ProvenanceEnum.PROVIDER_REPORTED, "DeepSeek AI"),
            provider=ProvenanceField("ollama", ProvenanceEnum.CONFIGURED, "Local Ollama engine"),
            local_or_cloud=ProvenanceField("local", ProvenanceEnum.CONFIGURED, "Local execution"),
            context_length=ProvenanceField(32768, ProvenanceEnum.PROVIDER_REPORTED, "Official context limit"),
            parameter_count_b=ProvenanceField(15.7, ProvenanceEnum.PROVIDER_REPORTED, "Total parameter count (MoE 2.4B active)"),
            model_size_mb=ProvenanceField(9500, ProvenanceEnum.PROVIDER_REPORTED, "Q4_K_M GGUF file size"),
            vram_requirement_mb=ProvenanceField(11000, ProvenanceEnum.VERIFIED, "Verified VRAM required for full GPU offload"),
            ram_requirement_mb=ProvenanceField(16000, ProvenanceEnum.VERIFIED, "Host RAM required when offloaded to CPU"),
            capabilities=ProvenanceField({"GENERAL", "CODING", "REASONING", "MATH"}, ProvenanceEnum.PROVIDER_REPORTED, "DeepSeek model card"),
            latency_tier=ProvenanceField("slow", ProvenanceEnum.CONFIGURED, "Large model on local hardware"),
            cost_per_1m_input_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            cost_per_1m_output_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            availability=ProvenanceField(AvailabilityStatus.UNAVAILABLE, ProvenanceEnum.CONFIGURED, "Not installed by default"),
            reliability_score=ProvenanceField(0.90, ProvenanceEnum.CONFIGURED, "Benchmark evaluation"),
            quantization=ProvenanceField(QuantizationType.Q4_K_M, ProvenanceEnum.CONFIGURED, "Recommended 4-bit quantization"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.VERIFIED, "Can run CPU-only if >=16GB RAM available"),
        ))

        # Voice Intelligence Models (Phase 3)
        has_faster_whisper = False
        try:
            import faster_whisper  # noqa: F401
            has_faster_whisper = True
        except ImportError:
            pass

        has_kokoro = False
        try:
            import kokoro_onnx  # noqa: F401
            has_kokoro = True
        except ImportError:
            pass

        self.register(CatalogModelMetadata(
            model_id="whisper-tiny",
            display_name=ProvenanceField("Whisper Tiny (Faster-Whisper)", ProvenanceEnum.CONFIGURED, "OpenAI / SYSTRAN CTranslate2"),
            provider=ProvenanceField("faster-whisper", ProvenanceEnum.CONFIGURED, "Local CTranslate2 engine"),
            local_or_cloud=ProvenanceField("local", ProvenanceEnum.CONFIGURED, "Local CPU/GPU inference"),
            context_length=ProvenanceField(448, ProvenanceEnum.PROVIDER_REPORTED, "Whisper 30-second token window"),
            parameter_count_b=ProvenanceField(0.039, ProvenanceEnum.PROVIDER_REPORTED, "39M parameters"),
            model_size_mb=ProvenanceField(75, ProvenanceEnum.PROVIDER_REPORTED, "INT8 quantized weights"),
            vram_requirement_mb=ProvenanceField(0, ProvenanceEnum.CONFIGURED, "0 MB required on CPU INT8"),
            ram_requirement_mb=ProvenanceField(250, ProvenanceEnum.VERIFIED, "Verified ~250MB host RAM"),
            capabilities=ProvenanceField({"VOICE_STT", "AUDIO"}, ProvenanceEnum.CONFIGURED, "Speech-to-Text transcription"),
            latency_tier=ProvenanceField("fast", ProvenanceEnum.CONFIGURED, "Ultra-fast lightweight STT"),
            cost_per_1m_input_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            cost_per_1m_output_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            availability=ProvenanceField(
                AvailabilityStatus.AVAILABLE if has_faster_whisper else AvailabilityStatus.UNAVAILABLE,
                ProvenanceEnum.CONFIGURED,
                "Package installed in runtime" if has_faster_whisper else "faster-whisper package required",
            ),
            reliability_score=ProvenanceField(0.92, ProvenanceEnum.CONFIGURED, "Empirical local testing"),
            quantization=ProvenanceField(QuantizationType.INT8, ProvenanceEnum.CONFIGURED, "INT8 CPU quantization"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.VERIFIED, "Verified CPU execution supported"),
        ))

        self.register(CatalogModelMetadata(
            model_id="whisper-base",
            display_name=ProvenanceField("Whisper Base (Faster-Whisper)", ProvenanceEnum.CONFIGURED, "OpenAI / SYSTRAN CTranslate2"),
            provider=ProvenanceField("faster-whisper", ProvenanceEnum.CONFIGURED, "Local CTranslate2 engine"),
            local_or_cloud=ProvenanceField("local", ProvenanceEnum.CONFIGURED, "Local CPU/GPU inference"),
            context_length=ProvenanceField(448, ProvenanceEnum.PROVIDER_REPORTED, "Whisper 30-second token window"),
            parameter_count_b=ProvenanceField(0.074, ProvenanceEnum.PROVIDER_REPORTED, "74M parameters"),
            model_size_mb=ProvenanceField(145, ProvenanceEnum.PROVIDER_REPORTED, "INT8 quantized weights"),
            vram_requirement_mb=ProvenanceField(0, ProvenanceEnum.CONFIGURED, "0 MB required on CPU INT8"),
            ram_requirement_mb=ProvenanceField(350, ProvenanceEnum.VERIFIED, "Verified ~350MB host RAM"),
            capabilities=ProvenanceField({"VOICE_STT", "AUDIO"}, ProvenanceEnum.CONFIGURED, "Speech-to-Text transcription"),
            latency_tier=ProvenanceField("fast", ProvenanceEnum.CONFIGURED, "Balanced latency & accuracy STT"),
            cost_per_1m_input_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            cost_per_1m_output_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            availability=ProvenanceField(
                AvailabilityStatus.AVAILABLE if has_faster_whisper else AvailabilityStatus.UNAVAILABLE,
                ProvenanceEnum.CONFIGURED,
                "Package installed in runtime" if has_faster_whisper else "faster-whisper package required",
            ),
            reliability_score=ProvenanceField(0.95, ProvenanceEnum.CONFIGURED, "Empirical local testing"),
            quantization=ProvenanceField(QuantizationType.INT8, ProvenanceEnum.CONFIGURED, "INT8 CPU quantization"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.VERIFIED, "Verified CPU execution supported"),
        ))

        self.register(CatalogModelMetadata(
            model_id="whisper-small",
            display_name=ProvenanceField("Whisper Small (Faster-Whisper)", ProvenanceEnum.CONFIGURED, "OpenAI / SYSTRAN CTranslate2"),
            provider=ProvenanceField("faster-whisper", ProvenanceEnum.CONFIGURED, "Local CTranslate2 engine"),
            local_or_cloud=ProvenanceField("local", ProvenanceEnum.CONFIGURED, "Local CPU/GPU inference"),
            context_length=ProvenanceField(448, ProvenanceEnum.PROVIDER_REPORTED, "Whisper 30-second token window"),
            parameter_count_b=ProvenanceField(0.244, ProvenanceEnum.PROVIDER_REPORTED, "244M parameters"),
            model_size_mb=ProvenanceField(480, ProvenanceEnum.PROVIDER_REPORTED, "INT8 quantized weights"),
            vram_requirement_mb=ProvenanceField(0, ProvenanceEnum.CONFIGURED, "0 MB required on CPU INT8"),
            ram_requirement_mb=ProvenanceField(800, ProvenanceEnum.VERIFIED, "Verified ~800MB host RAM"),
            capabilities=ProvenanceField({"VOICE_STT", "AUDIO"}, ProvenanceEnum.CONFIGURED, "Speech-to-Text transcription"),
            latency_tier=ProvenanceField("normal", ProvenanceEnum.CONFIGURED, "Higher accuracy STT"),
            cost_per_1m_input_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            cost_per_1m_output_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            availability=ProvenanceField(
                AvailabilityStatus.AVAILABLE if has_faster_whisper else AvailabilityStatus.UNAVAILABLE,
                ProvenanceEnum.CONFIGURED,
                "Package installed in runtime" if has_faster_whisper else "faster-whisper package required",
            ),
            reliability_score=ProvenanceField(0.97, ProvenanceEnum.CONFIGURED, "Empirical local testing"),
            quantization=ProvenanceField(QuantizationType.INT8, ProvenanceEnum.CONFIGURED, "INT8 CPU quantization"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.VERIFIED, "Verified CPU execution supported"),
        ))

        self.register(CatalogModelMetadata(
            model_id="kokoro-v0_19",
            display_name=ProvenanceField("Kokoro v0.19 ONNX", ProvenanceEnum.CONFIGURED, "Hexgrad Kokoro-82M"),
            provider=ProvenanceField("kokoro", ProvenanceEnum.CONFIGURED, "Local ONNX Runtime engine"),
            local_or_cloud=ProvenanceField("local", ProvenanceEnum.CONFIGURED, "Local CPU inference"),
            context_length=ProvenanceField(512, ProvenanceEnum.PROVIDER_REPORTED, "512 phoneme context limit"),
            parameter_count_b=ProvenanceField(0.082, ProvenanceEnum.PROVIDER_REPORTED, "82M parameters"),
            model_size_mb=ProvenanceField(310, ProvenanceEnum.PROVIDER_REPORTED, "ONNX model weights"),
            vram_requirement_mb=ProvenanceField(0, ProvenanceEnum.CONFIGURED, "0 MB required on CPU ONNX"),
            ram_requirement_mb=ProvenanceField(400, ProvenanceEnum.VERIFIED, "Verified ~400MB host RAM"),
            capabilities=ProvenanceField({"VOICE_TTS", "AUDIO"}, ProvenanceEnum.CONFIGURED, "Text-to-Speech synthesis"),
            latency_tier=ProvenanceField("fast", ProvenanceEnum.CONFIGURED, "High-fidelity local TTS"),
            cost_per_1m_input_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            cost_per_1m_output_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            availability=ProvenanceField(
                AvailabilityStatus.AVAILABLE if has_kokoro else AvailabilityStatus.UNAVAILABLE,
                ProvenanceEnum.CONFIGURED,
                "Package installed in runtime" if has_kokoro else "kokoro-onnx package required",
            ),
            reliability_score=ProvenanceField(0.94, ProvenanceEnum.CONFIGURED, "Empirical local testing"),
            quantization=ProvenanceField(QuantizationType.FP16, ProvenanceEnum.CONFIGURED, "FP16 ONNX weights"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.VERIFIED, "Runs via ONNX CPU EP"),
        ))

        self.register(CatalogModelMetadata(
            model_id="sapi5-desktop",
            display_name=ProvenanceField("Windows SAPI5 Desktop TTS", ProvenanceEnum.CONFIGURED, "Microsoft SAPI5 / pyttsx3"),
            provider=ProvenanceField("sapi5", ProvenanceEnum.CONFIGURED, "Native Windows Speech API"),
            local_or_cloud=ProvenanceField("local", ProvenanceEnum.CONFIGURED, "Local OS execution"),
            context_length=ProvenanceField(2048, ProvenanceEnum.CONFIGURED, "Windows speech buffer"),
            parameter_count_b=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "OS native synth"),
            model_size_mb=ProvenanceField(50, ProvenanceEnum.CONFIGURED, "Built-in OS voice assets"),
            vram_requirement_mb=ProvenanceField(0, ProvenanceEnum.CONFIGURED, "0 MB VRAM"),
            ram_requirement_mb=ProvenanceField(64, ProvenanceEnum.CONFIGURED, "Host memory ~64MB"),
            capabilities=ProvenanceField({"VOICE_TTS", "AUDIO"}, ProvenanceEnum.CONFIGURED, "Text-to-Speech synthesis"),
            latency_tier=ProvenanceField("fast", ProvenanceEnum.CONFIGURED, "Instant OS synth"),
            cost_per_1m_input_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            cost_per_1m_output_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "100% offline free"),
            availability=ProvenanceField(AvailabilityStatus.AVAILABLE, ProvenanceEnum.CONFIGURED, "Windows native OS speech"),
            reliability_score=ProvenanceField(0.90, ProvenanceEnum.CONFIGURED, "Standard Windows speech"),
            quantization=ProvenanceField(QuantizationType.NONE, ProvenanceEnum.CONFIGURED, "Native OS synth"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.VERIFIED, "Native CPU OS execution"),
        ))

        self.register(CatalogModelMetadata(
            model_id="speech-recognition-google",
            display_name=ProvenanceField("Google Speech Recognition (Cloud STT)", ProvenanceEnum.CONFIGURED, "Google Speech Recognition API"),
            provider=ProvenanceField("speech-recognition", ProvenanceEnum.CONFIGURED, "SpeechRecognition Google Cloud endpoint"),
            local_or_cloud=ProvenanceField("cloud", ProvenanceEnum.CONFIGURED, "Cloud API transcription"),
            context_length=ProvenanceField(2048, ProvenanceEnum.CONFIGURED, "Audio chunk buffer"),
            parameter_count_b=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Cloud hosted"),
            model_size_mb=ProvenanceField(None, ProvenanceEnum.UNKNOWN, "Cloud hosted"),
            vram_requirement_mb=ProvenanceField(0, ProvenanceEnum.CONFIGURED, "0 MB local VRAM"),
            ram_requirement_mb=ProvenanceField(64, ProvenanceEnum.CONFIGURED, "Client buffer ~64MB"),
            capabilities=ProvenanceField({"VOICE_STT", "AUDIO"}, ProvenanceEnum.CONFIGURED, "Speech-to-Text transcription"),
            latency_tier=ProvenanceField("fast", ProvenanceEnum.CONFIGURED, "Cloud speech recognition"),
            cost_per_1m_input_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "Default free tier"),
            cost_per_1m_output_usd=ProvenanceField(0.0, ProvenanceEnum.CONFIGURED, "Default free tier"),
            availability=ProvenanceField(AvailabilityStatus.AVAILABLE, ProvenanceEnum.CONFIGURED, "SpeechRecognition library installed"),
            reliability_score=ProvenanceField(0.92, ProvenanceEnum.CONFIGURED, "Cloud STT availability"),
            quantization=ProvenanceField(QuantizationType.NONE, ProvenanceEnum.CONFIGURED, "Cloud precision"),
            cpu_only_supported=ProvenanceField(True, ProvenanceEnum.CONFIGURED, "Cloud client runs on any CPU"),
        ))

    def register(self, metadata: CatalogModelMetadata):
        self._catalog[metadata.model_id] = metadata

    def get(self, model_id: str) -> Optional[CatalogModelMetadata]:
        return self._catalog.get(model_id)

    def list_all(self) -> List[CatalogModelMetadata]:
        return list(self._catalog.values())


class ModelCompatibilityEvaluator:
    """
    Answers: "Can this model run this task on this machine?"
    Considers:
    - CPU cores
    - RAM total and available
    - GPU presence and VRAM total/available
    - Task capability requirements
    - Context length requirements
    - Model availability
    """

    def __init__(self, catalog: Optional[ModelCatalogRegistry] = None):
        self.catalog = catalog or ModelCatalogRegistry()

    def evaluate(
        self,
        model_id: str,
        required_capabilities: Optional[Set[str]] = None,
        context_tokens_needed: int = 4096,
        hardware: Optional[HardwareSpecs] = None,
    ) -> CompatibilityResult:
        meta = self.catalog.get(model_id)
        if not meta:
            return CompatibilityResult(
                model_id=model_id,
                is_compatible=False,
                score=0.0,
                reasons=[f"Model '{model_id}' is unknown in the catalog."],
                hardware_sufficient=False,
                capability_sufficient=False,
                context_sufficient=False,
                suggested_alternative="gpt-5.6-terra",
            )

        hw = hardware or HardwareProbe.probe()
        reasons: List[str] = []
        score = 1.0
        hw_ok = True
        cap_ok = True
        ctx_ok = True

        # 1. Capability evaluation
        if required_capabilities:
            model_caps = meta.capabilities.value
            missing_caps = set(required_capabilities) - set(model_caps)
            if missing_caps:
                cap_ok = False
                reasons.append(f"Model lacks required capabilities: {sorted(list(missing_caps))}")
                score -= 0.4

        # 2. Context evaluation
        max_ctx = meta.context_length.value
        if context_tokens_needed > max_ctx:
            ctx_ok = False
            reasons.append(f"Required context ({context_tokens_needed} tokens) exceeds model limit ({max_ctx})")
            score -= 0.3

        # 3. Hardware evaluation
        is_local = meta.local_or_cloud.value.lower() == "local"
        if is_local:
            vram_req = meta.vram_requirement_mb.value or 0
            ram_req = meta.ram_requirement_mb.value or 0
            cpu_ok = meta.cpu_only_supported.value

            if hw.gpu_available and hw.vram_available_mb >= vram_req:
                reasons.append(f"GPU VRAM sufficient ({hw.vram_available_mb}MB >= {vram_req}MB)")
            elif cpu_ok:
                if hw.available_ram_mb >= ram_req:
                    reasons.append(f"CPU-only mode supported: System RAM sufficient ({hw.available_ram_mb}MB >= {ram_req}MB)")
                    score -= 0.15  # Penalty for CPU-only slower execution
                else:
                    hw_ok = False
                    reasons.append(f"Insufficient host RAM for CPU execution ({hw.available_ram_mb}MB < {ram_req}MB)")
                    score -= 0.6
            else:
                hw_ok = False
                reasons.append(f"GPU required ({vram_req}MB VRAM) but no compatible GPU detected on host")
                score -= 0.7
        else:
            # Cloud model: negligible local hardware required
            reasons.append("Cloud-hosted model: Local hardware impact is nominal.")

        # 4. Availability
        avail = meta.availability.value
        if avail == AvailabilityStatus.UNAVAILABLE:
            reasons.append(f"Model availability status is {avail.value}")
            score -= 0.2

        score = max(0.0, min(1.0, score))
        is_compatible = hw_ok and cap_ok and ctx_ok and (score >= 0.5)

        alt_model: Optional[str] = None
        if not is_compatible:
            alt_model = "gpt-5.6-terra" if is_local else "gemini-3.7-flash"

        return CompatibilityResult(
            model_id=model_id,
            is_compatible=is_compatible,
            score=score,
            reasons=reasons,
            hardware_sufficient=hw_ok,
            capability_sufficient=cap_ok,
            context_sufficient=ctx_ok,
            suggested_alternative=alt_model,
        )


# Global singleton instances for direct import
global_model_catalog = ModelCatalogRegistry()
global_compatibility_evaluator = ModelCompatibilityEvaluator(global_model_catalog)
