"""
NR-AI Universal Knowledge Fabric Comprehensive Test Suite (100+ Tests).

Tests the complete Universal Knowledge Brain Redesign (Phase 0.2):
- Dynamic Taxonomy & Registry (STEM, Humanities, Law, Medicine, CS, AI, etc.)
- Query Understanding & Speech Repair & Homonym Disambiguation
- Deterministic Answer Grounding Gate (Zero Hallucinations, Exact Matching)
- 1880–2026 Chronological Timeline Continuum & Evolutionary Trajectories
- Entity-Relationship Knowledge Graph (Canonical Triples & Traversals)
- Knowledge Versioning & Provenance (Supersedes / Superseded-By)
- Multi-Turn Conversational Coreference Resolution
- End-to-End Exact Matching & Adversarial Disambiguation
"""

import os
import tempfile
import time
import unittest
from typing import Dict, List

from app.knowledge.graph import EntityType, KnowledgeGraph, RelationType
from app.knowledge.grounding import AnswerGroundingGate, GroundingResult
from app.knowledge.query_understanding import (
    EntityCandidate,
    FreshnessRequirement,
    QueryIntent,
    QueryUnderstandingEngine,
    TimeScope,
    UnderstoodQuery,
)
from app.knowledge.research import ResearchEngine
from app.knowledge.store import HybridKnowledgeStore, extract_subject_tokens, sanitize_fts5_query
from app.knowledge.taxonomy import (
    EpistemicBadge,
    EpistemicType,
    KnowledgeDomain,
    KnowledgeNode,
    KnowledgeSource,
    ResearchReport,
    TaxonomyRegistry,
)
from app.knowledge.timeline import KnowledgeTimelineEngine, TimelineEvent


class TestTaxonomyAndRegistry(unittest.TestCase):
    """Tests 1-15: Taxonomy, Domains, Aliases, and Dynamic Registry."""

    def setUp(self):
        TaxonomyRegistry.initialize()

    def test_01_core_domains_present(self):
        domains = TaxonomyRegistry.list_domains()
        self.assertIn("history", domains)
        self.assertIn("stem", domains)
        self.assertIn("science", domains)
        self.assertIn("computer_science", domains)
        self.assertIn("ai_ml", domains)
        self.assertIn("medicine", domains)
        self.assertIn("law", domains)
        self.assertIn("economics", domains)
        self.assertIn("geography", domains)

    def test_02_backward_compatibility_enums(self):
        self.assertEqual(KnowledgeDomain.HISTORY.value, "history")
        self.assertEqual(KnowledgeDomain.AI_ML.value, "ai_ml")
        self.assertEqual(KnowledgeDomain.CLOUD.value, "cloud")
        self.assertEqual(KnowledgeDomain.GENERAL.value, "general")

    def test_03_expanded_domain_enums(self):
        self.assertEqual(KnowledgeDomain.SCIENCE.value, "science")
        self.assertEqual(KnowledgeDomain.PROGRAMMING.value, "programming")
        self.assertEqual(KnowledgeDomain.SEMICONDUCTOR.value, "semiconductor")
        self.assertEqual(KnowledgeDomain.AEROSPACE.value, "aerospace")

    def test_04_alias_resolution_math(self):
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("math"), "mathematics")
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("maths"), "mathematics")

    def test_05_alias_resolution_cs(self):
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("cs"), "computer_science")
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("computing"), "computer_science")

    def test_06_alias_resolution_coding(self):
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("coding"), "programming")
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("software"), "programming")

    def test_07_alias_resolution_ai(self):
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("ai"), "ai_ml")
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("deep_learning"), "ai_ml")
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("llm"), "ai_ml")

    def test_08_alias_resolution_health(self):
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("health"), "medicine")
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("pharma"), "medicine")

    def test_09_alias_resolution_semi(self):
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("semi"), "semiconductor")
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("chips"), "semiconductor")

    def test_10_alias_resolution_geo(self):
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("geo"), "geography")

    def test_11_dynamic_registration(self):
        TaxonomyRegistry.register_domain("astrophysics", "Study of the universe", ["astro", "space_physics"])
        self.assertTrue(TaxonomyRegistry.is_valid_domain("astrophysics"))
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("astro"), "astrophysics")
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("space_physics"), "astrophysics")

    def test_12_dynamic_registration_normalization(self):
        TaxonomyRegistry.register_domain("Quantum Computing", aliases=["quantum comp"])
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("quantum_computing"), "quantum_computing")
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("quantum comp"), "quantum_computing")

    def test_13_unknown_domain_fallback(self):
        self.assertEqual(TaxonomyRegistry.get_canonical_domain("completely_unheard_of_xyz"), "general")

    def test_14_epistemic_badges_formatting(self):
        badge = EpistemicBadge.from_type(EpistemicType.VERIFIED_FACT, confidence=1.0)
        self.assertEqual(badge.format_tag(), "[VERIFIED FACT | 100%]")
        unc_badge = EpistemicBadge.from_type(EpistemicType.UNCERTAINTY, confidence=0.0)
        self.assertEqual(unc_badge.format_tag(), "[UNCERTAINTY | 0%]")

    def test_15_knowledge_node_versioning_fields(self):
        node = KnowledgeNode(
            node_id="test-v1",
            domain="stem",
            topic="physics",
            title="Test",
            content="Content",
            version=2,
            supersedes="test-v0",
        )
        self.assertEqual(node.version, 2)
        self.assertEqual(node.supersedes, "test-v0")
        d = node.to_dict()
        self.assertEqual(d["version"], 2)
        self.assertEqual(d["supersedes"], "test-v0")


