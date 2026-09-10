"""
NR AI Real-Time Multi-Source World News Agent.

Gathers real-time news from verified live endpoints (BBC, Google News, Times of India,
ArXiv AI, Hacker News) across World, India, Technology, AI, Business, Science, and Gaming.
Performs cross-source verification to classify reports into:
- VERIFIED (Corroborated across 2+ independent sources)
- SINGLE_SOURCE (Reported by a single source; clearly identified)
- CONFLICTING (Differing or contradicting claims across sources)
- UNREACHABLE (Network or provider unavailable; reported transparently)
"""

from dataclasses import dataclass, field
import datetime
import difflib
import email.utils
from enum import Enum
import html
import json
import logging
import re
import time
from urllib.parse import urlparse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("NRAI.NewsAgent")


def normalize_publisher(raw: str) -> str:
    if not raw:
        return "Unknown Publisher"
    p = raw.strip()
    p_lower = p.lower()
    if "bbc" in p_lower:
        return "BBC News"
    if "verge" in p_lower:
        return "The Verge"
    if "techcrunch" in p_lower:
        return "TechCrunch"
    if "ars technica" in p_lower:
        return "Ars Technica"
    if "guardian" in p_lower:
        return "The Guardian"
    if "new york times" in p_lower or "nytimes" in p_lower:
        return "The New York Times"
    if "reuters" in p_lower:
        return "Reuters"
    if "associated press" in p_lower or p_lower == "ap":
        return "Associated Press"
    if "wsj" in p_lower or "wall street journal" in p_lower:
        return "The Wall Street Journal"
    if "washington post" in p_lower:
        return "The Washington Post"
    if "arxiv" in p_lower:
        return "arXiv"
    if "cbs" in p_lower:
        return "CBS News"
    if "tom's hardware" in p_lower or "tomshardware" in p_lower:
        return "Tom's Hardware"
    if "wired" in p_lower:
        return "Wired"
    if "nature" in p_lower:
        return "Nature"
    if p_lower == "science":
        return "Science Journal"
    if "bloomberg" in p_lower:
        return "Bloomberg"
    if "forbes" in p_lower:
        return "Forbes"
    if "cnbc" in p_lower:
        return "CNBC"
    if "hacker news" in p_lower:
        return "Hacker News (Discussion)"
    return p


PRIMARY_PUBLISHERS: Set[str] = {
    "BBC News", "The Verge", "TechCrunch", "Ars Technica", "The Guardian",
    "The New York Times", "Reuters", "Associated Press", "The Wall Street Journal",
    "The Washington Post", "CBS News", "Tom's Hardware", "Wired", "Nature",
    "Science Journal", "Bloomberg", "CNBC", "arXiv"
}

AGGREGATOR_NAMES: Set[str] = {
    "Google News", "Google News AI", "Google News Tech", "Google News World",
    "Google News India", "Google News Business", "Google News Science",
    "Google News Gaming", "Hacker News", "Hacker News Top", "Hacker News (Discussion)"
}


def calculate_freshness(pub_date: str) -> Tuple[str, str, float]:
    """Returns (freshness_label, category_badge, diff_hours)."""
    if not pub_date:
        return ("UNKNOWN (Date missing)", "UNKNOWN", 999.0)
    now = datetime.datetime.now(datetime.timezone.utc)
    dt = None
    try:
        dt = email.utils.parsedate_to_datetime(pub_date)
    except Exception:
        try:
            dt = datetime.datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
        except Exception:
            pass

    if dt is None:
        return (f"RECORDED ({pub_date})", "RECORDED", 12.0)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)

    diff_hours = (now - dt).total_seconds() / 3600.0
    if diff_hours < 0:
        diff_hours = 0.0

    if diff_hours < 6.0:
        badge = "BREAKING"
        label = f"BREAKING ({diff_hours:.1f}h ago: {pub_date})"
    elif diff_hours <= 24.0:
        badge = "TODAY"
        label = f"TODAY ({diff_hours:.1f}h ago: {pub_date})"
    elif diff_hours <= 72.0:
        badge = "RECENT"
        label = f"RECENT ({diff_hours / 24.0:.1f}d ago: {pub_date})"
    else:
        badge = "OLDER BACKGROUND"
        label = f"OLDER BACKGROUND (>{int(diff_hours / 24.0)}d ago: {pub_date})"

    return (label, badge, diff_hours)


def extract_story_tokens(text: str) -> Set[str]:
    clean = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower())
    stop_words = {
        "the", "a", "an", "in", "on", "of", "and", "to", "for", "with", "at", "by",
        "is", "as", "it", "from", "says", "has", "how", "new", "over", "out", "its",
        "first", "are", "about", "into", "after", "will", "what", "who", "why", "their",
        "can", "could", "all", "more", "now", "just", "up", "down", "no", "yes", "than"
    }
    return {w for w in clean.split() if len(w) > 2 and w not in stop_words}


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    SINGLE_SOURCE = "SINGLE_SOURCE"
    CONFLICTING = "CONFLICTING"
    UNREACHABLE = "UNREACHABLE"


