import calendar
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional

import feedparser
import requests


class DynamicNewsIngestor:
    """
    Supplychainer Stage 1: Dynamic News Ingestion.
    Consumes live external intelligence from Google News RSS with a per-request timeout and cache.
    """
    def __init__(self, timeout: float = 4.0, max_age_days: int = 7):
        self.cache = {}  # {query: (timestamp, headlines)}
        self.cache_ttl = 900  # 15 minutes
        self.timeout = timeout
        self.max_age_days = max_age_days

        # Mode descriptions shown when no live signal exists. These are NOT news about any
        # location and are never scored as threats.
        self.fallback_news = {
            "sea": "Maritime congestion reported at major transshipment hubs. Berthing delays expected.",
            "air": "Aviation fuel surcharge volatility and cargo handling backlogs noted in international airports.",
            "road": "Highway traffic density increasing in primary logistics corridors.",
            "rail": "Rail freight scheduling adjustments due to infrastructure maintenance."
        }

    def fetch_headlines(self, location: str) -> Optional[List[Dict[str, Any]]]:
        """
        Recent headlines mentioning `location`. Returns None when the feed could not be reached
        (so callers can tell "no news" apart from "no connectivity").
        """
        query = f'"{location}" (shipping OR port OR freight OR logistics OR airport OR rail) when:{self.max_age_days}d'
        now = time.time()
        if query in self.cache and now - self.cache[query][0] < self.cache_ttl:
            return self.cache[query][1]
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
               + "&hl=en-US&gl=US&ceid=US:en")
        try:
            # A per-request timeout; the old socket.setdefaulttimeout() changed it for the whole process.
            resp = requests.get(url, timeout=self.timeout, headers={"User-Agent": "Supplychainer/1.0"})
            resp.raise_for_status()
        except Exception as e:
            print(f"[NEWS] fetch failed for {location}: {e}")
            return None
        feed = feedparser.parse(resp.content)
        cutoff = now - self.max_age_days * 86400
        headlines = []
        for entry in feed.entries[:10]:
            published = entry.get("published_parsed")
            ts = calendar.timegm(published) if published else None
            if ts is not None and ts < cutoff:
                continue
            headlines.append({"title": entry.get("title", ""), "link": entry.get("link"), "published": ts})
        self.cache[query] = (now, headlines)
        return headlines

    def get_latest_news(self, location: str, transport_mode: str) -> str:
        """Backward-compatible: joined headlines, or the mode description when nothing is available."""
        headlines = self.fetch_headlines(location)
        if headlines:
            return " | ".join(h["title"] for h in headlines[:3])
        return self.fallback_news.get(transport_mode.lower(), "Normal operational conditions reported.")


# A headline must describe a disruption *event* before its semantic severity is trusted. Without
# this gate, real-world geopolitics/business headlines ("Is America China's adversary?",
# "Changi is the best investment ever made") scored 0.3-0.65 against the disaster anchors.
EVENT_TERMS = re.compile(
    r"\b(strik\w*|walkout\w*|lockout\w*|protest\w*|blockad\w*|block(?:ed|age|ages|ing)|clos(?:ed|ure|ures|ing)|"
    r"shut\w*|suspen\w*|halt\w*|disrupt\w*|delay\w*|backlog\w*|congest\w*|queue\w*|ground(?:ed|ing)|aground|"
    r"collid\w*|collision\w*|capsiz\w*|sank|sink(?:s|ing)?|fire|fires|explosion\w*|blast\w*|attack\w*|missile\w*|"
    r"drone\w*|hijack\w*|pira(?:cy|tes?)|seiz\w*|conflict\w*|sanction\w*|embargo\w*|flood\w*|storm\w*|"
    r"typhoon\w*|hurricane\w*|cyclone\w*|earthquake\w*|tsunami\w*|landslide\w*|derail\w*|collaps\w*|outage\w*|"
    r"cyberattack\w*|ransomware|hack(?:ed|ers?)|evacuat\w*|cancel\w*|divert\w*|rerout\w*|accident\w*|spill\w*)\b",
    re.IGNORECASE)