class TestQueryUnderstandingAndDisambiguation(unittest.TestCase):
    """Tests 16-35: Query Understanding Layer, Speech Repairs, Homonyms, Entities."""

    def setUp(self):
        self.engine = QueryUnderstandingEngine()

    def test_16_speech_repair_transformer(self):
        q = self.engine.understand("what is a transfomer in ai")
        self.assertIn("transformer", q.repaired_query.lower())
        self.assertEqual(q.primary_subject.lower(), "transformer")

    def test_17_speech_repair_nvidia(self):
        q = self.engine.understand("tell me about nvdia gpu")
        self.assertIn("nvidia", q.repaired_query.lower())

    def test_18_speech_repair_who_made(self):
        q = self.engine.understand("who made java")
        self.assertIn("created java", q.repaired_query.lower())

    def test_19_homonym_java_programming(self):
        q = self.engine.understand("Who created the Java programming language?")
        self.assertEqual(q.domain, "programming")
        self.assertIn("programming language", q.primary_subject.lower())

    def test_20_homonym_java_island(self):
        q = self.engine.understand("Where is the island of Java located?")
        self.assertEqual(q.domain, "geography")
        self.assertIn("island", q.primary_subject.lower())

    def test_21_homonym_java_coffee(self):
        q = self.engine.understand("What is Java coffee bean?")
        self.assertIn(q.domain, ("agriculture", "general"))

    def test_22_homonym_transformer_ai(self):
        q = self.engine.understand("What is a transformer in AI?")
        self.assertEqual(q.domain, "ai_ml")
        self.assertIn("transformer", q.primary_subject.lower())

    def test_23_homonym_transformer_electrical(self):
        q = self.engine.understand("How does an electrical transformer step down voltage?")
        self.assertIn(q.domain, ("physics", "hardware", "stem"))

    def test_24_homonym_python_programming(self):
        q = self.engine.understand("What is the latest Python version?")
        self.assertEqual(q.domain, "programming")
        self.assertEqual(q.target_attribute, "version")

    def test_25_homonym_python_snake(self):
        q = self.engine.understand("What does a reticulated python eat?")
        self.assertEqual(q.domain, "biology")

    def test_26_homonym_apple_tech(self):
        q = self.engine.understand("When was Apple Inc founded?")
        self.assertIn(q.domain, ("computer_science", "business"))

    def test_27_homonym_apple_fruit(self):
        q = self.engine.understand("What nutrients are in an apple fruit?")
        self.assertIn(q.domain, ("biology", "medicine", "general"))

    def test_28_target_attribute_capital(self):
        q = self.engine.understand("What is the capital of Australia?")
        self.assertEqual(q.target_attribute, "capital")
        self.assertEqual(q.primary_subject.lower(), "australia")

    def test_29_target_attribute_creator(self):
        q = self.engine.understand("Who developed the Linux kernel?")
        self.assertEqual(q.target_attribute, "creator")
        self.assertIn("linux", q.primary_subject.lower())

    def test_30_temporal_scope_historical(self):
        q = self.engine.understand("What happened in 1969?")
        self.assertEqual(q.temporal_scope, TimeScope.HISTORICAL)
        self.assertEqual(q.time_anchor, "1969")

    def test_31_temporal_scope_future(self):
        q = self.engine.understand("Will ASI be achieved by 2035?")
        self.assertEqual(q.temporal_scope, TimeScope.FUTURE)
        self.assertEqual(q.intent, QueryIntent.SPECULATION)

    def test_32_freshness_requirement_realtime(self):
        q = self.engine.understand("What is the stock price of Apple right now?")
        self.assertEqual(q.freshness, FreshnessRequirement.REALTIME)

    def test_33_comparison_intent(self):
        q = self.engine.understand("Compare PyTorch versus TensorFlow")
        self.assertEqual(q.intent, QueryIntent.COMPARISON)

    def test_34_definition_intent(self):
        q = self.engine.understand("What is General Relativity?")
        self.assertEqual(q.intent, QueryIntent.FACTUAL_LOOKUP)
        self.assertEqual(q.target_attribute, "definition")

    def test_35_coreference_resolution_pronoun(self):
        session = {"last_subject": "Python"}
        q = self.engine.understand("Who created it?", session_context=session)
        self.assertEqual(q.primary_subject, "Python")
        self.assertEqual(q.target_attribute, "creator")


