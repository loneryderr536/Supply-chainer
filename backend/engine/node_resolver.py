import json
import os
from typing import Dict, Optional, Any


class NodeResolver:
    """
    Supplychainer Unified Node Resolver.

    Maps a city name, hub ID or hub alias to the virtual (hub:mode) nodes a route may start or
    end at, honouring the requested transport mode instead of always entering by road.
    """
    def __init__(self, locations_path: str = None, hubs_path: str = None):
        if not locations_path:
            locations_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'canonical_locations.json')
        if not hubs_path:
            hubs_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'canonical_hubs.json')

        self.location_map = {}
        if os.path.exists(locations_path):
            with open(locations_path, 'r') as f:
                self.location_map = json.load(f)

        self.hubs = []
        if os.path.exists(hubs_path):
            with open(hubs_path, 'r') as f:
                self.hubs = json.load(f)
        self.hub_by_id = {h["id"]: h for h in self.hubs}
        self._city_ci = {k.lower(): k for k in self.location_map}
        self._alias_ci = {}
        for h in self.hubs:
            for a in h.get("aliases", []):
                self._alias_ci.setdefault(a.lower(), h["id"])

    def _lookup(self, location_or_id: str, preferred_mode: Optional[str]) -> Optional[str]:
        """Physical hub for the input; for a city, the hub serving the preferred mode (else road)."""
        if location_or_id in self.hub_by_id:
            return location_or_id
        key = (location_or_id or "").strip()
        city = self.location_map.get(key) or self.location_map.get(self._city_ci.get(key.lower(), ""))
        if city:
            if preferred_mode and preferred_mode in city:
                return city[preferred_mode]
            return city.get("road") or next(iter(city.values()))
        upper = key.upper()
        if upper in self.hub_by_id:
            return upper
        return self._alias_ci.get(key.lower())

    def resolve_entry_points(self, location_or_id: str, preferred_mode: str = "any") -> Dict[str, Any]:
        """
        Returns {"hub": physical_id, "ids": [virtual nodes]}.

        * A hub ID with no mode preference may start/end on any of its modes (the cargo is
          already at that facility, so no artificial road->X transfer is forced).
        * A city with no preference starts at its road distribution hub (first mile by truck).
        * With a preference, the matching mode node is used when the hub supports it.
        """
        pref = None if preferred_mode in (None, "", "any") else preferred_mode
        is_hub_input = location_or_id in self.hub_by_id or (location_or_id or "").upper() in self.hub_by_id
        physical_id = self._lookup(location_or_id, pref)
        if not physical_id:
            return {"hub": None, "ids": [], "error": f"Entry point unavailable for {location_or_id}"}
        hub = self.hub_by_id.get(physical_id)
        if not hub:
            return {"hub": None, "ids": [], "error": f"Physical Hub mapping corrupted for {physical_id}"}

        modes = hub["modes"]
        if pref and pref in modes:
            ids = [f"{physical_id}:{pref}"]
        elif is_hub_input:
            ids = [f"{physical_id}:{m}" for m in modes]
        elif "road" in modes:
            ids = [f"{physical_id}:road"]
        else:
            ids = [f"{physical_id}:{modes[0]}"]
        return {"hub": physical_id, "ids": ids}

    def resolve_node_to_entry_point(self, location_or_id: str, preferred_mode: str = None) -> Dict[str, Any]:
        """Single-node variant kept for older callers."""
        res = self.resolve_entry_points(location_or_id, preferred_mode or "any")
        if "error" in res:
            return {"id": None, "error": res["error"]}
        return {"id": res["ids"][0]}

    def resolve_node(self, location_or_id: str, mode: str = "any") -> str:
        """Legacy support"""
        return self.resolve_node_to_entry_point(location_or_id, mode).get("id")
