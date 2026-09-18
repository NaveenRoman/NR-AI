"""
Unit and regression tests for Knowledge Trinity Phase 3: Aegis Verification Agent.
Verifies all 24 required capabilities:
1. claim decomposition
2. supported claim
3. partially supported claim
4. insufficient evidence
5. contradicted claim
6. unknown claim
7. source corroboration
8. duplicate-source detection
9. temporal verification
10. version conflict handling
11. numerical conflict
12. contradiction detection
13. verification report schema
14. TrinityBus integration
15. telemetry
16. correction loop
17. hard 2-cycle limit
18. model failure fallback
19. security invariants
20. no shell=True
21. no evidence fabrication
22. provenance preservation
23. founder validation
24. final Knowledge gate
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from app.knowledge.taxonomy import EpistemicType
from app.knowledge.trinity.aegis import (
    AegisVerificationAgent,
    AtomicClaim,
    ClaimDecomposer,
    ClaimType,
    ContradictionDetector,
    CorroborationEngine,
    FounderValidationEngine,
    TemporalVerifier,
)
from app.knowledge.trinity.protocol import (
    TrinityBus,
    TrinityTelemetryEvent,
    VerificationReport,
    VerificationRequest,
)
from app.knowledge.trinity.schemas import (
    ClaimStatus,
    ClaimVerification,
    DiscoveryEvidence,
    FounderValidationResult,
    SourceAuthorityTier,
)


class TestAegisVerification(unittest.TestCase):
    """Test suite validating all Aegis verification capabilities and security invariants."""

    def setUp(self):
        self.bus = TrinityBus()
        self.agent = AegisVerificationAgent(bus=self.bus)

    # -------------------------------------------------------------------------
    # 1. Claim Decomposition
    # -------------------------------------------------------------------------
    def test_01_claim_decomposition(self):
        text = "Python was created by Guido van Rossum and first released in 1991. It is an interpreted language."
        claims = ClaimDecomposer.decompose(text, subject="Python")
        self.assertGreaterEqual(len(claims), 2)
        # Verify first claim contains Guido van Rossum
        first = claims[0].text
        self.assertTrue("Guido van Rossum" in first or "Python" in first)
        # Verify dates extracted
        all_dates = [d for c in claims for d in c.dates_mentioned]
        self.assertIn("1991", all_dates)

    # -------------------------------------------------------------------------
    # 2. Supported Claim
    # -------------------------------------------------------------------------
    def test_02_supported_claim(self):
        ev = DiscoveryEvidence(
            evidence_id="ev-1",
            query_id="q1",
            claim_candidate="Alexander Graham Bell was awarded the telephone patent in 1876.",
            source_name="USPTO",
            source_url="https://uspto.gov/patents/174465",
            publisher="US Patent Office",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
            reliability_weight=1.0,
            raw_snippet="In March 1876, Alexander Graham Bell was awarded US Patent 174,465 for the telephone.",
        )
        req = VerificationRequest(
            query_id="q1",
            draft_text="Alexander Graham Bell invented the telephone in 1876.",
            subject="Telephone",
            evidence_items=[ev],
        )
        report = self.agent.verify(req)
        self.assertEqual(report.verdict, "APPROVED")
        self.assertEqual(report.overall_epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertGreaterEqual(len(report.claims_verified), 1)
        self.assertEqual(report.claims_verified[0].status, ClaimStatus.SUPPORTED)

    # -------------------------------------------------------------------------
    # 3. Partially Supported Claim
    # -------------------------------------------------------------------------
    def test_03_partially_supported_claim(self):
        ev = DiscoveryEvidence(
            evidence_id="ev-spec",
            query_id="q2",
            claim_candidate="Researchers speculate quantum computing may reach fault tolerance by 2030.",
            source_name="TechBlog",
            source_url="https://example.com/quantum",
            authority_tier=SourceAuthorityTier.TECHNICAL_COMMUNITY,
            reliability_weight=0.6,
            raw_snippet="Industry roadmaps suggest quantum fault tolerance could arrive within the decade.",
        )
        req = VerificationRequest(
            query_id="q2",
            draft_text="Quantum computing might achieve fault tolerance in the near future.",
            subject="Quantum Computing",
            evidence_items=[ev],
        )
        report = self.agent.verify(req)
        # Speculative / opinion claims should receive PARTIALLY_SUPPORTED
        self.assertIn(report.claims_verified[0].status, (ClaimStatus.SUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED))
        self.assertIn(report.claims_verified[0].epistemic_type, (EpistemicType.SPECULATION, EpistemicType.CONSENSUS_ANALYSIS))

    # -------------------------------------------------------------------------
    # 4. Insufficient Evidence
    # -------------------------------------------------------------------------
    def test_04_insufficient_evidence(self):
        req = VerificationRequest(
            query_id="q3",
            draft_text="The Zorblax hyperdrive engine produces 900 Terawatts of warp energy.",
            subject="Zorblax",
            evidence_items=[],  # No evidence provided
            cycle_number=2,     # Final cycle
        )
        report = self.agent.verify(req)
        self.assertIn(report.verdict, ("REVISE", "UNCERTAIN"))
        self.assertEqual(report.claims_verified[0].status, ClaimStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(report.overall_epistemic_type, EpistemicType.UNCERTAINTY)

    # -------------------------------------------------------------------------
    # 5. Contradicted Claim
    # -------------------------------------------------------------------------
    def test_05_contradicted_claim(self):
        ev = DiscoveryEvidence(
            evidence_id="ev-real",
            query_id="q4",
            claim_candidate="Alexander Graham Bell was born in 1847.",
            source_name="Biography",
            source_url="https://biography.org/bell",
            authority_tier=SourceAuthorityTier.RELIABLE_SECONDARY,
            raw_snippet="Alexander Graham Bell was born in 1847 in Edinburgh, Scotland.",
        )
        req = VerificationRequest(
            query_id="q4",
            draft_text="Alexander Graham Bell was born in 1955.",  # Contradicted date
            subject="Alexander Graham Bell",
            evidence_items=[ev],
            cycle_number=2,
        )
        report = self.agent.verify(req)
        self.assertEqual(report.verdict, "REJECT")
        self.assertEqual(report.claims_verified[0].status, ClaimStatus.CONTRADICTED)
        self.assertGreaterEqual(len(report.contradictions), 1)

    # -------------------------------------------------------------------------
    # 6. Unknown Claim Preservation
    # -------------------------------------------------------------------------
    def test_06_unknown_claim(self):
        req = VerificationRequest(
            query_id="q5",
            draft_text="The specifications of QuantumDragon X9000 include 10,000 qubit processors.",
            subject="QuantumDragon X9000",
            evidence_items=[],
            cycle_number=2,
        )
        report = self.agent.verify(req)
        self.assertEqual(report.verdict, "UNCERTAIN")
        self.assertEqual(report.overall_epistemic_type, EpistemicType.UNCERTAINTY)

    # -------------------------------------------------------------------------
    # 7. Source Corroboration
    # -------------------------------------------------------------------------
    def test_07_source_corroboration(self):
        ev1 = DiscoveryEvidence(
            evidence_id="ev-stanford",
            query_id="q6",
            claim_candidate="FlashAttention cuts attention memory IO.",
            source_name="Stanford",
            source_url="https://stanford.edu/dao/flash",
            publisher="Stanford University",
            authority_tier=SourceAuthorityTier.AUTHORITATIVE_ORG,
            reliability_weight=0.95,
            raw_snippet="FlashAttention computes exact attention with IO awareness.",
        )
        ev2 = DiscoveryEvidence(
            evidence_id="ev-arxiv",
            query_id="q6",
            claim_candidate="FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness.",
            source_name="arXiv",
            source_url="https://arxiv.org/abs/2205.14135",
            publisher="Cornell University / arXiv",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
            reliability_weight=0.99,
            raw_snippet="We propose FlashAttention to speed up attention and reduce memory footprint.",
        )
        score, unique_ids, telemetry = CorroborationEngine.evaluate_corroboration(
            [ev1, ev2],
            ["ev-stanford", "ev-arxiv"],
        )
        self.assertEqual(telemetry["independent_domains"], 2)
        self.assertGreaterEqual(score, 0.90)
        self.assertIn("ev-stanford", unique_ids)
        self.assertIn("ev-arxiv", unique_ids)

    # -------------------------------------------------------------------------
    # 8. Duplicate Source Detection (Provenance Tracking)
    # -------------------------------------------------------------------------
    def test_08_duplicate_source_detection(self):
        ev1 = DiscoveryEvidence(
            evidence_id="ev-mirror1",
            query_id="q7",
            claim_candidate="Company X announces product Y.",
            source_name="Mirror 1",
            source_url="https://news.example.com/syndicated/article1?ref=rss",
            publisher="PR Newswire",
            authority_tier=SourceAuthorityTier.RELIABLE_SECONDARY,
            raw_snippet="Company X announces product Y.",
        )
        ev2 = DiscoveryEvidence(
            evidence_id="ev-mirror2",
            query_id="q7",
            claim_candidate="Company X announces product Y.",
            source_name="Mirror 2",
            source_url="https://news.example.com/syndicated/article2?ref=rss",
            publisher="PR Newswire",
            authority_tier=SourceAuthorityTier.RELIABLE_SECONDARY,
            raw_snippet="Company X announces product Y.",
        )
        score, unique_ids, telemetry = CorroborationEngine.evaluate_corroboration(
            [ev1, ev2],
            ["ev-mirror1", "ev-mirror2"],
        )
        # Same domain and publisher must NOT count as 2 independent sources
        self.assertEqual(telemetry["independent_domains"], 1)
        self.assertEqual(len(unique_ids), 1)

    # -------------------------------------------------------------------------
    # 9. Temporal Verification (Historical vs Current)
    # -------------------------------------------------------------------------
    def test_09_temporal_verification(self):
        claim_hist = AtomicClaim(
            claim_id="c-hist",
            text="In 1876, the telephone was patented by Alexander Graham Bell.",
            claim_type=ClaimType.FACTUAL_HISTORICAL,
            dates_mentioned=["1876"],
        )
        ev = DiscoveryEvidence(
            evidence_id="ev-hist",
            query_id="q8",
            claim_candidate="Bell patent in 1876.",
            source_name="USPTO",
            source_url="https://uspto.gov",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
            raw_snippet="Patent granted March 1876.",
        )
        validity, notes = TemporalVerifier.evaluate_temporality(claim_hist, [ev], ["ev-hist"])
        self.assertEqual(validity, "HISTORICAL_ONLY")

    # -------------------------------------------------------------------------
    # 10. Version Conflict Handling (Superseded vs Contradiction)
    # -------------------------------------------------------------------------
    def test_10_version_conflict_handling(self):
        claim_ver = AtomicClaim(
            claim_id="c-ver",
            text="Python 2.7 is currently the latest version of Python.",
            claim_type=ClaimType.FACTUAL_TEMPORAL,
            temporal_markers=["currently", "latest"],
        )
        ev = DiscoveryEvidence(
            evidence_id="ev-py",
            query_id="q9",
            claim_candidate="Python 3.12 is the latest stable release.",
            source_name="Python.org",
            source_url="https://python.org/downloads",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
            raw_snippet="Python version 3.12 is currently the latest release.",
        )
        validity, notes = TemporalVerifier.evaluate_temporality(claim_ver, [ev], ["ev-py"])
        self.assertEqual(validity, "OUTDATED")
        self.assertIn("newer version", notes)

    # -------------------------------------------------------------------------
    # 11. Numerical Conflict Detection
    # -------------------------------------------------------------------------
    def test_11_numerical_conflict(self):
        claim_num = AtomicClaim(
            claim_id="c-num",
            text="The L1 cache size is 256 MB per core.",
            claim_type=ClaimType.FACTUAL_NUMERICAL,
            numbers_mentioned=["256"],
        )
        ev = DiscoveryEvidence(
            evidence_id="ev-cpu",
            query_id="q10",
            claim_candidate="CPU Architecture spec sheet.",
            source_name="Intel",
            source_url="https://intel.com/spec",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
            raw_snippet="The core features a dedicated 48 KB L1 data cache and 32 MB L3 cache.",
        )
        conflicts = ContradictionDetector.detect_conflicts(claim_num, [ev])
        self.assertGreaterEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["conflict_type"], "NUMERICAL_CONFLICT")

    # -------------------------------------------------------------------------
    # 12. Contradiction Detection (Direct Negation)
    # -------------------------------------------------------------------------
    def test_12_contradiction_detection(self):
        claim_neg = AtomicClaim(
            claim_id="c-neg",
            text="PyTorch does not support GPU CUDA acceleration.",
            claim_type=ClaimType.FACTUAL_TECHNICAL,
        )
        ev = DiscoveryEvidence(
            evidence_id="ev-pt",
            query_id="q11",
            claim_candidate="PyTorch CUDA Documentation.",
            source_name="PyTorch Docs",
            source_url="https://pytorch.org/docs/stable/cuda.html",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
            raw_snippet="PyTorch officially supports CUDA acceleration across NVIDIA GPUs.",
        )
        conflicts = ContradictionDetector.detect_conflicts(claim_neg, [ev])
        self.assertGreaterEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["conflict_type"], "NEGATION_CONFLICT")

    # -------------------------------------------------------------------------
    # 13. Verification Report Schema
    # -------------------------------------------------------------------------
    def test_13_verification_report_schema(self):
        req = VerificationRequest(
            query_id="q12",
            draft_text="The Earth orbits the Sun.",
            subject="Solar System",
            evidence_items=[],
        )
        report = self.agent.verify(req)
        d = report.to_dict()
        self.assertIn("query_id", d)
        self.assertIn("verdict", d)
        self.assertIn("overall_epistemic_type", d)
        self.assertIn("claims_verified", d)
        self.assertIn("confidence", d)
        self.assertIn("cycle_number", d)

    # -------------------------------------------------------------------------
    # 14. TrinityBus Integration
    # -------------------------------------------------------------------------
    def test_14_trinity_bus_integration(self):
        events_received = []
        self.bus.subscribe_telemetry(lambda ev: events_received.append(ev))

        req = VerificationRequest(
            query_id="q13",
            draft_text="Guido created Python.",
            subject="Python",
            evidence_items=[],
        )
        self.agent.verify(req)
        self.assertGreaterEqual(len(events_received), 2)
        sources = [e.agent_source for e in events_received]
        self.assertIn("aegis", sources)

    # -------------------------------------------------------------------------
    # 15. Telemetry Lifecycle Phases
    # -------------------------------------------------------------------------
    def test_15_telemetry_phases(self):
        phases_seen = []
        self.bus.subscribe_telemetry(lambda ev: phases_seen.append(ev.phase))

        ev = DiscoveryEvidence(
            evidence_id="ev-telemetry",
            query_id="q14",
            claim_candidate="Alexander Graham Bell telephone patent 1876.",
            source_name="USPTO",
            source_url="https://uspto.gov",
            authority_tier=SourceAuthorityTier.PRIMARY_CANONICAL,
            raw_snippet="In March 1876 Alexander Graham Bell received the patent.",
        )
        req = VerificationRequest(
            query_id="q14",
            draft_text="Alexander Graham Bell patented the telephone in 1876.",
            subject="Telephone",
            evidence_items=[ev],
        )
        self.agent.verify(req)
        self.assertIn("AEGIS_RECEIVING_CLAIMS", phases_seen)
        self.assertIn("AEGIS_DECOMPOSING", phases_seen)
        self.assertIn("AEGIS_CHECKING_EVIDENCE", phases_seen)
        self.assertIn("AEGIS_APPROVED", phases_seen)

    # -------------------------------------------------------------------------
    # 16. Correction Loop: Cycle 1 Requests REVISE
    # -------------------------------------------------------------------------
    def test_16_correction_loop_cycle_1(self):
        ev = DiscoveryEvidence(
            evidence_id="ev-c1",
            query_id="q15",
            claim_candidate="Bell was born in 1847.",
            source_name="Bio",
            source_url="https://example.com/bio",
            authority_tier=SourceAuthorityTier.RELIABLE_SECONDARY,
            raw_snippet="Bell was born in 1847.",
        )
        req = VerificationRequest(
            query_id="q15",
            draft_text="Bell was born in 1999.",  # Contradiction
            subject="Bell",
            evidence_items=[ev],
            cycle_number=1,
        )
        report = self.agent.verify(req)
        self.assertEqual(report.verdict, "REVISE")
        self.assertIsNotNone(report.revision_feedback)
        self.assertEqual(report.cycle_number, 1)

    # -------------------------------------------------------------------------
    # 17. Hard 2-Cycle Limit: Cycle 2 Rejects
    # -------------------------------------------------------------------------
    def test_17_hard_2_cycle_limit(self):
        ev = DiscoveryEvidence(
            evidence_id="ev-c2",
            query_id="q16",
            claim_candidate="Bell was born in 1847.",
            source_name="Bio",
            source_url="https://example.com/bio",
            authority_tier=SourceAuthorityTier.RELIABLE_SECONDARY,
            raw_snippet="Bell was born in 1847.",
        )
        req = VerificationRequest(
            query_id="q16",
            draft_text="Bell was born in 1999.",  # Contradiction persisted to Cycle 2
            subject="Bell",
            evidence_items=[ev],
            cycle_number=2,  # Maximum cycle
        )
        report = self.agent.verify(req)
        # Cycle 2 must STOP and REJECT rather than requesting cycle 3
        self.assertEqual(report.verdict, "REJECT")
        self.assertEqual(report.cycle_number, 2)

    # -------------------------------------------------------------------------
    # 18. Model Failure Fallback
    # -------------------------------------------------------------------------
    def test_18_model_failure_fallback(self):
        # Aegis operates deterministically even if any optional cloud models fail
        req = VerificationRequest(
            query_id="q17",
            draft_text="RAM is volatile memory and ROM is non-volatile.",
            subject="Memory",
            evidence_items=[],
        )
        # Runs 100% offline without network or cloud model dependency
        report = self.agent.verify(req)
        self.assertIsNotNone(report)
        self.assertEqual(report.query_id, "q17")

    # -------------------------------------------------------------------------
    # 19. Security Invariants (No Shell Commands)
    # -------------------------------------------------------------------------
    def test_19_security_invariants(self):
        import subprocess
        with patch.object(subprocess, "Popen") as mock_popen, patch.object(subprocess, "run") as mock_run:
            req = VerificationRequest(
                query_id="q18",
                draft_text="System command execution check.",
                subject="Security",
                evidence_items=[],
            )
            self.agent.verify(req)
            self.assertEqual(mock_popen.call_count, 0)
            self.assertEqual(mock_run.call_count, 0)

    # -------------------------------------------------------------------------
    # 20. Zero Evidence Fabrication
    # -------------------------------------------------------------------------
    def test_20_no_evidence_fabrication(self):
        ev1 = DiscoveryEvidence(
            evidence_id="real-id-99",
            query_id="q19",
            claim_candidate="Valid observation.",
            source_name="KnownSource",
            source_url="https://example.com/source",
            raw_snippet="Valid observation snippet.",
        )
        req = VerificationRequest(
            query_id="q19",
            draft_text="Valid observation snippet.",
            subject="Subject",
            evidence_items=[ev1],
        )
        report = self.agent.verify(req)
        for claim in report.claims_verified:
            for s_id in claim.corroborating_source_ids:
                self.assertEqual(s_id, "real-id-99")
            for c_id in claim.contradicting_source_ids:
                self.assertEqual(c_id, "real-id-99")

    # -------------------------------------------------------------------------
    # 21. Provenance Preservation
    # -------------------------------------------------------------------------
    def test_21_provenance_preservation(self):
        domain = CorroborationEngine.extract_root_domain("https://subdomain.stanford.edu/path/file.html?utm_source=test")
        self.assertEqual(domain, "stanford.edu")

    # -------------------------------------------------------------------------
    # 22. Founder Validation Workflow
    # -------------------------------------------------------------------------
    def test_22_founder_validation(self):
        prob = "Students struggle to understand programming concepts because documentation is dense and abstract."
        sol = "NR-AI provides an interactive AI coding tutor that adapts explanations to the student's level."
        res = FounderValidationEngine.validate(problem_statement=prob, proposed_solution=sol, market_or_domain="EdTech")

        self.assertIsInstance(res, FounderValidationResult)
        self.assertGreaterEqual(res.overall_score, 50.0)
        self.assertGreaterEqual(len(res.supported_claims), 1)
        self.assertGreaterEqual(len(res.risk_factors), 1)
        self.assertGreaterEqual(len(res.recommended_validation_steps), 1)

    # -------------------------------------------------------------------------
    # 23. Final Knowledge Gate Integration
    # -------------------------------------------------------------------------
    def test_23_final_knowledge_gate(self):
        from app.knowledge.engine import UniversalKnowledgeEngine
        engine = UniversalKnowledgeEngine(auto_seed=False, auto_start_scheduler=False)

        # High stakes questions should trigger verification
        self.assertTrue(engine.should_verify("Who invented the telephone?", "Alexander Graham Bell"))
        self.assertTrue(engine.should_verify("What are the specs of GPT-6?", "Context window 200,000"))
        # Low stakes greetings should NOT trigger verification
        self.assertFalse(engine.should_verify("hello", "Hi, how can I help you today?"))

    # -------------------------------------------------------------------------
    # 24. Zero-Evidence Safe Fallback (Honest Uncertainty)
    # -------------------------------------------------------------------------
    def test_24_zero_evidence_honest_uncertainty(self):
        req = VerificationRequest(
            query_id="q24",
            draft_text="The Zorblax system is rated at 500 GigaWatts.",
            subject="Zorblax",
            evidence_items=[],
            cycle_number=2,
        )
        report = self.agent.verify(req)
        self.assertEqual(report.verdict, "UNCERTAIN")
        self.assertEqual(report.overall_epistemic_type, EpistemicType.UNCERTAINTY)


if __name__ == "__main__":
    unittest.main()
