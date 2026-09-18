"""
NR-AI Knowledge Trinity Architecture Package.
NOVA (Discovery) + KNOWLEDGE (Coordination) + AEGIS (Verification)

Coordinates:
- Autonomous, multi-source evidence discovery (Nova)
- Zero-hallucination fact-checking and claim-level verification (Aegis)
- Multi-turn conversational reasoning and unified user presentation (Knowledge)
- Real-time backend telemetry without fake animations
"""

from app.knowledge.trinity.nova import (
    ArXivDiscoverySource,
    ContinuousDiscoveryScheduler,
    DiscoverySource,
    DuckDuckGoDiscoverySource,
    GitHubDiscoverySource,
    NewsFeedDiscoverySource,
    NovaDiscoveryAgent,
    PublicMediaDiscoverySource,
    SSRFGuard,
    WikipediaDiscoverySource,
    canonicalize_url,
    compute_content_hash,
)
from app.knowledge.trinity.protocol import (
    DiscoveryRequest,
    DiscoveryResponse,
    TrinityBus,
    TrinityMessageType,
    TrinityTelemetryEvent,
    VerificationReport,
    VerificationRequest,
)
from app.knowledge.trinity.schemas import (
    ClaimStatus,
    ClaimVerification,
    DiscoveryEvidence,
    FounderValidationResult,
    MediaItem,
    MediaType,
    SourceAuthorityTier,
)

__all__ = [
    "SourceAuthorityTier",
    "MediaType",
    "MediaItem",
    "DiscoveryEvidence",
    "ClaimStatus",
    "ClaimVerification",
    "FounderValidationResult",
    "TrinityMessageType",
    "DiscoveryRequest",
    "DiscoveryResponse",
    "VerificationRequest",
    "VerificationReport",
    "TrinityTelemetryEvent",
    "TrinityBus",
    "NovaDiscoveryAgent",
    "DiscoverySource",
    "SSRFGuard",
    "ArXivDiscoverySource",
    "WikipediaDiscoverySource",
    "DuckDuckGoDiscoverySource",
    "GitHubDiscoverySource",
    "NewsFeedDiscoverySource",
    "PublicMediaDiscoverySource",
    "ContinuousDiscoveryScheduler",
    "canonicalize_url",
    "compute_content_hash",
]
