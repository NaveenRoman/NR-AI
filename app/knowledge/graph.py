"""
NR-AI Entity-Relationship Knowledge Graph.

Represents typed entities, directed relations, attribute edges, and multi-hop
graph traversals for the Universal Knowledge Brain.
Provides deterministic entity resolution and relationship lookup.
"""

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class EntityType(str, Enum):
    PERSON = "person"
    TECHNOLOGY = "technology"
    ORGANIZATION = "organization"
    CONCEPT = "concept"
    LOCATION = "location"
    STANDARD = "standard"
    EVENT = "event"
    ARTIFACT = "artifact"


class RelationType(str, Enum):
    CREATED_BY = "created_by"
    INVENTED_BY = "invented_by"
    DEVELOPED_BY = "developed_by"
    FOUNDED_BY = "founded_by"
    RELEASED_ON = "released_on"
    SUPERSEDES = "supersedes"
    SUPERSEDED_BY = "superseded_by"
    DEPENDS_ON = "depends_on"
    BASED_ON = "based_on"
    PART_OF = "part_of"
    CAPITAL_OF = "capital_of"
    LOCATED_IN = "located_in"
    PRECEDES = "precedes"
    SUCCEEDS = "succeeds"
    AUTHORED = "authored"
    CONTRADICTS = "contradicts"


@dataclass
class GraphEntity:
    entity_id: str
    name: str
    entity_type: EntityType
    domain: str = "general"
    properties: Dict[str, Any] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "entity_type": self.entity_type.value,
            "domain": self.domain,
            "properties": self.properties,
            "aliases": self.aliases,
        }


@dataclass
class GraphRelation:
    source_id: str
    relation_type: RelationType
    target_id: str
    properties: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "relation_type": self.relation_type.value,
            "target_id": self.target_id,
            "properties": self.properties,
            "confidence": self.confidence,
        }