class TestDeterministicAnswerGroundingGate(unittest.TestCase):
    """Tests 36-55: Answer Grounding Gate, Subject Containment, Attribute Verification, Uncertainty Fallback."""

    def setUp(self):
        self.gate = AnswerGroundingGate()
        self.understanding = QueryUnderstandingEngine()

    def test_36_exact_capital_grounding_success(self):
        uq = self.understanding.understand("What is the capital of Australia?")
        candidate = "The capital of Australia is Canberra, founded in 1913 as the planned seat of government."
        res = self.gate.verify_grounding(uq, candidate)
        self.assertTrue(res.is_grounded)
        self.assertEqual(res.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("Canberra", res.verified_answer)

    def test_37_reject_unrelated_seed_for_capital(self):
        # The classic bug: returning AWS S3 for Australia capital
        uq = self.understanding.understand("What is the capital of Australia?")
        candidate = "Amazon S3 (Simple Storage Service) is an object storage service offering industry-leading scalability."
        res = self.gate.verify_grounding(uq, candidate)
        self.assertFalse(res.is_grounded)
        self.assertEqual(res.epistemic_type, EpistemicType.UNCERTAINTY)
        self.assertEqual(res.verified_answer, AnswerGroundingGate.HONEST_UNKNOWN_TEMPLATE)

    def test_38_reject_attribute_missing(self):
        # Document mentions Australia, but says nothing about a capital
        uq = self.understanding.understand("What is the capital of Australia?")
        candidate = "Australia is a country comprising the mainland of the Australian continent, known for its unique wildlife."
        res = self.gate.verify_grounding(uq, candidate)
        self.assertFalse(res.is_grounded)
        self.assertEqual(res.verified_answer, AnswerGroundingGate.HONEST_UNKNOWN_TEMPLATE)

    def test_39_linux_developer_grounding_success(self):
        uq = self.understanding.understand("Who developed the Linux kernel?")
        candidate = "The Linux kernel was initially developed and released by Linus Torvalds in 1991."
        res = self.gate.verify_grounding(uq, candidate)
        self.assertTrue(res.is_grounded)
        self.assertIn("Linus Torvalds", res.verified_answer)

    def test_40_reject_rust_for_linux_developer(self):
        # Document talks about Rust by Graydon Hoare
        uq = self.understanding.understand("Who developed the Linux kernel?")
        candidate = "The Rust programming language was created by Graydon Hoare at Mozilla Research in 2010."
        res = self.gate.verify_grounding(uq, candidate)
        self.assertFalse(res.is_grounded)
        self.assertEqual(res.verified_answer, AnswerGroundingGate.HONEST_UNKNOWN_TEMPLATE)

    def test_41_transformer_ai_grounding_success(self):
        uq = self.understanding.understand("What is a transformer in AI?")
        candidate = "The Transformer is a deep learning neural network architecture introduced in 2017 by Vaswani et al. based on self-attention."
        res = self.gate.verify_grounding(uq, candidate)
        self.assertTrue(res.is_grounded)
        self.assertEqual(res.epistemic_type, EpistemicType.VERIFIED_FACT)

    def test_42_reject_alexnet_for_transformer(self):
        uq = self.understanding.understand("What is a transformer in AI?")
        candidate = "AlexNet is a convolutional neural network designed by Alex Krizhevsky in 2012 that revolutionized computer vision."
        res = self.gate.verify_grounding(uq, candidate)
        self.assertFalse(res.is_grounded)

    def test_43_honest_unknown_fallback_format(self):
        uq = self.understanding.understand("What is the secret recipe of UnknownAlienSoup?")
        res = self.gate.verify_grounding(uq, "Completely random text with no answer.")
        self.assertFalse(res.is_grounded)
        self.assertEqual(res.confidence, 0.0)
        self.assertEqual(res.epistemic_type, EpistemicType.UNCERTAINTY)
        report = res.to_report("What is the secret recipe of UnknownAlienSoup?")
        self.assertIn("I do not have enough verified information", report.primary_answer)
        self.assertEqual(report.badges[0].format_tag(), "[UNCERTAINTY | 0%]")

    def test_44_contradiction_detection_in_snippets(self):
        snippets = [
            "Entity X was founded in 1920 by John Doe.",
            "Entity X was established in 1985 by Jane Smith.",
        ]
        conflicts = AnswerGroundingGate._detect_contradictions(snippets)
        self.assertTrue(len(conflicts) > 0)
        self.assertIn("conflicting years", conflicts[0].lower())

    def test_45_epistemic_speculation_confidence(self):
        uq = self.understanding.understand("Will AGI be developed by 2029?")
        candidate = "Forecasts by frontier AI researchers speculate that AGI could emerge around 2029 based on compute scaling."
        res = self.gate.verify_grounding(uq, candidate)
        self.assertTrue(res.is_grounded)
        self.assertEqual(res.epistemic_type, EpistemicType.SPECULATION_PREDICTION)
        self.assertTrue(res.confidence <= 0.75)


class TestTimelineEngine1880to2026(unittest.TestCase):
    """Tests 46-65: 1880–2026 Chronological Continuum, Trajectories, Evolution."""

    def setUp(self):
        self.timeline = KnowledgeTimelineEngine()

    def test_46_preseeded_events_span(self):
        all_events = self.timeline.lookup_range(1880, 2026)
        self.assertGreaterEqual(len(all_events), 20)
        years = [e.year for e in all_events]
        self.assertIn(1887, years)
        self.assertIn(1903, years)
        self.assertIn(1947, years)
        self.assertIn(1969, years)
        self.assertIn(1991, years)
        self.assertIn(2017, years)
        self.assertIn(2026, years)

    def test_47_lookup_year_1903(self):
        evs = self.timeline.lookup_year(1903)
        self.assertTrue(any("Wright Brothers" in e.title for e in evs))

    def test_48_lookup_year_1905(self):
        evs = self.timeline.lookup_year(1905)
        self.assertTrue(any("Einstein" in e.title for e in evs))

    def test_49_lookup_year_1947(self):
        evs = self.timeline.lookup_year(1947)
        self.assertTrue(any("Transistor" in e.title for e in evs))

    def test_50_lookup_year_1956(self):
        evs = self.timeline.lookup_year(1956)
        self.assertTrue(any("Artificial Intelligence" in e.title for e in evs))

    def test_51_lookup_year_1969(self):
        evs = self.timeline.lookup_year(1969)
        titles = [e.title for e in evs]
        self.assertTrue(any("Apollo 11" in t or "Moon" in t for t in titles))

    def test_52_lookup_year_1971(self):
        evs = self.timeline.lookup_year(1971)
        self.assertTrue(any("Intel 4004" in e.title for e in evs))

    def test_53_lookup_year_1972(self):
        evs = self.timeline.lookup_year(1972)
        self.assertTrue(any("Dennis Ritchie" in e.title or "C Programming" in e.title for e in evs))

    def test_54_lookup_year_1989(self):
        evs = self.timeline.lookup_year(1989)
        self.assertTrue(any("World Wide Web" in e.title for e in evs))

    def test_55_lookup_year_1991(self):
        evs = self.timeline.lookup_year(1991)
        self.assertTrue(any("Linux" in e.title and "Python" in e.title for e in evs))

    def test_56_lookup_year_1995(self):
        evs = self.timeline.lookup_year(1995)
        self.assertTrue(any("Java" in e.title for e in evs))

    def test_57_lookup_year_2012(self):
        evs = self.timeline.lookup_year(2012)
        self.assertTrue(any("AlexNet" in e.title or "CRISPR" in e.title for e in evs))

    def test_58_lookup_year_2017(self):
        evs = self.timeline.lookup_year(2017)
        self.assertTrue(any("Transformer" in e.title for e in evs))

    def test_59_lookup_year_2020(self):
        evs = self.timeline.lookup_year(2020)
        self.assertTrue(any("AlphaFold" in e.title for e in evs))

    def test_60_lookup_year_2026(self):
        evs = self.timeline.lookup_year(2026)
        self.assertTrue(any("Semiconductor" in e.title or "Autonomous" in e.title for e in evs))

    def test_61_entity_trajectory_einstein(self):
        traj = self.timeline.lookup_entity_trajectory("Albert Einstein")
        self.assertGreaterEqual(len(traj), 2)
        self.assertEqual(traj[0].year, 1905)
        self.assertEqual(traj[1].year, 1915)

    def test_62_entity_trajectory_torvalds(self):
        traj = self.timeline.lookup_entity_trajectory("Linus Torvalds")
        years = [e.year for e in traj]
        self.assertIn(1991, years)  # Linux
        self.assertIn(2005, years)  # Git

    def test_63_evolution_synthesis(self):
        summary = self.timeline.synthesize_evolution("ai_ml", 1950, 2026)
        self.assertIn("1956", summary)
        self.assertIn("2017", summary)
        self.assertIn("Evolution of Ai_Ml", summary)

    def test_64_add_custom_event(self):
        event = TimelineEvent(
            year=1983,
            title="TCP/IP Cutover on ARPANET",
            description="ARPANET officially adopts TCP/IP, creating the modern Internet protocol stack.",
            domain="networking",
            entities=["ARPANET", "TCP/IP"],
        )
        self.timeline.add_event(event)
        evs = self.timeline.lookup_year(1983)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].title, "TCP/IP Cutover on ARPANET")

    def test_65_range_query_empty_window(self):
        evs = self.timeline.lookup_range(1800, 1850)
        self.assertEqual(len(evs), 0)