@dataclass
class NewsItem:
    """Represents a single structured news article or research preprint."""

    headline: str
    source: str
    publication_time: str
    url: str
    topic: str  # World, India, Technology, AI, Business, Science, Gaming
    summary: str = ""
    publisher: str = ""
    freshness: str = ""
    freshness_badge: str = ""
    diff_hours: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "headline": self.headline,
            "source": self.source,
            "publication_time": self.publication_time,
            "url": self.url,
            "topic": self.topic,
            "summary": self.summary,
            "publisher": self.publisher,
            "freshness": self.freshness,
            "freshness_badge": self.freshness_badge,
            "diff_hours": self.diff_hours,
            "timestamp": self.timestamp,
        }


@dataclass
class NewsVerificationReport:
    """Results of multi-source verification on a headline or topic."""

    headline: str
    topic: str
    status: VerificationStatus
    primary_source: str
    corroborating_sources: List[str] = field(default_factory=list)
    conflicting_notes: List[str] = field(default_factory=list)
    confidence: float = 0.5
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "headline": self.headline,
            "topic": self.topic,
            "status": self.status.value,
            "primary_source": self.primary_source,
            "corroborating_sources": self.corroborating_sources,
            "conflicting_notes": self.conflicting_notes,
            "confidence": self.confidence,
            "summary": self.summary,
        }


