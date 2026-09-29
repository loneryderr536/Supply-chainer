"""
Maritime lane model for the multimodal graph.

The raw `connections` in canonical_hubs.json were authored as one-way "gateway" links
(e.g. PORT-BUSAN -> PORT-JEBEL -> CHOKE-SUEZ) and most chokepoints had no inbound edge at
all, so CHOKE-CAPEGOOD / CHOKE-BABEL / CHOKE-HORMUZ / CHOKE-MALACCA could never appear on
a route. That made the SUEZ_BLOCK, RED_SEA_CONFLICT and HORMUZ_CLOSURE scenarios either
unavoidable (Suez) or no-ops (Bab-el-Mandeb, Hormuz).

This module classifies every sea-capable hub into an ocean basin and forces any
cross-basin voyage through the chokepoint "gates" that physically join those basins.
The result is that:
  * Shanghai -> Rotterdam goes Malacca -> Bab-el-Mandeb -> Suez -> Gibraltar, and
  * when Suez is closed, the only way from the Indian Ocean to the Atlantic is the Cape.
"""
from typing import Dict, List, Optional, Set, Tuple

# Chokepoint -> the basins it joins. A gate is reachable from every sea port in either basin.
GATES: Dict[str, Tuple[str, ...]] = {
    "CHOKE-HORMUZ": ("GULF", "INDIAN"),
    "CHOKE-BABEL": ("INDIAN", "RED"),
    "CHOKE-SUEZ": ("RED", "MED"),
    "CHOKE-GIBRAL": ("MED", "ATLANTIC"),
    "CHOKE-BOSPHO": ("MED", "BLACK"),
    "CHOKE-DARDANELLES": ("MED", "BLACK"),
    "CHOKE-MALACCA": ("INDIAN", "PACIFIC"),
    "CHOKE-LOMBOK": ("INDIAN", "PACIFIC"),
    "CHOKE-CAPEGOOD": ("INDIAN", "ATLANTIC"),
    "CHOKE-PANAMA": ("ATLANTIC", "AMER_WEST"),
}

# Basin pairs that touch across open ocean with no chokepoint in between.
OPEN_OCEAN = {frozenset(("PACIFIC", "AMER_WEST"))}

# Hubs whose position alone is ambiguous (they sit right on a strait or in an enclosed sea).
BASIN_OVERRIDES = {
    "PORT-BANDARABBAS": "GULF",
    "PORT-TANGIER": "ATLANTIC",
    "PORT-LAEMCHA": "PACIFIC",
    "PORT-SIHANOUKVILLE": "PACIFIC",
    "PORT-FREMANTLE": "PACIFIC",   # reached through Lombok in this model
    "CHOKE-TAIWAN": "PACIFIC",
    "CHOKE-CAPEHATT": "ATLANTIC",
    "CHOKE-DOVER": "ATLANTIC",
    "CHOKE-NSR": "ARCTIC",         # seasonal ice route: kept out of the default network
}


def classify_basin(hub: dict) -> Optional[str]:
    """Assign a sea-capable hub to an ocean basin from its coordinates."""
    if hub["id"] in BASIN_OVERRIDES:
        return BASIN_OVERRIDES[hub["id"]]
    lat, lon = hub["lat"], hub["lon"]

    if 36 <= lat <= 47.5 and 46.5 <= lon <= 55.5:
        return "CASPIAN"
    if 40.9 <= lat <= 47.5 and 27.5 <= lon <= 42:
        return "BLACK"
    if 24.0 <= lat <= 31 and 47 <= lon < 56.3:
        return "GULF"
    if 12.5 <= lat < 30.5 and 32 <= lon <= 43.5:
        return "RED"
    if 30 <= lat <= 46.5 and -5.45 <= lon <= 36.5:
        return "MED"
    # Americas Pacific coast: west coast of North America, Pacific side of Panama, Peru/Chile.
    if lon < -100 or (lat < 9.2 and -90 < lon < -76.5) or (lat < -15 and -80 < lon < -69.5):
        return "AMER_WEST"
    if 20 <= lon < 100 and lat < 30:
        return "INDIAN"
    if 100 <= lon < 102.25 and lat < 8:
        return "INDIAN"
    if lon >= 100:
        return "PACIFIC"
    return "ATLANTIC"


def _basins_of(hub_id: str, basin: Dict[str, str]) -> Set[str]:
    if hub_id in GATES:
        return set(GATES[hub_id])
    b = basin.get(hub_id)
    return {b} if b else set()


def build_sea_lanes(hubs: List[dict]) -> Tuple[List[Tuple[str, str]], Dict[str, str]]:
    """
    Returns (undirected sea links, basin assignment).

    Links come from three sources:
      1. Declared `sea` connections whose endpoints share a basin (or are open-ocean neighbours).
         Declared cross-basin links that would "tunnel" past a chokepoint are dropped.
      2. Gate links: every chokepoint connects to every sea hub in the basins it joins,
         and to every other gate sharing one of those basins.
      3. Open-ocean trunks between major ports (importance >= 8) of adjacent basins.
    """
    sea_hubs = [h for h in hubs if "sea" in h["modes"]]
    by_id = {h["id"]: h for h in sea_hubs}
    basin = {h["id"]: classify_basin(h) for h in sea_hubs if h["id"] not in GATES}

    links: Set[frozenset] = set()

    def compatible(a: str, b: str) -> bool:
        ba, bb = _basins_of(a, basin), _basins_of(b, basin)
        if ba & bb:
            return True
        return any(frozenset((x, y)) in OPEN_OCEAN for x in ba for y in bb)

    for h in sea_hubs:
        for conn in h.get("connections", []):
            if conn["mode"] != "sea" or conn["to"] not in by_id or conn["to"] == h["id"]:
                continue
            if compatible(h["id"], conn["to"]):
                links.add(frozenset((h["id"], conn["to"])))

    gates = [g for g in GATES if g in by_id]
    for g in gates:
        for hub_id, b in basin.items():
            if b in GATES[g]:
                links.add(frozenset((g, hub_id)))
        for other in gates:
            if other != g and set(GATES[g]) & set(GATES[other]):
                links.add(frozenset((g, other)))

    for pair in OPEN_OCEAN:
        a, b = tuple(pair)
        majors_a = [i for i, x in basin.items() if x == a and by_id[i].get("importance", 0) >= 8]
        majors_b = [i for i, x in basin.items() if x == b and by_id[i].get("importance", 0) >= 8]
        for u in majors_a:
            for v in majors_b:
                links.add(frozenset((u, v)))

    return [tuple(sorted(l)) for l in links], basin
