"""
Structured disruption feeds that complement headline scoring:

* GDACSFeed: the UN/EC Global Disaster Alert and Coordination System publishes current tropical
  cyclones, earthquakes, floods, volcanoes, wildfires and droughts with coordinates and a
  Green/Orange/Red alert level. No key required. Events are matched to hubs by distance with a
  hazard-specific radius, and applied only to the transport modes that hazard disrupts.

* AISMonitor: live vessel positions from aisstream.io (free API key, set AISSTREAM_API_KEY).
  Around each maritime chokepoint it tracks how many vessels are holding position versus moving;
  an unusually high share of holding vessels is reported as CONGESTION.

Both publish into IntelMonitor under their own source name, so the router, alerts and dashboard
treat them like any other intelligence.
"""
import asyncio
import json
import math
import os
import threading
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import requests

GDACS_URL = "https://www.gdacs.org/xml/rss.xml"
_NS = {"gdacs": "http://www.gdacs.org", "georss": "http://www.georss.org/georss"}

# radius_km: how far from the epicentre / track a hub is considered exposed.
# modes: which transport modes the hazard actually disrupts.
HAZARDS = {
    "TC": {"name": "Tropical cyclone", "radius_km": 600, "modes": {"sea", "air", "road", "rail"}, "category": "WEATHER"},
    "EQ": {"name": "Earthquake", "radius_km": 300, "modes": {"sea", "air", "road", "rail"}, "category": "GEOHAZARD"},
    "FL": {"name": "Flood", "radius_km": 150, "modes": {"road", "rail"}, "category": "WEATHER"},
    "VO": {"name": "Volcanic eruption", "radius_km": 400, "modes": {"air"}, "category": "GEOHAZARD"},
    "WF": {"name": "Wildfire", "radius_km": 50, "modes": {"road", "rail"}, "category": "WEATHER"},
    "DR": {"name": "Drought", "radius_km": 300, "modes": {"rail", "road"}, "category": "WEATHER"},
}
# Green is "limited impact" for humanitarian purposes; only storms and quakes still matter for freight.
ALERT_THREAT = {"Green": 0.25, "Orange": 0.6, "Red": 0.9}
GREEN_TYPES = {"TC", "EQ"}


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class GDACSFeed:
    source = "GDACS"

    def __init__(self, hubs: List[dict], url: str = GDACS_URL, timeout: float = 10.0):
        self.hubs = hubs
        self.url = url
        self.timeout = timeout
        self.status: Dict[str, Any] = {"source": self.source, "state": "not_run"}

    @staticmethod
    def parse(xml_text: str) -> List[Dict[str, Any]]:
        root = ET.fromstring(xml_text)
        events = []
        for item in root.findall("./channel/item"):
            etype = item.findtext("gdacs:eventtype", namespaces=_NS)
            level = item.findtext("gdacs:alertlevel", namespaces=_NS)
            point = item.findtext("georss:point", namespaces=_NS)
            current = (item.findtext("gdacs:iscurrent", namespaces=_NS) or "true").lower() == "true"
            if etype not in HAZARDS or level not in ALERT_THREAT or not point or not current:
                continue
            lat, lon = (float(x) for x in point.split()[:2])
            pub = item.findtext("pubDate")
            try:
                ts = parsedate_to_datetime(pub).timestamp() if pub else None
            except (TypeError, ValueError):
                ts = None
            events.append({"type": etype, "level": level, "lat": lat, "lon": lon,
                           "title": (item.findtext("title") or "").strip(), "link": item.findtext("link"),
                           "country": item.findtext("gdacs:country", namespaces=_NS), "published": ts})
        return events

    def match(self, events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Hub -> strongest exposure. Threat decays linearly from full at the centre to half at the radius."""
        picture: Dict[str, Dict[str, Any]] = {}
        for ev in events:
            hz = HAZARDS[ev["type"]]
            if ev["level"] == "Green" and ev["type"] not in GREEN_TYPES:
                continue
            base = ALERT_THREAT[ev["level"]]
            for hub in self.hubs:
                d = haversine_km(ev["lat"], ev["lon"], hub["lat"], hub["lon"])
                if d > hz["radius_km"]:
                    continue
                threat = round(base * (1.0 - 0.5 * d / hz["radius_km"]), 3)
                by_mode = {m: (threat if m in hz["modes"] else 0.0) for m in hub["modes"]}
                if max(by_mode.values()) <= 0:
                    continue
                cur = picture.get(hub["id"])
                if cur and max(cur["threat_by_mode"].values()) >= threat:
                    continue
                picture[hub["id"]] = {
                    "hub_id": hub["id"], "source": self.source, "confidence": 1.0,
                    "headline": f"{ev['title']} ({round(d)} km from {hub['display_name']})",
                    "link": ev["link"], "published": ev["published"], "score": base,
                    "category": hz["category"], "threat_by_mode": by_mode, "observed_at": time.time(),
                    "details": {"hazard": hz["name"], "alert_level": ev["level"], "distance_km": round(d)},
                }
        return picture

    def refresh(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """Fetch and match. Returns None (and keeps the previous picture) if the feed is unreachable."""
        try:
            resp = requests.get(self.url, timeout=self.timeout, headers={"User-Agent": "Supplychainer/1.0"})
            resp.raise_for_status()
            events = self.parse(resp.text)
        except Exception as e:
            self.status = {"source": self.source, "state": "error", "error": str(e)[:200], "at": time.time()}
            return None
        picture = self.match(events)
        self.status = {"source": self.source, "state": "ok", "at": time.time(), "events": len(events),
                       "hubs_exposed": len(picture)}
        return picture


# Watch boxes around the maritime chokepoints: (lat_min, lon_min, lat_max, lon_max).
AIS_ZONES = {
    "CHOKE-SUEZ": (29.8, 32.2, 31.4, 32.7),
    "CHOKE-BABEL": (12.2, 42.9, 13.0, 43.8),
    "CHOKE-HORMUZ": (25.9, 55.8, 27.0, 57.0),
    "CHOKE-MALACCA": (1.0, 100.5, 3.5, 104.0),
    "CHOKE-GIBRAL": (35.7, -6.1, 36.3, -5.2),
    "CHOKE-PANAMA": (8.8, -80.0, 9.5, -79.4),
    "CHOKE-BOSPHO": (40.9, 28.9, 41.3, 29.2),
    "CHOKE-DOVER": (50.8, 1.1, 51.3, 1.9),
}
HOLDING_KNOTS = 0.5
HOLDING_STATUSES = {1, 5}  # AIS navigational status: at anchor, moored


class AISMonitor:
    """
    Chokepoint congestion from AIS position reports.

    A zone raises CONGESTION when at least `min_vessels` distinct ships were seen in the window
    and the share holding position exceeds the normal anchorage level (`baseline_holding`).
    """
    source = "AIS"
    url = "wss://stream.aisstream.io/v0/stream"

    def __init__(self, hubs_by_id: Dict[str, dict], api_key: Optional[str] = None, window_s: int = 2 * 3600,
                 min_vessels: int = 15, baseline_holding: float = 0.35):
        self.hubs = hubs_by_id
        self.api_key = api_key if api_key is not None else os.getenv("AISSTREAM_API_KEY")
        self.window_s = window_s
        self.min_vessels = min_vessels
        self.baseline_holding = baseline_holding
        self._lock = threading.Lock()
        self._seen: Dict[str, Dict[int, tuple]] = {z: {} for z in AIS_ZONES}  # zone -> mmsi -> (ts, sog, status)
        self.messages = 0
        self.status: Dict[str, Any] = ({"source": self.source, "state": "disabled",
                                        "reason": "Set AISSTREAM_API_KEY (free at aisstream.io) to enable."}
                                       if not self.api_key else {"source": self.source, "state": "starting"})

    @staticmethod
    def zone_of(lat: float, lon: float) -> Optional[str]:
        for zone, (la0, lo0, la1, lo1) in AIS_ZONES.items():
            if la0 <= lat <= la1 and lo0 <= lon <= lo1:
                return zone
        return None

    def ingest(self, msg: Dict[str, Any], now: Optional[float] = None):
        """Consume one aisstream.io PositionReport message."""
        if msg.get("MessageType") != "PositionReport":
            return
        meta = msg.get("MetaData", {})
        report = msg.get("Message", {}).get("PositionReport", {})
        lat = report.get("Latitude", meta.get("latitude"))
        lon = report.get("Longitude", meta.get("longitude"))
        mmsi = meta.get("MMSI") if meta.get("MMSI") is not None else report.get("UserID")
        if lat is None or lon is None or mmsi is None:
            return
        zone = self.zone_of(lat, lon)
        if zone is None:
            return
        with self._lock:
            self._seen[zone][mmsi] = (now or time.time(), float(report.get("Sog", 0.0)),
                                      int(report.get("NavigationalStatus", 15)))
            self.messages += 1

    def zone_stats(self, now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
        now = now or time.time()
        out = {}
        with self._lock:
            for zone, ships in self._seen.items():
                for mmsi in [m for m, (ts, _, _) in ships.items() if now - ts > self.window_s]:
                    ships.pop(mmsi)
                n = len(ships)
                holding = sum(1 for _, sog, st in ships.values() if sog < HOLDING_KNOTS or st in HOLDING_STATUSES)
                out[zone] = {"vessels": n, "holding": holding, "holding_share": round(holding / n, 3) if n else 0.0}
        return out

    def picture(self, now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
        pic = {}
        for zone, st in self.zone_stats(now).items():
            if st["vessels"] < self.min_vessels or st["holding_share"] <= self.baseline_holding:
                continue
            # Excess holding over the normal anchorage level, scaled so everyone holding -> 1.0.
            threat = round(min(1.0, (st["holding_share"] - self.baseline_holding) / (1 - self.baseline_holding)), 3)
            hub = self.hubs.get(zone)
            if not hub:
                continue
            pic[zone] = {
                "hub_id": zone, "source": self.source, "confidence": 0.75, "score": threat,
                "headline": f"AIS: {st['holding']} of {st['vessels']} vessels near {hub['display_name']} holding "
                            f"position (<{HOLDING_KNOTS} kn or anchored) over the last {self.window_s // 3600}h",
                "link": None, "published": now or time.time(), "category": "CONGESTION",
                "threat_by_mode": {m: (round(threat * 0.75, 3) if m == "sea" else 0.0) for m in hub["modes"]},
                "observed_at": now or time.time(), "details": st,
            }
        return pic

    async def _run(self, on_update):
        import websockets
        sub = {"APIKey": self.api_key, "FilterMessageTypes": ["PositionReport"],
               "BoundingBoxes": [[[la0, lo0], [la1, lo1]] for la0, lo0, la1, lo1 in AIS_ZONES.values()]}
        backoff = 5
        while True:
            try:
                async with websockets.connect(self.url, open_timeout=20) as ws:
                    await ws.send(json.dumps(sub))
                    self.status = {"source": self.source, "state": "connected", "at": time.time()}
                    backoff = 5
                    last_push = 0.0
                    async for raw in ws:
                        self.ingest(json.loads(raw))
                        if time.time() - last_push > 60:
                            last_push = time.time()
                            self.status.update(messages=self.messages, zones=self.zone_stats())
                            on_update(self.picture())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.status = {"source": self.source, "state": "reconnecting", "error": str(e)[:200], "at": time.time()}
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

    def start(self, on_update) -> Optional[threading.Thread]:
        if not self.api_key:
            return None
        t = threading.Thread(target=lambda: asyncio.run(self._run(on_update)), daemon=True, name="ais-stream")
        t.start()
        return t