# Unverified headlines influence routing at reduced weight until an analyst confirms them
# (an analyst report on the same hub replaces the entry at full weight).
# Structured feeds are authoritative (GDACS) or instrument-derived but heuristic (AIS congestion).
SOURCE_CONFIDENCE = {"LIVE_NEWS": 0.5, "ANALYST": 1.0, "GDACS": 1.0, "AIS": 0.75}


class IntelMonitor:
    """
    Keeps a hub -> threat picture from several sources: live headlines and analyst reports
    (scored by the contrastive NLP engine and filtered per mode by CARF) and structured feeds
    (GDACS disaster alerts, AIS vessel congestion). Signals are stored per (hub, source) and
    merged per hub; the route recommender reads the merged picture on every request.
    """

    def __init__(self, hubs: List[dict], nlp, carf, ingestor: Optional[DynamicNewsIngestor] = None,
                 ttl_s: int = 6 * 3600, max_hubs: int = 40):
        self.hubs = {h["id"]: h for h in hubs}
        self.nlp = nlp
        self.carf = carf
        self.ingestor = ingestor or DynamicNewsIngestor()
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._picture: Dict[tuple, Dict[str, Any]] = {}  # (hub_id, source) -> entry
        self.last_scan: Optional[Dict[str, Any]] = None
        ranked = sorted(hubs, key=lambda h: (h["type"] != "choke_point", -h.get("importance", 0)))
        self.watchlist = [h["id"] for h in ranked if h["type"] == "choke_point" or h.get("importance", 0) >= 9][:max_hubs]

    def _mask_location(self, hub: dict, text: str) -> str:
        """
        Remove the hub's own names before semantic scoring. The safe anchor corpus mentions
        real places (e.g. "Operations at the Port of Rotterdam are proceeding normally"), so an
        unmasked name drags any report about that place toward "safe". Location relevance is
        already established by which hub the report is attached to.
        """
        for name in sorted(self._names(hub), key=len, reverse=True):
            text = re.sub(re.escape(name), "the location", text, flags=re.IGNORECASE)
        return text

    @staticmethod
    def _names(hub: dict) -> List[str]:
        return [n for n in {hub["display_name"], hub.get("parent_city") or "", *hub.get("aliases", [])} if len(n) >= 3]

    def _mentions_hub(self, hub: dict, text: str) -> bool:
        return any(re.search(r"\b" + re.escape(n) + r"\b", text, re.IGNORECASE) for n in self._names(hub))

    def gate(self, hub: dict, text: str, require_location: bool) -> Optional[str]:
        """Reason a headline is rejected before scoring, or None if it may be scored."""
        if require_location and not self._mentions_hub(hub, text):
            return "does not mention this hub"
        if not EVENT_TERMS.search(text):
            return "no disruption event described"
        return None

    def _score(self, hub_id: str, items: List[Dict[str, Any]], source: str) -> Optional[Dict[str, Any]]:
        hub = self.hubs[hub_id]
        best, by_mode = None, {m: 0.0 for m in hub["modes"]}
        for item in items:
            text = item["title"]
            # Search results are loosely matched, so live headlines must name the hub; analyst
            # reports are attached to a hub explicitly.
            if self.gate(hub, text, require_location=(source == "LIVE_NEWS")):
                continue
            analysis = self.nlp.analyze(self._mask_location(hub, text))
            if analysis["score"] <= 0:
                continue
            for m in hub["modes"]:
                by_mode[m] = max(by_mode[m], self.carf.apply_filter(analysis["score"], text, m))
            if best is None or analysis["score"] > best[1]["score"]:
                best = (item, analysis)
        if best is None or max(by_mode.values()) <= 0:
            return None
        item, analysis = best
        confidence = SOURCE_CONFIDENCE.get(source, 1.0)
        return {"hub_id": hub_id, "headline": item["title"], "link": item.get("link"),
                "published": item.get("published"), "score": round(analysis["score"], 3),
                "category": analysis["category"], "confidence": confidence,
                "threat_by_mode": {m: round(v * confidence, 3) for m, v in by_mode.items()},
                "source": source, "observed_at": time.time()}

    def report(self, hub_id: str, text: str, source: str = "ANALYST") -> Dict[str, Any]:
        """Score a free-text report against a hub (analyst intel / structured feed / tests)."""
        if hub_id not in self.hubs:
            raise KeyError(hub_id)
        if not self.nlp.ready:
            raise RuntimeError("NLP engine is not ready")
        entry = self._score(hub_id, [{"title": text}], source)
        with self._lock:
            # An analyst's call on a hub supersedes unverified headlines about it.
            self._picture.pop((hub_id, "LIVE_NEWS"), None)
            if entry:
                self._picture[(hub_id, source)] = entry
            else:
                self._picture.pop((hub_id, source), None)
        if entry:
            return entry
        rejected = self.gate(self.hubs[hub_id], text, require_location=False)
        return {"hub_id": hub_id, "score": 0.0, "threat_by_mode": {}, "headline": text,
                "note": f"No threat recorded: {rejected}." if rejected else
                        "No threat recorded: scored below the noise floor or filtered by CARF."}

    def clear(self, hub_id: Optional[str] = None, source: Optional[str] = None):
        with self._lock:
            for key in list(self._picture):
                if (hub_id is None or key[0] == hub_id) and (source is None or key[1] == source):
                    self._picture.pop(key)

    def replace_source(self, source: str, entries: Dict[str, Dict[str, Any]]):
        """Swap in a structured feed's latest picture (hub_id -> entry) for that source."""
        with self._lock:
            for key in [k for k in self._picture if k[1] == source]:
                self._picture.pop(key)
            for hub_id, entry in entries.items():
                if hub_id in self.hubs:
                    self._picture[(hub_id, source)] = entry

    def scan(self, hub_ids: Optional[List[str]] = None, workers: int = 8) -> Dict[str, Any]:
        """Fetch + score live headlines for the watchlist (or the given hubs)."""
        if not self.nlp.ready:
            self.last_scan = {"at": time.time(), "status": "skipped", "reason": "NLP engine not ready"}
            return self.last_scan
        targets = [h for h in (hub_ids or self.watchlist) if h in self.hubs]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fetched = list(pool.map(lambda h: (h, self.ingestor.fetch_headlines(self.hubs[h]["display_name"])), targets))
        reachable = [(h, items) for h, items in fetched if items is not None]
        threats = 0
        with self._lock:
            for hub_id, items in reachable:
                entry = self._score(hub_id, items, "LIVE_NEWS")
                if entry and (hub_id, "ANALYST") not in self._picture:
                    self._picture[(hub_id, "LIVE_NEWS")] = entry
                    threats += 1
                else:
                    self._picture.pop((hub_id, "LIVE_NEWS"), None)
        self.last_scan = {"at": time.time(), "status": "ok" if reachable else "offline",
                          "hubs_scanned": len(targets), "hubs_reachable": len(reachable), "threats": threats}
        return self.last_scan

    def entries(self) -> List[Dict[str, Any]]:
        """Every fresh signal, one per (hub, source)."""
        now = time.time()
        with self._lock:
            return [e for e in self._picture.values() if now - e["observed_at"] < self.ttl_s]

    def hub_threats(self) -> Dict[str, Dict[str, Any]]:
        """Merged per hub: per-mode max across sources, described by the strongest signal."""
        merged: Dict[str, Dict[str, Any]] = {}
        for e in self.entries():
            h = e["hub_id"]
            strength = max(e["threat_by_mode"].values(), default=0.0)
            cur = merged.get(h)
            if cur is None:
                merged[h] = {**e, "threat_by_mode": dict(e["threat_by_mode"]), "sources": [e["source"]],
                             "_strength": strength}
                continue
            for m, v in e["threat_by_mode"].items():
                cur["threat_by_mode"][m] = max(cur["threat_by_mode"].get(m, 0.0), v)
            cur["sources"].append(e["source"])
            if strength > cur["_strength"]:
                for k in ("headline", "link", "category", "source", "score", "confidence", "published"):
                    cur[k] = e.get(k)
                cur["_strength"] = strength
        for v in merged.values():
            v.pop("_strength", None)
        return merged