class KnowledgeGraph:
    """
    In-memory directed property graph supporting entity resolution,
    typed relationship traversal, and attribute queries.
    """

    def __init__(self):
        self._entities: Dict[str, GraphEntity] = {}
        self._name_to_id: Dict[str, str] = {}
        self._out_edges: Dict[str, List[GraphRelation]] = {}
        self._in_edges: Dict[str, List[GraphRelation]] = {}
        self._seed_canonical_graph()

    def add_entity(
        self,
        entity_id: str,
        name: str,
        entity_type: EntityType,
        domain: str = "general",
        properties: Optional[Dict[str, Any]] = None,
        aliases: Optional[List[str]] = None,
    ) -> GraphEntity:
        """Adds an entity to the knowledge graph."""
        norm_id = entity_id.strip().lower()
        entity = GraphEntity(
            entity_id=norm_id,
            name=name.strip(),
            entity_type=entity_type,
            domain=domain,
            properties=properties or {},
            aliases=aliases or [],
        )
        self._entities[norm_id] = entity
        self._name_to_id[name.strip().lower()] = norm_id
        if aliases:
            for a in aliases:
                self._name_to_id[a.strip().lower()] = norm_id
        return entity

    def add_relation(
        self,
        source_id: str,
        relation_type: RelationType,
        target_id: str,
        properties: Optional[Dict[str, Any]] = None,
        confidence: float = 1.0,
    ) -> GraphRelation:
        """Adds a directed relation between two entities."""
        s_id = source_id.strip().lower()
        t_id = target_id.strip().lower()
        rel = GraphRelation(
            source_id=s_id,
            relation_type=relation_type,
            target_id=t_id,
            properties=properties or {},
            confidence=confidence,
        )
        self._out_edges.setdefault(s_id, []).append(rel)
        self._in_edges.setdefault(t_id, []).append(rel)
        return rel

    def get_entity(self, name_or_id: str) -> Optional[GraphEntity]:
        """Resolves an entity by canonical ID, name, or alias."""
        clean = name_or_id.strip().lower()
        if clean in self._entities:
            return self._entities[clean]
        if clean in self._name_to_id:
            return self._entities[self._name_to_id[clean]]
        # Partial match
        for name, eid in self._name_to_id.items():
            if clean in name or name in clean:
                return self._entities[eid]
        return None

    def get_relations_from(self, entity_id: str, relation_type: Optional[RelationType] = None) -> List[GraphRelation]:
        """Returns outgoing relations from an entity."""
        clean_id = entity_id.strip().lower()
        rels = self._out_edges.get(clean_id, [])
        if relation_type:
            return [r for r in rels if r.relation_type == relation_type]
        return list(rels)

    def get_relations_to(self, entity_id: str, relation_type: Optional[RelationType] = None) -> List[GraphRelation]:
        """Returns incoming relations to an entity."""
        clean_id = entity_id.strip().lower()
        rels = self._in_edges.get(clean_id, [])
        if relation_type:
            return [r for r in rels if r.relation_type == relation_type]
        return list(rels)

    def query_attribute(self, entity_name: str, attribute_or_relation: str) -> List[Dict[str, Any]]:
        """Queries attribute or related entities for a given subject with synonym expansion."""
        ent = self.get_entity(entity_name)
        if not ent:
            return []

        attr_clean = attribute_or_relation.strip().lower().replace(" ", "_")
        results: List[Dict[str, Any]] = []

        RELATION_SYNONYMS: Dict[str, Set[str]] = {
            "creator": {"created_by", "developed_by", "invented_by", "authored", "founded_by", "creator", "developer", "inventor", "author"},
            "developer": {"created_by", "developed_by", "invented_by", "authored", "creator", "developer"},
            "inventor": {"invented_by", "created_by", "developed_by", "inventor"},
            "author": {"authored", "created_by", "author"},
            "capital": {"capital_of", "capital"},
            "definition": {"definition", "description", "summary", "paper", "architecture"},
            "architecture": {"architecture", "based_on", "definition"},
        }
        synonyms = RELATION_SYNONYMS.get(attr_clean, {attr_clean})

        # 1. Check direct properties
        for k, v in ent.properties.items():
            k_clean = k.lower().replace(" ", "_")
            if k_clean == attr_clean or k_clean in synonyms or attr_clean in k_clean:
                results.append({
                    "source": ent.name,
                    "relation": attr_clean,
                    "value": v,
                    "type": "property",
                })

        # 2. Check outgoing relations
        for rel in self.get_relations_from(ent.entity_id):
            rel_val = rel.relation_type.value.lower()
            if rel_val in synonyms or attr_clean in rel_val or any(s in rel_val for s in synonyms):
                target_ent = self._entities.get(rel.target_id)
                target_name = target_ent.name if target_ent else rel.target_id
                results.append({
                    "source": ent.name,
                    "relation": rel.relation_type.value,
                    "target": target_name,
                    "target_entity": target_ent.to_dict() if target_ent else None,
                    "type": "relation",
                })

        # 3. Check incoming relations (e.g. capital_of Australia -> Canberra)
        for rel in self.get_relations_to(ent.entity_id):
            rel_val = rel.relation_type.value.lower()
            if rel_val in synonyms or attr_clean in rel_val or any(s in rel_val for s in synonyms):
                source_ent = self._entities.get(rel.source_id)
                source_name = source_ent.name if source_ent else rel.source_id
                results.append({
                    "source": source_name,
                    "relation": rel.relation_type.value,
                    "target": ent.name,
                    "source_entity": source_ent.to_dict() if source_ent else None,
                    "type": "inverse_relation",
                })

        return results

    def _seed_canonical_graph(self) -> None:
        """Pre-seeds canonical, undisputed factual triples across core domains."""
        # Geography
        self.add_entity("australia", "Australia", EntityType.LOCATION, "geography", {"continent": "Oceania", "largest_city": "Sydney"})
        self.add_entity("canberra", "Canberra", EntityType.LOCATION, "geography", {"founded": 1913})
        self.add_relation("canberra", RelationType.CAPITAL_OF, "australia")

        self.add_entity("france", "France", EntityType.LOCATION, "geography", {"continent": "Europe"})
        self.add_entity("paris", "Paris", EntityType.LOCATION, "geography")
        self.add_relation("paris", RelationType.CAPITAL_OF, "france")

        self.add_entity("japan", "Japan", EntityType.LOCATION, "geography", {"continent": "Asia"})
        self.add_entity("tokyo", "Tokyo", EntityType.LOCATION, "geography")
        self.add_relation("tokyo", RelationType.CAPITAL_OF, "japan")

        self.add_entity("usa", "United States", EntityType.LOCATION, "geography", aliases=["united states of america", "us", "u.s."])
        self.add_entity("washington_dc", "Washington, D.C.", EntityType.LOCATION, "geography")
        self.add_relation("washington_dc", RelationType.CAPITAL_OF, "usa")

        # Computer Science & Programming Languages
        self.add_entity("linus_torvalds", "Linus Torvalds", EntityType.PERSON, "computer_science")
        self.add_entity("linux_kernel", "Linux Kernel", EntityType.TECHNOLOGY, "operating_systems", {"initial_release": 1991, "creator": "Linus Torvalds", "developer": "Linus Torvalds"}, aliases=["linux", "linux kernel"])
        self.add_entity("git", "Git", EntityType.TECHNOLOGY, "programming", {"initial_release": 2005})
        self.add_relation("linux_kernel", RelationType.CREATED_BY, "linus_torvalds")
        self.add_relation("linux_kernel", RelationType.DEVELOPED_BY, "linus_torvalds")
        self.add_relation("git", RelationType.CREATED_BY, "linus_torvalds")

        self.add_entity("dennis_ritchie", "Dennis Ritchie", EntityType.PERSON, "computer_science")
        self.add_entity("c_lang", "C Programming Language", EntityType.TECHNOLOGY, "programming", {"initial_release": 1972}, aliases=["c"])
        self.add_relation("c_lang", RelationType.CREATED_BY, "dennis_ritchie")

        self.add_entity("james_gosling", "James Gosling", EntityType.PERSON, "computer_science")
        self.add_entity("java_lang", "Java", EntityType.TECHNOLOGY, "programming", {"initial_release": 1995, "creator": "James Gosling"}, aliases=["java programming language"])
        self.add_relation("java_lang", RelationType.CREATED_BY, "james_gosling")

        self.add_entity("guido_van_rossum", "Guido van Rossum", EntityType.PERSON, "computer_science")
        self.add_entity("python_lang", "Python", EntityType.TECHNOLOGY, "programming", {"initial_release": 1991, "creator": "Guido van Rossum"}, aliases=["python programming language"])
        self.add_relation("python_lang", RelationType.CREATED_BY, "guido_van_rossum")

        self.add_entity("graydon_hoare", "Graydon Hoare", EntityType.PERSON, "computer_science")
        self.add_entity("rust_lang", "Rust", EntityType.TECHNOLOGY, "programming", {"initial_release": 2010}, aliases=["rust programming language"])
        self.add_relation("rust_lang", RelationType.CREATED_BY, "graydon_hoare")

        self.add_entity("tim_berners_lee", "Tim Berners-Lee", EntityType.PERSON, "computer_science")
        self.add_entity("www", "World Wide Web", EntityType.TECHNOLOGY, "networking", {"invented": 1989})
        self.add_relation("www", RelationType.INVENTED_BY, "tim_berners_lee")

        self.add_entity("alan_turing", "Alan Turing", EntityType.PERSON, "computer_science")
        self.add_entity("turing_machine", "Universal Turing Machine", EntityType.CONCEPT, "computer_science", {"year": 1936})
        self.add_relation("turing_machine", RelationType.CREATED_BY, "alan_turing")

        # AI / ML
        self.add_entity("vaswani_et_al", "Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan Gomez, Łukasz Kaiser, Illia Polosukhin", EntityType.PERSON, "ai_ml", aliases=["vaswani", "vaswani et al."])
        self.add_entity(
            "transformer_arch",
            "Transformer Architecture",
            EntityType.TECHNOLOGY,
            "ai_ml",
            {
                "year": 2017,
                "paper": "Attention Is All You Need",
                "authors": "Ashish Vaswani et al.",
                "definition": "The Transformer is a deep learning architecture introduced in 2017 by Ashish Vaswani and colleagues in the landmark paper 'Attention Is All You Need', based on self-attention mechanisms without recurrent neural networks.",
                "architecture": "Multi-Head Self-Attention",
            },
            aliases=["transformer", "transformer in ai", "transformer architecture"]
        )
        self.add_entity("self_attention", "Multi-Head Self-Attention", EntityType.CONCEPT, "ai_ml")
        self.add_entity("rnn", "Recurrent Neural Network (RNN / LSTM)", EntityType.TECHNOLOGY, "ai_ml")
        self.add_relation("transformer_arch", RelationType.AUTHORED, "vaswani_et_al")
        self.add_relation("transformer_arch", RelationType.BASED_ON, "self_attention")
        self.add_relation("transformer_arch", RelationType.SUPERSEDES, "rnn")

        # Science & Physics
        self.add_entity("albert_einstein", "Albert Einstein", EntityType.PERSON, "physics")
        self.add_entity("general_relativity", "General Relativity", EntityType.CONCEPT, "physics", {"year": 1915})
        self.add_entity("special_relativity", "Special Relativity", EntityType.CONCEPT, "physics", {"year": 1905})
        self.add_relation("general_relativity", RelationType.CREATED_BY, "albert_einstein")
        self.add_relation("special_relativity", RelationType.CREATED_BY, "albert_einstein")

        self.add_entity("alexander_fleming", "Alexander Fleming", EntityType.PERSON, "medicine")
        self.add_entity("penicillin", "Penicillin", EntityType.TECHNOLOGY, "medicine", {"discovered": 1928})
        self.add_relation("penicillin", RelationType.INVENTED_BY, "alexander_fleming")