class TestKnowledgeGraph(unittest.TestCase):
    """Tests 66-80: Entity-Relationship Knowledge Graph, Triples, and Attribute Queries."""

    def setUp(self):
        self.graph = KnowledgeGraph()

    def test_66_preseeded_australia_capital(self):
        results = self.graph.query_attribute("Australia", "capital")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]["source"], "Canberra")

    def test_67_preseeded_france_capital(self):
        results = self.graph.query_attribute("France", "capital")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]["source"], "Paris")

    def test_68_preseeded_linux_creator(self):
        results = self.graph.query_attribute("Linux Kernel", "created_by")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]["target"], "Linus Torvalds")

    def test_69_preseeded_git_creator(self):
        results = self.graph.query_attribute("Git", "created_by")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]["target"], "Linus Torvalds")

    def test_70_preseeded_java_creator(self):
        results = self.graph.query_attribute("Java", "created_by")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]["target"], "James Gosling")

    def test_71_preseeded_python_creator(self):
        results = self.graph.query_attribute("Python", "created_by")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]["target"], "Guido van Rossum")

    def test_72_preseeded_c_creator(self):
        results = self.graph.query_attribute("C Programming Language", "created_by")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]["target"], "Dennis Ritchie")

    def test_73_preseeded_transformer_supersedes(self):
        results = self.graph.query_attribute("Transformer Architecture", "supersedes")
        self.assertTrue(len(results) > 0)
        self.assertIn("Recurrent Neural Network", results[0]["target"])

    def test_74_add_custom_entity_and_relation(self):
        self.graph.add_entity("rust_creator", "Graydon Hoare", EntityType.PERSON, "computer_science")
        self.graph.add_entity("rust_lang_custom", "Rust Language", EntityType.TECHNOLOGY, "programming")
        self.graph.add_relation("rust_lang_custom", RelationType.CREATED_BY, "rust_creator")

        res = self.graph.query_attribute("Rust Language", "created_by")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["target"], "Graydon Hoare")

    def test_75_query_entity_by_alias(self):
        ent = self.graph.get_entity("u.s.")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.name, "United States")

    def test_76_query_entity_case_insensitive(self):
        ent = self.graph.get_entity("AUSTRALIA")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.name, "Australia")

    def test_77_inverse_relation_lookup(self):
        # Querying capital_of on Canberra
        rels = self.graph.get_relations_from("canberra", RelationType.CAPITAL_OF)
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0].target_id, "australia")

    def test_78_nonexistent_entity_query(self):
        res = self.graph.query_attribute("ImaginaryEntityXYZ", "creator")
        self.assertEqual(len(res), 0)

    def test_79_entity_properties_lookup(self):
        ent = self.graph.get_entity("canberra")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.properties.get("founded"), 1913)

    def test_80_penicillin_discoverer(self):
        res = self.graph.query_attribute("Penicillin", "invented_by")
        self.assertTrue(len(res) > 0)
        self.assertEqual(res[0]["target"], "Alexander Fleming")