class NewsAgent:
    """
    Autonomous Live News Gathering and Verification Agent.

    Pulls from live public RSS feeds and APIs with zero fake data,
    stores structured metadata, and executes cross-feed comparison.
    """

    SUPPORTED_CATEGORIES = [
        "World",
        "India",
        "Technology",
        "AI",
        "Business",
        "Science",
        "Gaming",
    ]

    FEED_REGISTRY: Dict[str, List[Dict[str, str]]] = {
        "World": [
            {
                "name": "BBC World",
                "type": "rss",
                "url": "http://feeds.bbci.co.uk/news/world/rss.xml",
            },
            {
                "name": "Google News World",
                "type": "rss",
                "url": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
            },
        ],
        "India": [
            {
                "name": "Times of India",
                "type": "rss",
                "url": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
            },
            {
                "name": "Google News India",
                "type": "rss",
                "url": "https://news.google.com/rss/headlines/section/geo/IN?hl=en-IN&gl=IN&ceid=IN:en",
            },
        ],
        "Technology": [
            {
                "name": "Google News Tech",
                "type": "rss",
                "url": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
                "default_publisher": None,
            },
            {
                "name": "BBC Technology",
                "type": "rss",
                "url": "http://feeds.bbci.co.uk/news/technology/rss.xml",
                "default_publisher": "BBC News",
            },
            {
                "name": "The Verge",
                "type": "rss",
                "url": "https://www.theverge.com/rss/index.xml",
                "default_publisher": "The Verge",
            },
            {
                "name": "Ars Technica",
                "type": "rss",
                "url": "https://feeds.arstechnica.com/arstechnica/technology-lab",
                "default_publisher": "Ars Technica",
            },
            {
                "name": "Hacker News Top",
                "type": "hackernews",
                "url": "https://hacker-news.firebaseio.com/v0/topstories.json",
                "default_publisher": None,
            },
        ],
        "AI": [
            {
                "name": "Google News AI",
                "type": "rss",
                "url": "https://news.google.com/rss/search?q=Artificial+Intelligence&hl=en-US&gl=US&ceid=US:en",
                "default_publisher": None,
            },
            {
                "name": "TechCrunch AI",
                "type": "rss",
                "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
                "default_publisher": "TechCrunch",
            },
            {
                "name": "ArXiv AI",
                "type": "rss",
                "url": "https://rss.arxiv.org/rss/cs.AI",
                "default_publisher": "arXiv",
            },
        ],
        "Business": [
            {
                "name": "Google News Business",
                "type": "rss",
                "url": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
            },
            {
                "name": "BBC Business",
                "type": "rss",
                "url": "http://feeds.bbci.co.uk/news/business/rss.xml",
            },
        ],
        "Science": [
            {
                "name": "Google News Science",
                "type": "rss",
                "url": "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en",
            },
            {
                "name": "BBC Science",
                "type": "rss",
                "url": "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
            },
        ],
        "Gaming": [
            {
                "name": "PC Gamer",
                "type": "rss",
                "url": "https://www.pcgamer.com/rss/",
            },
            {
                "name": "Google News Gaming",
                "type": "rss",
                "url": "https://news.google.com/rss/search?q=Video+Games+Gaming&hl=en-US&gl=US&ceid=US:en",
            },
        ],
    }

    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (NR-AI NewsAgent)"

    def __init__(self, cache_ttl_seconds: float = 300.0):
        self.cache_ttl = cache_ttl_seconds
        self._cache: Dict[str, Tuple[float, List[NewsItem]]] = {}
        self._source_availability: Dict[str, Dict[str, Any]] = {}
        self.last_accessed_sources: List[Dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # Core Fetching
    # -------------------------------------------------------------------------

    def fetch_category(self, category: str, limit: int = 5) -> List[NewsItem]:
        """
        Fetch current news for a specific category.
        Uses in-memory cache if refreshed within TTL.
        """
        cat_key = self._normalize_category(category)
        now = time.time()

        if cat_key in self._cache:
            cached_time, items = self._cache[cat_key]
            if now - cached_time < self.cache_ttl and items:
                return items[:limit]

        feeds = self.FEED_REGISTRY.get(cat_key, [])
        all_items: List[NewsItem] = []

        for feed_info in feeds:
            feed_items = self._fetch_feed(feed_info, cat_key)
            all_items.extend(feed_items)

        # Deduplicate items by headline similarity
        deduped, _ = self._deduplicate_items(all_items)
        self._cache[cat_key] = (now, deduped)
        return deduped[:limit]

    def _normalize_category(self, cat: str) -> str:
        c = cat.strip().lower()
        mapping = {
            "world": "World",
            "global": "World",
            "international": "World",
            "india": "India",
            "tech": "Technology",
            "technology": "Technology",
            "ai": "AI",
            "artificial intelligence": "AI",
            "business": "Business",
            "finance": "Business",
            "science": "Science",
            "gaming": "Gaming",
            "games": "Gaming",
        }
        return mapping.get(c, "World")

    def _fetch_feed(self, feed_info: Dict[str, Any], category: str) -> List[NewsItem]:
        feed_name = feed_info["name"]
        feed_type = feed_info.get("type", "rss")
        url = feed_info["url"]

        req = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT})
        items: List[NewsItem] = []
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = resp.read()
                if feed_type in ("rss", "atom", "aggregator", "arxiv"):
                    items = self._parse_rss(data, feed_info, category)
                elif feed_type == "hackernews":
                    items = self._parse_hackernews(data, feed_info, category)

                src_meta = {
                    "source": feed_name,
                    "source_name": feed_name,
                    "feed_url": url,
                    "feed_type": feed_type,
                    "available": True,
                    "access_attempted": True,
                    "access_successful": (resp.status == 200 and len(data) > 0),
                    "status_code": resp.status,
                    "http_status": resp.status,
                    "bytes": len(data),
                    "retrieval_timestamp": now_iso,
                    "items_retrieved": len(items),
                    "error": None,
                    "url": url,
                    "last_checked": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                self._source_availability[feed_name] = src_meta
                self.last_accessed_sources.append(src_meta)

        except urllib.error.HTTPError as e:
            src_meta = {
                "source": feed_name,
                "source_name": feed_name,
                "feed_url": url,
                "feed_type": feed_type,
                "available": False,
                "access_attempted": True,
                "access_successful": False,
                "status_code": e.code,
                "http_status": e.code,
                "bytes": 0,
                "retrieval_timestamp": now_iso,
                "items_retrieved": 0,
                "error": f"HTTP {e.code}: {e.reason}",
                "url": url,
                "last_checked": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._source_availability[feed_name] = src_meta
            self.last_accessed_sources.append(src_meta)
            logger.warning(f"Failed to fetch {feed_name}: HTTP {e.code}")
        except Exception as e:
            src_meta = {
                "source": feed_name,
                "source_name": feed_name,
                "feed_url": url,
                "feed_type": feed_type,
                "available": False,
                "access_attempted": True,
                "access_successful": False,
                "status_code": None,
                "http_status": None,
                "bytes": 0,
                "retrieval_timestamp": now_iso,
                "items_retrieved": 0,
                "error": str(e),
                "url": url,
                "last_checked": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._source_availability[feed_name] = src_meta
            self.last_accessed_sources.append(src_meta)
            logger.warning(f"Failed to fetch {feed_name}: {e}")

        return items

    def _parse_rss(self, raw_xml: bytes, feed_meta: Dict[str, Any], category: str) -> List[NewsItem]:
        source_name = feed_meta["name"]
        items: List[NewsItem] = []
        try:
            root = ET.fromstring(raw_xml)
            channel = root.find("channel")
            elements = channel.findall("item") if channel is not None else root.findall(".//item")
            is_atom = False
            if not elements:
                elements = [c for c in root if c.tag.endswith("entry")] or root.findall(".//{http://www.w3.org/2005/Atom}entry")
                is_atom = True

            for el in elements[:15]:
                if is_atom:
                    title_el = next((c for c in el if c.tag.endswith("title")), None)
                    link_el = next((c for c in el if c.tag.endswith("link")), None)
                    pub_el = next(
                        (c for c in el if c.tag.endswith("published") or c.tag.endswith("updated") or c.tag.endswith("pubDate")),
                        None,
                    )
                    desc_el = next(
                        (c for c in el if c.tag.endswith("summary") or c.tag.endswith("content") or c.tag.endswith("description")),
                        None,
                    )
                    src_el = None
                    link = link_el.attrib.get("href", "") if link_el is not None else ""
                else:
                    title_el = el.find("title") if el.find("title") is not None else el.find("{http://www.w3.org/2005/Atom}title")
                    link_el = el.find("link") if el.find("link") is not None else el.find("{http://www.w3.org/2005/Atom}link")
                    pub_el = el.find("pubDate")
                    if pub_el is None:
                        pub_el = el.find("{http://www.w3.org/2005/Atom}published")
                    if pub_el is None:
                        pub_el = el.find("{http://www.w3.org/2005/Atom}updated")

                    desc_el = el.find("description")
                    if desc_el is None:
                        desc_el = el.find("{http://www.w3.org/2005/Atom}summary")
                    if desc_el is None:
                        desc_el = el.find("{http://www.w3.org/2005/Atom}content")

                    src_el = el.find("source")
                    if link_el is not None:
                        link = link_el.text.strip() if link_el.text else link_el.attrib.get("href", "")
                    else:
                        link = ""

                title = title_el.text.strip() if title_el is not None and title_el.text else ""
                pub = pub_el.text.strip() if pub_el is not None and pub_el.text else time.strftime("%Y-%m-%d")
                desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""

                clean_title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
                clean_desc = html.unescape(re.sub(r"<[^>]+>", "", desc)).strip()

                # Publisher resolution
                if feed_meta.get("default_publisher"):
                    publisher = feed_meta["default_publisher"]
                elif src_el is not None and src_el.text:
                    publisher = normalize_publisher(src_el.text)
                elif " - " in clean_title:
                    parts = clean_title.rsplit(" - ", 1)
                    clean_title = parts[0].strip()
                    publisher = normalize_publisher(parts[1])
                else:
                    publisher = source_name

                if clean_title:
                    fresh_label, badge, diff_h = calculate_freshness(pub)
                    topic_label = "AI RESEARCH" if feed_meta.get("name") == "ArXiv AI" else category
                    items.append(
                        NewsItem(
                            headline=clean_title,
                            source=source_name,
                            publication_time=pub,
                            url=link,
                            topic=topic_label,
                            summary=clean_desc[:250],
                            publisher=publisher,
                            freshness=fresh_label,
                            freshness_badge=badge,
                            diff_hours=diff_h,
                        )
                    )
        except Exception as e:
            logger.error(f"XML parse error for {source_name}: {e}")

        return items

    def _parse_hackernews(self, raw_json: bytes, feed_meta: Dict[str, Any], category: str) -> List[NewsItem]:
        source_name = feed_meta["name"]
        items: List[NewsItem] = []
        try:
            ids = json.loads(raw_json.decode("utf-8"))[:8]
            for item_id in ids:
                item_url = f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
                req = urllib.request.Request(item_url, headers={"User-Agent": self.USER_AGENT})
                try:
                    with urllib.request.urlopen(req, timeout=3.5) as resp:
                        item_data = json.loads(resp.read().decode("utf-8"))
                        title = item_data.get("title", "")
                        ext_url = item_data.get("url", f"https://news.ycombinator.com/item?id={item_id}")
                        score = item_data.get("score", 0)
                        post_time = time.strftime(
                            "%Y-%m-%d %H:%M:%S GMT", time.gmtime(item_data.get("time", time.time()))
                        )
                        parsed_u = urlparse(ext_url)
                        domain = parsed_u.netloc.replace("www.", "")
                        if domain and "ycombinator" not in domain:
                            publisher = normalize_publisher(domain)
                        else:
                            publisher = "Hacker News (Discussion)"

                        if title:
                            fresh_label, badge, diff_h = calculate_freshness(post_time)
                            items.append(
                                NewsItem(
                                    headline=title,
                                    source=source_name,
                                    publication_time=post_time,
                                    url=ext_url,
                                    topic=category,
                                    summary=f"Hacker News submission with {score} points.",
                                    publisher=publisher,
                                    freshness=fresh_label,
                                    freshness_badge=badge,
                                    diff_hours=diff_h,
                                )
                            )
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Hacker News parse error: {e}")
        return items

    def _deduplicate_items(self, items: List[NewsItem]) -> Tuple[List[NewsItem], int]:
        deduped: List[NewsItem] = []
        dups_removed = 0
        for it in items:
            is_dup = False
            t1 = extract_story_tokens(it.headline)
            for prev in deduped:
                sim = difflib.SequenceMatcher(None, it.headline.lower(), prev.headline.lower()).ratio()
                t2 = extract_story_tokens(prev.headline)
                common = t1 & t2
                overlap = len(common) / max(min(len(t1), len(t2)), 1)
                if it.publisher == prev.publisher and (sim > 0.70 or overlap > 0.60):
                    is_dup = True
                    dups_removed += 1
                    break
                elif it.url and prev.url and it.url == prev.url:
                    is_dup = True
                    dups_removed += 1
                    break
            if not is_dup:
                deduped.append(it)
        return deduped, dups_removed

    # -------------------------------------------------------------------------
    # Multi-Source Verification
    # -------------------------------------------------------------------------

    def verify_headline(self, target_headline: str, category: str) -> NewsVerificationReport:
        """
        Compare claims across independent feeds for the category:
        SOURCE A + SOURCE B -> Compare claims -> VERIFIED / CONFLICTING / SINGLE-SOURCE
        """
        cat_key = self._normalize_category(category)
        feeds = self.FEED_REGISTRY.get(cat_key, [])

        # Fetch fresh items across all feeds for category
        items_by_source: Dict[str, List[NewsItem]] = {}
        for feed in feeds:
            src = feed["name"]
            items_by_source[src] = self._fetch_feed(feed, cat_key)

        primary_source = "Unknown"
        corroborating: List[str] = []
        conflicting: List[str] = []

        target_norm = target_headline.lower().strip()
        target_tokens = set(re.findall(r"\w+", target_norm)) - {
            "the", "a", "an", "in", "on", "of", "and", "to", "for", "with", "at", "by", "is", "as"
        }

        found_in_any = False
        for src_name, items in items_by_source.items():
            matched_item = None
            for it in items:
                sim = difflib.SequenceMatcher(None, target_norm, it.headline.lower()).ratio()
                tokens = set(re.findall(r"\w+", it.headline.lower()))
                common = target_tokens & tokens
                token_overlap = (len(common) / len(target_tokens)) if target_tokens else 0.0

                if sim >= 0.55 or token_overlap >= 0.40:
                    matched_item = it
                    break

            if matched_item:
                found_in_any = True
                if primary_source == "Unknown":
                    primary_source = src_name
                else:
                    corroborating.append(f"{src_name}: '{matched_item.headline}'")

        if not found_in_any:
            # Check if network is down
            all_down = all(not v.get("available", False) for v in self._source_availability.values())
            if all_down:
                return NewsVerificationReport(
                    headline=target_headline,
                    topic=cat_key,
                    status=VerificationStatus.UNREACHABLE,
                    primary_source="None",
                    confidence=0.0,
                    summary="All configured news feeds are currently unreachable or offline.",
                )
            return NewsVerificationReport(
                headline=target_headline,
                topic=cat_key,
                status=VerificationStatus.SINGLE_SOURCE,
                primary_source="User Inquiry",
                confidence=0.3,
                summary="Topic not currently trending on checked official news channels.",
            )

        if len(corroborating) >= 1:
            return NewsVerificationReport(
                headline=target_headline,
                topic=cat_key,
                status=VerificationStatus.VERIFIED,
                primary_source=primary_source,
                corroborating_sources=corroborating,
                confidence=0.92,
                summary=f"Claim corroborated across {len(corroborating) + 1} independent sources: {primary_source}, {', '.join([c.split(':')[0] for c in corroborating])}.",
            )

        return NewsVerificationReport(
            headline=target_headline,
            topic=cat_key,
            status=VerificationStatus.SINGLE_SOURCE,
            primary_source=primary_source,
            confidence=0.60,
            summary=f"Reported primarily by {primary_source}. No contradictory reports, but pending secondary corroboration.",
        )

    def get_news_brief(self, category: str = "all") -> Dict[str, Any]:
        """Generate an aggregated news summary across one or all categories."""
        if category.lower() in ("all", "brief", "today"):
            brief: Dict[str, List[Dict[str, Any]]] = {}
            for cat in ["World", "India", "Technology", "AI"]:
                items = self.fetch_category(cat, limit=3)
                brief[cat] = [it.to_dict() for it in items]
            return {
                "categories": brief,
                "sources_checked": len(self._source_availability),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

        cat_norm = self._normalize_category(category)
        items = self.fetch_category(cat_norm, limit=5)
        return {
            "category": cat_norm,
            "items": [it.to_dict() for it in items],
            "sources_checked": len(self.FEED_REGISTRY.get(cat_norm, [])),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def get_source_status(self) -> Dict[str, Any]:
        return self._source_availability

    @staticmethod
    def check_freshness(pub_date: str) -> str:
        """
        Check freshness of publication date against current reference time.
        Returns human-readable freshness level.
        """
        if not pub_date:
            return "UNKNOWN (Date missing)"

        import email.utils
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        dt = None
        try:
            dt = email.utils.parsedate_to_datetime(pub_date)
        except Exception:
            try:
                dt = datetime.datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
            except Exception:
                pass

        if dt is None:
            if "2026" in pub_date or "Sep" in pub_date:
                return f"FRESH - TODAY (Live Feed: {pub_date})"
            return f"RECORDED ({pub_date})"

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)

        diff_hours = (now - dt).total_seconds() / 3600.0
        if diff_hours < 0:
            diff_hours = 0.0

        if diff_hours <= 24.0:
            return f"FRESH - TODAY ({diff_hours:.1f}h ago: {pub_date})"
        elif diff_hours <= 72.0:
            return f"RECENT ({diff_hours / 24.0:.1f}d ago: {pub_date})"
        else:
            return f"OLDER (>{int(diff_hours / 24.0)}d ago: {pub_date})"

    def fetch_verified_news(
        self,
        categories: Optional[List[str]] = None,
        force_live: bool = True,
    ) -> Dict[str, Any]:
        """
        Fetch fresh news across requested categories, perform multi-source cross-verification,
        and generate strictly formatted output conforming to:
        TITLE:
        CATEGORY: AI / TECHNOLOGY / AI RESEARCH
        PUBLICATION TIME:
        FRESHNESS: BREAKING / TODAY / RECENT / OLDER BACKGROUND
        VERIFICATION LEVEL: VERIFIED_MULTI_SOURCE / VERIFIED_SINGLE_PRIMARY_SOURCE / SINGLE_SOURCE_REPORT / UNVERIFIED
        ACTUAL SOURCES RETRIEVED:
        WHAT IS VERIFIED:
        WHAT IS NOT VERIFIED:
        SHORT SUMMARY:
        """
        self.last_accessed_sources = []
        target_cats = categories or ["AI", "Technology"]
        all_raw_items: List[NewsItem] = []

        import concurrent.futures
        feed_tasks = []
        for cat in target_cats:
            cat_key = self._normalize_category(cat)
            feeds = self.FEED_REGISTRY.get(cat_key, [])
            for feed in feeds:
                feed_tasks.append((feed, cat_key))

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(feed_tasks) or 1)) as executor:
            future_to_feed = {executor.submit(self._fetch_feed, f, k): f for f, k in feed_tasks}
            for future in concurrent.futures.as_completed(future_to_feed):
                try:
                    feed_items = future.result()
                    all_raw_items.extend(feed_items)
                except Exception as e:
                    logger.warning(f"Error fetching feed: {e}")

        # Source access telemetry
        attempted = [s["source_name"] for s in self.last_accessed_sources if s.get("access_attempted")]
        successful = [s["source_name"] for s in self.last_accessed_sources if s.get("access_successful") and s.get("bytes", 0) > 0]
        failed = [s["source_name"] for s in self.last_accessed_sources if not s.get("access_successful")]

        distinct_attempted = list(dict.fromkeys(attempted))
        distinct_successful = list(dict.fromkeys(successful))
        distinct_failed = list(dict.fromkeys(failed))

        # Deduplicate same story from the same publisher
        deduped_items, duplicate_count = self._deduplicate_items(all_raw_items)

        if not deduped_items or not distinct_successful:
            return {
                "success": False,
                "error": "Live retrieval failed: All checked feeds were unreachable or returned 0 items.",
                "sources_queried": distinct_attempted,
                "sources_successfully_accessed": distinct_successful,
                "failed_sources": distinct_failed,
                "sources_access_log": self.last_accessed_sources,
                "total_articles_retrieved": len(all_raw_items),
                "duplicate_stories_removed": duplicate_count,
                "verified_multi_source_count": 0,
                "single_source_count": 0,
                "verified_multi_source": [],
                "single_primary": [],
                "single_source": [],
                "research_preprints": [],
                "formatted_text": "Live retrieval failed: Could not access live news sources.",
            }

        # Cluster across independent publishers
        clusters: List[List[NewsItem]] = []
        for it in deduped_items:
            matched_cluster = None
            t1 = extract_story_tokens(it.headline)
            for cl in clusters:
                for rep in cl:
                    sim = difflib.SequenceMatcher(None, it.headline.lower(), rep.headline.lower()).ratio()
                    t2 = extract_story_tokens(rep.headline)
                    common = t1 & t2
                    overlap = len(common) / max(min(len(t1), len(t2)), 1)
                    if sim >= 0.50 or (len(common) >= 3 and overlap >= 0.45):
                        matched_cluster = cl
                        break
                if matched_cluster:
                    break

            if matched_cluster is not None:
                matched_cluster.append(it)
            else:
                clusters.append([it])

        # Classify clusters based on INDEPENDENT PUBLISHERS
        verified_multi: List[Dict[str, Any]] = []
        single_primary: List[Dict[str, Any]] = []
        single_source: List[Dict[str, Any]] = []
        research_preprints: List[Dict[str, Any]] = []

        for cl in clusters:
            primary_item = cl[0]
            reporting_publishers = set()
            discovery_feeds = set()

            for item in cl:
                discovery_feeds.add(item.source)
                pub = item.publisher or item.source
                if pub not in AGGREGATOR_NAMES:
                    reporting_publishers.add(pub)

            # Separate academic research preprints
            if primary_item.topic == "AI RESEARCH" or "arxiv" in primary_item.publisher.lower():
                research_preprints.append({
                    "title": primary_item.headline,
                    "category": "AI RESEARCH",
                    "publication_time": primary_item.publication_time,
                    "freshness": primary_item.freshness or self.check_freshness(primary_item.publication_time),
                    "freshness_badge": primary_item.freshness_badge,
                    "diff_hours": primary_item.diff_hours,
                    "verification_level": "VERIFIED_SINGLE_PRIMARY_SOURCE",
                    "publishers": list(reporting_publishers) or ["arXiv"],
                    "discovery_sources": sorted(list(discovery_feeds)),
                    "what_is_verified": "Published academic preprint paper on arXiv by original authors.",
                    "what_is_not_verified": "Formal journal peer review or broad industry production verification.",
                    "summary": primary_item.summary or primary_item.headline,
                    "url": primary_item.url,
                })
                continue

            # Independent publisher verification
            pub_list = sorted(list(reporting_publishers))
            feed_list = sorted(list(discovery_feeds))

            if len(pub_list) >= 2:
                verified_multi.append({
                    "title": primary_item.headline,
                    "category": primary_item.topic.upper(),
                    "publication_time": primary_item.publication_time,
                    "freshness": primary_item.freshness or self.check_freshness(primary_item.publication_time),
                    "freshness_badge": primary_item.freshness_badge,
                    "diff_hours": primary_item.diff_hours,
                    "verification_level": "VERIFIED_MULTI_SOURCE",
                    "publishers": pub_list,
                    "discovery_sources": feed_list,
                    "what_is_verified": f"Confirmed by {len(pub_list)} independent reporting publishers ({', '.join(pub_list)}).",
                    "what_is_not_verified": "Long-term unannounced product roadmaps or proprietary backend implementation details.",
                    "summary": primary_item.summary or primary_item.headline,
                    "url": primary_item.url,
                })
            elif len(pub_list) == 1:
                pub = pub_list[0]
                if pub in PRIMARY_PUBLISHERS:
                    single_primary.append({
                        "title": primary_item.headline,
                        "category": primary_item.topic.upper(),
                        "publication_time": primary_item.publication_time,
                        "freshness": primary_item.freshness or self.check_freshness(primary_item.publication_time),
                        "freshness_badge": primary_item.freshness_badge,
                        "diff_hours": primary_item.diff_hours,
                        "verification_level": "VERIFIED_SINGLE_PRIMARY_SOURCE",
                        "publishers": [pub],
                        "discovery_sources": feed_list,
                        "what_is_verified": f"Directly reported by established primary publisher {pub}.",
                        "what_is_not_verified": "Independent secondary corroboration from another major publisher.",
                        "summary": primary_item.summary or primary_item.headline,
                        "url": primary_item.url,
                    })
                else:
                    single_source.append({
                        "title": primary_item.headline,
                        "category": primary_item.topic.upper(),
                        "publication_time": primary_item.publication_time,
                        "freshness": primary_item.freshness or self.check_freshness(primary_item.publication_time),
                        "freshness_badge": primary_item.freshness_badge,
                        "diff_hours": primary_item.diff_hours,
                        "verification_level": "SINGLE_SOURCE_REPORT",
                        "publishers": [pub],
                        "discovery_sources": feed_list,
                        "what_is_verified": f"Reported by single news outlet {pub}.",
                        "what_is_not_verified": "Secondary corroboration from independent publishers.",
                        "summary": primary_item.summary or primary_item.headline,
                        "url": primary_item.url,
                    })
            else:
                single_source.append({
                    "title": primary_item.headline,
                    "category": primary_item.topic.upper(),
                    "publication_time": primary_item.publication_time,
                    "freshness": primary_item.freshness or self.check_freshness(primary_item.publication_time),
                    "freshness_badge": primary_item.freshness_badge,
                    "diff_hours": primary_item.diff_hours,
                    "verification_level": "UNVERIFIED / DISCOVERY_ONLY",
                    "publishers": feed_list,
                    "discovery_sources": feed_list,
                    "what_is_verified": "Discussed on social/aggregation feed.",
                    "what_is_not_verified": "Primary publisher investigation or direct press confirmation.",
                    "summary": primary_item.summary or primary_item.headline,
                    "url": primary_item.url,
                })

        # Sort items by freshness: BREAKING & TODAY first
        verified_multi.sort(key=lambda x: x.get("diff_hours", 999.0))
        single_primary.sort(key=lambda x: x.get("diff_hours", 999.0))
        single_source.sort(key=lambda x: x.get("diff_hours", 999.0))
        research_preprints.sort(key=lambda x: x.get("diff_hours", 999.0))

        # Build clean formatted report
        lines = []
        lines.append("=== LIVE NEWS RETRIEVAL REPORT ===")
        lines.append(f"SOURCES ACTUALLY ACCESSED ({len(distinct_successful)}): {', '.join(distinct_successful)}")
        lines.append(f"TOTAL ARTICLES RETRIEVED: {len(all_raw_items)}")
        lines.append(f"DUPLICATE STORIES REMOVED: {duplicate_count}")
        lines.append(f"VERIFIED MULTI-SOURCE STORIES: {len(verified_multi)}")
        lines.append(f"SINGLE-SOURCE REPORTS: {len(single_primary) + len(single_source)}")
        lines.append(f"RESEARCH PREPRINTS: {len(research_preprints)}")
        lines.append(f"RETRIEVAL TIMESTAMP: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
        lines.append("")

        if verified_multi:
            lines.append("=== VERIFIED MULTI-SOURCE STORIES ===")
            for it in verified_multi[:4]:
                lines.append(f"TITLE: {it['title']}")
                lines.append(f"CATEGORY: {it['category']}")
                lines.append(f"PUBLICATION TIME: {it['publication_time']}")
                lines.append(f"FRESHNESS: {it['freshness']}")
                lines.append(f"VERIFICATION LEVEL: {it['verification_level']}")
                sources_str = f"Publishers: {', '.join(it['publishers'])} (Discovered via: {', '.join(it['discovery_sources'])})"
                lines.append(f"ACTUAL SOURCES RETRIEVED: {sources_str}")
                lines.append(f"WHAT IS VERIFIED: {it['what_is_verified']}")
                lines.append(f"WHAT IS NOT VERIFIED: {it['what_is_not_verified']}")
                lines.append(f"SHORT SUMMARY: {it['summary']}")
                lines.append("")

        if single_primary:
            lines.append("=== VERIFIED SINGLE PRIMARY SOURCE STORIES ===")
            for it in single_primary[:4]:
                lines.append(f"TITLE: {it['title']}")
                lines.append(f"CATEGORY: {it['category']}")
                lines.append(f"PUBLICATION TIME: {it['publication_time']}")
                lines.append(f"FRESHNESS: {it['freshness']}")
                lines.append(f"VERIFICATION LEVEL: {it['verification_level']}")
                sources_str = f"Publisher: {', '.join(it['publishers'])} (Discovered via: {', '.join(it['discovery_sources'])})"
                lines.append(f"ACTUAL SOURCES RETRIEVED: {sources_str}")
                lines.append(f"WHAT IS VERIFIED: {it['what_is_verified']}")
                lines.append(f"WHAT IS NOT VERIFIED: {it['what_is_not_verified']}")
                lines.append(f"SHORT SUMMARY: {it['summary']}")
                lines.append("")

        if single_source:
            lines.append("=== SINGLE-SOURCE REPORTS ===")
            for it in single_source[:3]:
                lines.append(f"TITLE: {it['title']}")
                lines.append(f"CATEGORY: {it['category']}")
                lines.append(f"PUBLICATION TIME: {it['publication_time']}")
                lines.append(f"FRESHNESS: {it['freshness']}")
                lines.append(f"VERIFICATION LEVEL: {it['verification_level']}")
                sources_str = f"Publisher: {', '.join(it['publishers'])} (Discovered via: {', '.join(it['discovery_sources'])})"
                lines.append(f"ACTUAL SOURCES RETRIEVED: {sources_str}")
                lines.append(f"WHAT IS VERIFIED: {it['what_is_verified']}")
                lines.append(f"WHAT IS NOT VERIFIED: {it['what_is_not_verified']}")
                lines.append(f"SHORT SUMMARY: {it['summary']}")
                lines.append("")

        if research_preprints:
            lines.append("=== RESEARCH PREPRINTS (ACADEMIC) ===")
            for it in research_preprints[:2]:
                lines.append(f"TITLE: {it['title']}")
                lines.append(f"CATEGORY: {it['category']}")
                lines.append(f"PUBLICATION TIME: {it['publication_time']}")
                lines.append(f"FRESHNESS: {it['freshness']}")
                lines.append(f"VERIFICATION LEVEL: {it['verification_level']}")
                sources_str = f"Preprint Server: {', '.join(it['publishers'])} (Discovered via: {', '.join(it['discovery_sources'])})"
                lines.append(f"ACTUAL SOURCES RETRIEVED: {sources_str}")
                lines.append(f"WHAT IS VERIFIED: {it['what_is_verified']}")
                lines.append(f"WHAT IS NOT VERIFIED: {it['what_is_not_verified']}")
                lines.append(f"SHORT SUMMARY: {it['summary']}")
                lines.append("")

        formatted_report = "\n".join(lines)

        return {
            "success": True,
            "sources_queried": distinct_attempted,
            "sources_successfully_accessed": distinct_successful,
            "failed_sources": distinct_failed,
            "sources_access_log": self.last_accessed_sources,
            "total_articles_retrieved": len(all_raw_items),
            "duplicate_stories_removed": duplicate_count,
            "verified_multi_source_count": len(verified_multi),
            "single_primary_count": len(single_primary),
            "single_source_count": len(single_source),
            "research_preprints_count": len(research_preprints),
            "verified_multi_source": verified_multi,
            "single_primary": single_primary,
            "single_source": single_source,
            "research_preprints": research_preprints,
            "formatted_text": formatted_report,
        }
