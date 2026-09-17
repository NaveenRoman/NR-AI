"""
NR-AI Knowledge Timeline Engine (1880–2026 Chronological Continuum).

Indexes, cross-references, and queries historical, scientific, technological,
and cultural events across the 1880–2026 chronological continuum.
Enables precise temporal grounding, epoch filtering, and multi-hop evolution synthesis.
"""

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class TimelineEvent:
    """A chronologically anchored historical or technological event."""
    year: int
    title: str
    description: str
    domain: str
    date_str: Optional[str] = None  # e.g., "1969-07-20"
    entities: List[str] = field(default_factory=list)
    significance: str = "foundational"  # foundational, milestone, evolutionary, current
    sources: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "year": self.year,
            "title": self.title,
            "description": self.description,
            "domain": self.domain,
            "date_str": self.date_str,
            "entities": self.entities,
            "significance": self.significance,
            "sources": self.sources,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TimelineEvent":
        return cls(
            year=int(data["year"]),
            title=str(data["title"]),
            description=str(data["description"]),
            domain=str(data.get("domain", "history")),
            date_str=data.get("date_str"),
            entities=list(data.get("entities", [])),
            significance=str(data.get("significance", "milestone")),
            sources=list(data.get("sources", [])),
        )


class KnowledgeTimelineEngine:
    """
    Chronological index and query engine spanning 1880 to 2026.
    """

    def __init__(self):
        self._events_by_year: Dict[int, List[TimelineEvent]] = {}
        self._events_by_entity: Dict[str, List[TimelineEvent]] = {}
        self._events_by_domain: Dict[str, List[TimelineEvent]] = {}
        self._all_events: List[TimelineEvent] = []
        self._seed_chronology()

    def add_event(self, event: TimelineEvent) -> None:
        """Adds and indexes a timeline event."""
        self._all_events.append(event)
        self._events_by_year.setdefault(event.year, []).append(event)
        self._events_by_domain.setdefault(event.domain.lower(), []).append(event)
        for ent in event.entities:
            clean_ent = ent.strip().lower()
            self._events_by_entity.setdefault(clean_ent, []).append(event)

    def lookup_year(self, year: int) -> List[TimelineEvent]:
        """Returns all indexed events for a specific year."""
        return self._events_by_year.get(year, [])

    def lookup_range(self, start_year: int, end_year: int) -> List[TimelineEvent]:
        """Returns all indexed events between start_year and end_year inclusive."""
        results = [e for e in self._all_events if start_year <= e.year <= end_year]
        return sorted(results, key=lambda x: (x.year, x.date_str or ""))

    def lookup_entity_trajectory(self, entity_name: str) -> List[TimelineEvent]:
        """Returns chronological trajectory of an entity."""
        clean = entity_name.strip().lower()
        if clean in self._events_by_entity:
            return sorted(self._events_by_entity[clean], key=lambda x: x.year)
        # Substring search
        matched = []
        for ent_key, events in self._events_by_entity.items():
            if clean in ent_key or ent_key in clean:
                for ev in events:
                    if ev not in matched:
                        matched.append(ev)
        return sorted(matched, key=lambda x: x.year)

    def lookup_domain_events(self, domain: str) -> List[TimelineEvent]:
        """Returns all events in a given domain sorted chronologically."""
        dom = domain.strip().lower()
        return sorted(self._events_by_domain.get(dom, []), key=lambda x: x.year)

    def synthesize_evolution(self, topic_or_domain: str, start_year: int = 1880, end_year: int = 2026) -> str:
        """Generates a concise synthesized summary of evolution for a topic."""
        matches = self.lookup_entity_trajectory(topic_or_domain)
        if not matches:
            matches = [e for e in self.lookup_domain_events(topic_or_domain) if start_year <= e.year <= end_year]

        if not matches:
            return f"No chronological timeline events indexed for '{topic_or_domain}' between {start_year} and {end_year}."

        lines = [f"Evolution of {topic_or_domain.title()} ({matches[0].year}–{matches[-1].year}):"]
        for ev in matches:
            date_info = f" ({ev.date_str})" if ev.date_str else ""
            lines.append(f"- **{ev.year}**{date_info}: {ev.title} — {ev.description}")
        return "\n".join(lines)

    def _seed_chronology(self) -> None:
        """Pre-seeds key historical, scientific, and technological milestones (1880–2026)."""
        seeds = [
            # 1880 - 1900
            TimelineEvent(
                year=1887,
                title="Hertz Discovers Electromagnetic Waves & Tesla Patents AC Motor",
                description="Heinrich Hertz proves the existence of electromagnetic radio waves. Nikola Tesla files patents for polyphase AC electric motor and transmission system.",
                domain="physics",
                entities=["Tesla", "Nikola Tesla", "Hertz", "Electromagnetism", "AC Power"],
                sources=["Nobel Foundation", "IEEE History Center"]
            ),
            TimelineEvent(
                year=1895,
                title="Discovery of X-Rays & Wireless Telegraphy",
                description="Wilhelm Röntgen discovers X-rays; Guglielmo Marconi conducts successful pioneer experiments in radio wireless telegraphy.",
                domain="physics",
                entities=["Wilhelm Röntgen", "X-Rays", "Marconi", "Radio"],
                sources=["Royal Swedish Academy of Sciences"]
            ),
            # 1900 - 1920
            TimelineEvent(
                year=1903,
                title="Wright Brothers First Powered Flight",
                description="Orville and Wilbur Wright achieve the first controlled, sustained flight of a powered, heavier-than-air aircraft at Kitty Hawk, North Carolina.",
                date_str="1903-12-17",
                domain="aerospace",
                entities=["Wright Brothers", "Aviation", "Flight", "Kitty Hawk"],
                sources=["Smithsonian National Air and Space Museum"]
            ),
            TimelineEvent(
                year=1905,
                title="Einstein's Annus Mirabilis (Special Relativity & Photoelectric Effect)",
                description="Albert Einstein publishes four groundbreaking papers introducing Special Relativity, E=mc^2, Brownian motion, and the photoelectric effect (foundation of quantum theory).",
                domain="physics",
                entities=["Albert Einstein", "Special Relativity", "Photoelectric Effect", "Quantum Theory"],
                sources=["Annalen der Physik"]
            ),
            TimelineEvent(
                year=1915,
                title="Einstein Formulates General Relativity",
                description="Albert Einstein presents the field equations of General Relativity, describing gravitation as geometric curvature of spacetime.",
                domain="physics",
                entities=["Albert Einstein", "General Relativity", "Gravity", "Spacetime"],
                sources=["Prussian Academy of Sciences"]
            ),
            # 1920 - 1940
            TimelineEvent(
                year=1928,
                title="Alexander Fleming Discovers Penicillin",
                description="Alexander Fleming identifies penicillin from Penicillium notatum mold, ushering in the modern antibiotic era in medicine.",
                domain="medicine",
                entities=["Alexander Fleming", "Penicillin", "Antibiotics"],
                sources=["British Journal of Experimental Pathology"]
            ),
            TimelineEvent(
                year=1929,
                title="Hubble Discovers Expanding Universe",
                description="Edwin Hubble establishes that distant galaxies are moving away from Earth with velocities proportional to their distance (Hubble's Law).",
                domain="space",
                entities=["Edwin Hubble", "Expanding Universe", "Hubble Law", "Cosmology"],
                sources=["PNAS"]
            ),
            TimelineEvent(
                year=1936,
                title="Alan Turing Introduces Universal Turing Machine",
                description="Alan Turing publishes 'On Computable Numbers', formulating the concept of the universal computing machine and algorithmic computability.",
                domain="computer_science",
                entities=["Alan Turing", "Turing Machine", "Computation", "Algorithm"],
                sources=["Proceedings of the London Mathematical Society"]
            ),
            TimelineEvent(
                year=1938,
                title="Discovery of Nuclear Fission",
                description="Otto Hahn and Fritz Strassmann discover nuclear fission in uranium, theoretically explained by Lise Meitner and Otto Frisch.",
                domain="physics",
                entities=["Otto Hahn", "Lise Meitner", "Nuclear Fission"],
                sources=["Naturwissenschaften"]
            ),
            # 1940 - 1960
            TimelineEvent(
                year=1945,
                title="ENIAC Electronic Computer Completed",
                description="J. Presper Eckert and John Mauchly complete ENIAC, the first programmable, electronic, general-purpose digital computer.",
                domain="computer_science",
                entities=["ENIAC", "John Mauchly", "J. Presper Eckert", "Digital Computer"],
                sources=["University of Pennsylvania"]
            ),
            TimelineEvent(
                year=1947,
                title="Invention of the Transistor at Bell Labs",
                description="John Bardeen, Walter Brattain, and William Shockley invent the point-contact transistor, replacing bulky vacuum tubes.",
                date_str="1947-12-23",
                domain="semiconductor",
                entities=["Transistor", "Bell Labs", "William Shockley", "John Bardeen", "Walter Brattain", "Semiconductor"],
                sources=["Bell Laboratories"]
            ),
            TimelineEvent(
                year=1948,
                title="Shannon Founds Information Theory",
                description="Claude Shannon publishes 'A Mathematical Theory of Communication', establishing digital information theory and bit units.",
                domain="mathematics",
                entities=["Claude Shannon", "Information Theory", "Bits", "Entropy"],
                sources=["Bell System Technical Journal"]
            ),
            TimelineEvent(
                year=1953,
                title="Discovery of DNA Double Helix Structure",
                description="James Watson, Francis Crick, Rosalind Franklin, and Maurice Wilkins elucidate the double-helix chemical structure of DNA.",
                date_str="1953-04-25",
                domain="biology",
                entities=["DNA", "James Watson", "Francis Crick", "Rosalind Franklin", "Genetics"],
                sources=["Nature"]
            ),
            TimelineEvent(
                year=1956,
                title="Dartmouth Conference Coins 'Artificial Intelligence'",
                description="John McCarthy, Marvin Minsky, Nathaniel Rochester, and Claude Shannon organize the Dartmouth Summer Research Project on Artificial Intelligence, founding the field of AI.",
                domain="ai_ml",
                entities=["John McCarthy", "Marvin Minsky", "Artificial Intelligence", "AI", "Dartmouth"],
                sources=["Dartmouth College"]
            ),
            TimelineEvent(
                year=1957,
                title="Launch of Sputnik 1",
                description="The Soviet Union launches Sputnik 1, the first artificial Earth satellite, inaugurating the Space Age.",
                date_str="1957-10-04",
                domain="space",
                entities=["Sputnik", "Satellite", "Space Age"],
                sources=["Soviet Space Program"]
            ),
            TimelineEvent(
                year=1958,
                title="Invention of the Integrated Circuit (Microchip)",
                description="Jack Kilby at Texas Instruments and Robert Noyce at Fairchild Semiconductor independently invent the monolithic integrated circuit.",
                domain="semiconductor",
                entities=["Integrated Circuit", "Microchip", "Jack Kilby", "Robert Noyce", "Silicon"],
                sources=["IEEE Spectrum"]
            ),
            # 1960 - 1980
            TimelineEvent(
                year=1969,
                title="Apollo 11 Crew Lands on the Moon & ARPANET First Packet",
                description="Neil Armstrong and Buzz Aldrin become the first humans to walk on the Moon. ARPANET transmits the first message between UCLA and Stanford, establishing the foundation of the Internet.",
                date_str="1969-07-20",
                domain="space",
                entities=["Apollo 11", "Neil Armstrong", "ARPANET", "Internet", "Moon Landing"],
                sources=["NASA", "DARPA"]
            ),
            TimelineEvent(
                year=1971,
                title="Intel 4004 First Commercial Microprocessor",
                description="Intel releases the 4004 4-bit CPU designed by Federico Faggin, Ted Hoff, and Stanley Mazor, inaugurating the microprocessor revolution.",
                domain="hardware",
                entities=["Intel 4004", "Intel", "Microprocessor", "Federico Faggin", "CPU"],
                sources=["Intel Corporation"]
            ),
            TimelineEvent(
                year=1972,
                title="Dennis Ritchie Creates C Programming Language",
                description="Dennis Ritchie develops the C programming language at Bell Labs to implement the Unix operating system with Ken Thompson.",
                domain="programming",
                entities=["C", "C Programming Language", "Dennis Ritchie", "Bell Labs", "Unix"],
                sources=["Bell Laboratories"]
            ),
            TimelineEvent(
                year=1974,
                title="Cerf and Kahn Design TCP/IP Protocol Suite",
                description="Vinton Cerf and Robert Kahn publish 'A Protocol for Packet Network Intercommunication', establishing TCP/IP architecture.",
                domain="networking",
                entities=["TCP/IP", "Vint Cerf", "Bob Kahn", "Internet Protocol", "Networking"],
                sources=["IEEE Transactions on Communications"]
            ),
            TimelineEvent(
                year=1976,
                title="Apple Computer Founded (Apple I & II)",
                description="Steve Wozniak and Steve Jobs design and release the Apple I computer, followed by the breakthrough Apple II personal computer.",
                domain="computer_science",
                entities=["Apple", "Steve Jobs", "Steve Wozniak", "Personal Computer"],
                sources=["Apple Computer"]
            ),
            TimelineEvent(
                year=1977,
                title="Rivest, Shamir, Adleman Introduce RSA Cryptography",
                description="Ron Rivest, Adi Shamir, and Leonard Adleman invent the RSA public-key cryptosystem based on prime factorization difficulty.",
                domain="cybersecurity",
                entities=["RSA", "Cryptography", "Ron Rivest", "Public-Key Cryptography"],
                sources=["Communications of the ACM"]
            ),
            # 1980 - 2000
            TimelineEvent(
                year=1981,
                title="IBM Releases IBM Personal Computer (IBM PC 5150)",
                description="IBM introduces the IBM PC model 5150 running MS-DOS, establishing the x86 personal computing industrial architecture.",
                domain="hardware",
                entities=["IBM PC", "IBM", "x86", "MS-DOS", "PC"],
                sources=["IBM Archives"]
            ),
            TimelineEvent(
                year=1986,
                title="Backpropagation Learning Algorithm Popularized",
                description="David Rumelhart, Geoffrey Hinton, and Ronald Williams publish learning representations by back-propagating errors in multi-layer neural networks.",
                domain="ai_ml",
                entities=["Backpropagation", "Geoffrey Hinton", "Neural Networks", "Deep Learning", "AI"],
                sources=["Nature"]
            ),
            TimelineEvent(
                year=1989,
                title="Tim Berners-Lee Invents the World Wide Web",
                description="Tim Berners-Lee writes proposal for an information management system at CERN, implementing HTTP, HTML, URL, and the first web browser.",
                domain="networking",
                entities=["World Wide Web", "Tim Berners-Lee", "HTML", "HTTP", "CERN"],
                sources=["CERN"]
            ),
            TimelineEvent(
                year=1991,
                title="Linux Kernel & Python 0.9.0 Released",
                description="Linus Torvalds announces the open-source Linux kernel. Guido van Rossum releases Python 0.9.0 to alt.sources.",
                domain="programming",
                entities=["Linux", "Linus Torvalds", "Python", "Guido van Rossum", "Open Source"],
                sources=["comp.os.minix", "Python Software Foundation"]
            ),
            TimelineEvent(
                year=1995,
                title="Sun Microsystems Releases Java Programming Language",
                description="James Gosling and Sun Microsystems publicly release Java 1.0 with the 'Write Once, Run Anywhere' JVM architecture.",
                date_str="1995-05-23",
                domain="programming",
                entities=["Java", "James Gosling", "Sun Microsystems", "JVM"],
                sources=["Sun Microsystems"]
            ),
            TimelineEvent(
                year=1997,
                title="Deep Blue Defeats Garry Kasparov",
                description="IBM's Deep Blue supercomputer defeats world chess champion Garry Kasparov under standard tournament time controls.",
                domain="ai_ml",
                entities=["Deep Blue", "IBM", "Garry Kasparov", "Chess", "AI"],
                sources=["IBM"]
            ),
            TimelineEvent(
                year=1998,
                title="Larry Page and Sergey Brin Found Google",
                description="Larry Page and Sergey Brin incorporate Google based on the PageRank link-analysis search algorithm at Stanford.",
                domain="computer_science",
                entities=["Google", "Larry Page", "Sergey Brin", "PageRank", "Search"],
                sources=["Stanford University"]
            ),
            # 2000 - 2015
            TimelineEvent(
                year=2001,
                title="Launch of Wikipedia",
                description="Jimmy Wales and Larry Sanger launch Wikipedia as a free, collaboratively edited multilingual online encyclopedia.",
                domain="education",
                entities=["Wikipedia", "Jimmy Wales", "Encyclopedia"],
                sources=["Wikimedia Foundation"]
            ),
            TimelineEvent(
                year=2005,
                title="Linus Torvalds Creates Git Distributed Version Control",
                description="Linus Torvalds designs and releases Git for distributed version control of the Linux kernel codebase.",
                domain="programming",
                entities=["Git", "Linus Torvalds", "Version Control"],
                sources=["Linux Foundation"]
            ),
            TimelineEvent(
                year=2006,
                title="Amazon Web Services Launches S3 and EC2",
                description="Amazon Web Services launches Amazon S3 object storage and EC2 compute instances, inaugurating modern cloud infrastructure.",
                domain="cloud",
                entities=["AWS", "Amazon Web Services", "S3", "EC2", "Cloud"],
                sources=["Amazon Web Services"]
            ),
            TimelineEvent(
                year=2007,
                title="Apple Releases the iPhone",
                description="Apple introduces the iPhone, combining a multi-touch smartphone, mobile web communicator, and portable media player.",
                domain="hardware",
                entities=["iPhone", "Apple", "Smartphone", "Steve Jobs"],
                sources=["Apple"]
            ),
            TimelineEvent(
                year=2008,
                title="Satoshi Nakamoto Publishes Bitcoin Whitepaper",
                description="Satoshi Nakamoto publishes 'Bitcoin: A Peer-to-Peer Electronic Cash System', introducing blockchain decentralized consensus.",
                domain="cybersecurity",
                entities=["Bitcoin", "Satoshi Nakamoto", "Blockchain", "Cryptocurrency"],
                sources=["bitcoin.org"]
            ),
            TimelineEvent(
                year=2012,
                title="AlexNet Wins ImageNet & CRISPR Gene Editing Demonstrated",
                description="Alex Krizhevsky, Ilya Sutskever, and Geoffrey Hinton win ImageNet with AlexNet CNN, starting the modern deep learning era. Jennifer Doudna and Emmanuelle Charpentier demonstrate CRISPR-Cas9 genome editing.",
                domain="ai_ml",
                entities=["AlexNet", "Geoffrey Hinton", "Ilya Sutskever", "Deep Learning", "CRISPR", "Jennifer Doudna"],
                sources=["NeurIPS", "Science"]
            ),
            # 2015 - 2026
            TimelineEvent(
                year=2017,
                title="Vaswani et al. Introduce Transformer Architecture ('Attention Is All You Need')",
                description="Google Brain and Google Research publish 'Attention Is All You Need', replacing recurrence and convolution with multi-head self-attention.",
                domain="ai_ml",
                entities=["Transformer", "Attention Is All You Need", "Vaswani", "Deep Learning", "AI", "LLM"],
                sources=["NeurIPS 2017"]
            ),
            TimelineEvent(
                year=2020,
                title="DeepMind AlphaFold Solves 50-Year Protein Folding Challenge",
                description="DeepMind's AlphaFold 2 achieves atomic-accuracy 3D protein structure prediction directly from amino acid sequences.",
                domain="biology",
                entities=["AlphaFold", "DeepMind", "Protein Folding", "Structural Biology", "AI"],
                sources=["CASP14", "Nature"]
            ),
            TimelineEvent(
                year=2022,
                title="James Webb Space Telescope First Images & ChatGPT Debut",
                description="NASA's JWST releases deepest infrared views of the universe. OpenAI introduces ChatGPT, propelling conversational LLMs into mainstream global adoption.",
                domain="ai_ml",
                entities=["JWST", "ChatGPT", "OpenAI", "LLM", "Generative AI", "NASA"],
                sources=["NASA", "OpenAI"]
            ),
            TimelineEvent(
                year=2024,
                title="Agentic AI Workflows, Multimodal Reasoning & Quantum Milestones",
                description="Frontier models achieve native multimodal reasoning across text, code, audio, video, and autonomous multi-agent tool execution.",
                domain="ai_ml",
                entities=["Agentic AI", "Multimodal AI", "Autonomous Agents", "Quantum"],
                sources=["ACM", "IEEE"]
            ),
            TimelineEvent(
                year=2026,
                title="Autonomous Computing, Sub-2nm Semiconductor Nodes & Frontier AGI Systems",
                description="Commercialization of sub-2nm gate-all-around (GAA) semiconductor nodes (TSMC A16 / Intel 18A). Deep epistemic agentic AI systems achieve autonomous software engineering and scientific synthesis.",
                domain="future_tech",
                entities=["Sub-2nm", "Semiconductor", "AGI", "Autonomous Systems", "NR-AI"],
                sources=["IEEE", "Semiconductor Industry Association"]
            ),
        ]

        for s in seeds:
            self.add_event(s)