class TestStoreRankingAndVersioning(unittest.TestCase):
    """Tests 81-95: Store Ranking Bug Fix, Subject Disambiguation, Node Versioning."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_knowledge.db")
        self.store = HybridKnowledgeStore(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_81_subject_token_extraction_capital_australia(self):
        tokens = extract_subject_tokens("What is the capital of Australia?")
        self.assertIn("australia", tokens)
        self.assertNotIn("capital", tokens)  # Capital must be excluded as target attribute

    def test_82_subject_token_extraction_who_developed_linux(self):
        tokens = extract_subject_tokens("Who developed the Linux kernel?")
        self.assertIn("linux", tokens)
        self.assertNotIn("developed", tokens)

    def test_83_subject_token_extraction_latest_python_version(self):
        tokens = extract_subject_tokens("What is the latest Python version?")
        self.assertIn("python", tokens)
        self.assertNotIn("version", tokens)
        self.assertNotIn("latest", tokens)

    def test_84_subject_token_fallback_when_only_attribute(self):
        tokens = extract_subject_tokens("capital")
        self.assertEqual(tokens, ["capital"])

    def test_85_store_migration_columns_exist(self):
        # Insert node with versioning columns
        node = KnowledgeNode(
            node_id="test-node-1",
            domain="stem",
            topic="physics",
            title="Quantum Superposition",
            content="A physical system exists partly in all theoretically possible states.",
            version=1,
            effective_from=1000.0,
        )
        self.assertTrue(self.store.upsert_node(node))
        fetched = self.store.get_node("test-node-1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.version, 1)
        self.assertEqual(fetched.effective_from, 1000.0)

    def test_86_node_supersede_workflow(self):
        old_node = KnowledgeNode(
            node_id="python-version-old",
            domain="programming",
            topic="python",
            title="Python Current Version",
            content="The current version of Python is 3.11.",
            version=1,
        )
        self.store.upsert_node(old_node)

        new_node = KnowledgeNode(
            node_id="python-version-new",
            domain="programming",
            topic="python",
            title="Python Current Version",
            content="The current version of Python is 3.13.",
        )
        success = self.store.supersede_node("python-version-old", new_node)
        self.assertTrue(success)

        # Verify old node was marked superseded
        updated_old = self.store.get_node("python-version-old")
        self.assertEqual(updated_old.superseded_by, "python-version-new")
        self.assertIsNotNone(updated_old.effective_until)

        # Verify new node has incremented version and supersedes reference
        updated_new = self.store.get_node("python-version-new")
        self.assertEqual(updated_new.version, 2)
        self.assertEqual(updated_new.supersedes, "python-version-old")

    def test_87_node_history_traversal(self):
        n1 = KnowledgeNode(node_id="history-v1", domain="ai", topic="model", title="Model", content="v1", version=1)
        self.store.upsert_node(n1)

        n2 = KnowledgeNode(node_id="history-v2", domain="ai", topic="model", title="Model", content="v2")
        self.store.supersede_node("history-v1", n2)

        n3 = KnowledgeNode(node_id="history-v3", domain="ai", topic="model", title="Model", content="v3")
        self.store.supersede_node("history-v2", n3)

        history = self.store.get_node_history("history-v2")
        self.assertEqual(len(history), 3)
        self.assertEqual([h.version for h in history], [1, 2, 3])

    def test_88_ranking_does_not_inflate_score_for_unrelated_doc(self):
        # Insert AWS S3 node with the word "capital" in content (e.g. capital expenses)
        s3_node = KnowledgeNode(
            node_id="aws-s3-test",
            domain="cloud",
            topic="storage",
            title="Amazon Simple Storage Service S3",
            content="Cloud object storage reduces capital expenditure and operating expenses.",
            tags=["aws", "storage", "cloud"],
        )
        self.store.upsert_node(s3_node)

        # Search for Australia capital
        hits = self.store.search_bm25(query="What is the capital of Australia?", limit=5)
        # Because Australia is the subject token, AWS S3 MUST NOT be returned!
        s3_hits = [h for h in hits if h[0].node_id == "aws-s3-test"]
        self.assertEqual(len(s3_hits), 0, "AWS S3 must not match Australia capital query")

    def test_89_ranking_boosts_exact_subject_in_title(self):
        node = KnowledgeNode(
            node_id="canberra-node",
            domain="geography",
            topic="australia",
            title="Canberra Australia Capital",
            content="Canberra is the capital city of Australia.",
            tags=["australia", "capital", "city"],
        )
        self.store.upsert_node(node)

        hits = self.store.search_bm25(query="What is the capital of Australia?", limit=5)
        self.assertTrue(len(hits) > 0)
        self.assertEqual(hits[0][0].node_id, "canberra-node")
        self.assertGreaterEqual(hits[0][1], 0.70)


class TestEndToEndUniversalKnowledgeFabric(unittest.TestCase):
    """Tests 90-105: End-to-End Universal Knowledge Verification & Adversarial Queries."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "e2e_knowledge.db")
        self.store = HybridKnowledgeStore(db_path=self.db_path)
        self.engine = ResearchEngine(store=self.store, allow_web=False)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_90_e2e_australia_capital_exact(self):
        report = self.engine.research("What is the capital of Australia?")
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("Canberra", report.primary_answer)
        self.assertNotIn("Amazon", report.primary_answer)
        self.assertNotIn("S3", report.primary_answer)

    def test_91_e2e_france_capital_exact(self):
        report = self.engine.research("What is the capital of France?")
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("Paris", report.primary_answer)

    def test_92_e2e_linux_kernel_developer_exact(self):
        report = self.engine.research("Who developed the Linux kernel?")
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("Linus Torvalds", report.primary_answer)
        self.assertNotIn("Graydon Hoare", report.primary_answer)

    def test_93_e2e_java_creator_exact(self):
        report = self.engine.research("Who created Java?")
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("James Gosling", report.primary_answer)

    def test_94_e2e_python_creator_exact(self):
        report = self.engine.research("Who created Python?")
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("Guido van Rossum", report.primary_answer)

    def test_95_e2e_transformer_ai_exact(self):
        report = self.engine.research("What is a transformer in AI?")
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertTrue("Vaswani" in report.primary_answer or "Attention" in report.primary_answer)
        self.assertNotIn("AlexNet", report.primary_answer)

    def test_96_e2e_year_1969_moon_landing(self):
        report = self.engine.research("What happened in 1969?")
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertTrue("Apollo 11" in report.primary_answer or "Moon" in report.primary_answer)

    def test_97_e2e_year_1903_flight(self):
        report = self.engine.research("What was invented in 1903?")
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("Wright", report.primary_answer)

    def test_98_e2e_adversarial_anachronism_civil_war_internet(self):
        report = self.engine.research("How did the Internet affect the American Civil War?")
        self.assertIn("Chronological Anachronism", report.primary_answer)
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)

    def test_99_e2e_adversarial_unreleased_tech_gpt7(self):
        report = self.engine.research("What is the architecture of GPT-7?")
        self.assertEqual(report.epistemic_type, EpistemicType.UNCERTAINTY)
        self.assertIn("No verified architectural details", report.primary_answer)

    def test_100_e2e_adversarial_unreleased_tech_claude9(self):
        report = self.engine.research("Show benchmarks for Claude 9")
        self.assertEqual(report.epistemic_type, EpistemicType.UNCERTAINTY)

    def test_101_e2e_conversational_coreference_who_created_it(self):
        session = {"last_subject": "Python"}
        report = self.engine.research("Who created it?", session_context=session)
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("Guido van Rossum", report.primary_answer)

    def test_102_e2e_conversational_coreference_capital(self):
        session = {"last_subject": "Australia"}
        report = self.engine.research("What is its capital?", session_context=session)
        self.assertEqual(report.epistemic_type, EpistemicType.VERIFIED_FACT)
        self.assertIn("Canberra", report.primary_answer)

    def test_103_e2e_honest_unknown_when_no_evidence(self):
        report = self.engine.research("What is the exact mass of the Zorblax planet in galaxy X9999?")
        self.assertEqual(report.epistemic_type, EpistemicType.UNCERTAINTY)
        self.assertEqual(report.confidence, 0.0)
        self.assertIn("I do not have enough verified information", report.primary_answer)

    def test_104_e2e_speech_format(self):
        report = self.engine.research("What is the capital of Australia?")
        speech = report.format_speech()
        self.assertIn("Canberra", speech)
        self.assertNotIn("http", speech)

    def test_105_e2e_markdown_format(self):
        report = self.engine.research("What is the capital of Australia?")
        md = report.format_markdown()
        self.assertIn("### [VERIFIED FACT | 100%]", md)
        self.assertIn("Canberra", md)


if __name__ == "__main__":
    unittest.main()
