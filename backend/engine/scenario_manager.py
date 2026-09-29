from typing import Dict, List, Any, Optional, Iterable


class ScenarioManager:
    """
    Supplychainer Scenario Trigger Engine.

    Two separate notions of "scenario":
      * what-if: passed per request to /api/recommend; never mutates shared state, so two
        dashboard users running different what-ifs cannot contaminate each other's results.
      * live operating picture: scenarios an operator has activated globally. Monitored routes
        are re-evaluated against this set and alerts are raised when they are affected.

    `closure: True` means the affected hubs are impassable while the scenario is active; the
    delay_hours is then the estimated time until reopening (used for the "hold" option).
    """
    SCENARIOS = {
        "SUEZ_BLOCK": {
            "name": "Suez Canal Blockage",
            "description": "Critical maritime corridor obstructed by vessel grounding.",
            "affected_nodes": ["CHOKE-SUEZ"],
            "threat_level": 1.0,
            "delay_hours": 240,  # 10 days
            "closure": True,
            "category": "INFRASTRUCTURE",
            "reason": "Vessel grounding in Canal Narrows. Canal authority estimates 10-day salvage window.",
            "mode": "sea"
        },
        "RED_SEA_CONFLICT": {
            "name": "Red Sea Escalation",
            "description": "Increased regional instability affecting Bab el-Mandeb.",
            "affected_nodes": ["CHOKE-BABEL"],
            "threat_level": 0.85,
            "delay_hours": 72,
            "closure": False,
            "category": "GEOPOLITICAL",
            "reason": "Regional conflict escalation. Vessels rerouting via Cape of Good Hope for risk mitigation.",
            "mode": "sea"
        },
        "LA_PORT_STRIKE": {
            "name": "LA Port Strike",
            "description": "Labor dispute causing terminal shutdowns in Los Angeles.",
            "affected_nodes": ["PORT-LOSANGELES", "PORT-LONGBEACH"],
            "threat_level": 0.9,
            "delay_hours": 120,
            "closure": False,
            "category": "LABOR",
            "reason": "Terminal labor strike. Picket lines at all major berths. Throughput at 0%.",
            "mode": "sea"
        },
        "CHENNAI_FLOOD": {
            "name": "Chennai Monsoon Flooding",
            "description": "Extreme weather disrupting South India logistics.",
            "affected_nodes": ["PORT-CHENNAI", "HUB-CHENNAI"],
            "threat_level": 0.75,
            "delay_hours": 48,
            "closure": False,
            "category": "WEATHER",
            "reason": "Severe urban flooding. Inland road access to Port and Logistics Park is underwater.",
            "mode": "road"
        },
        "DUBAI_AIR_CONGESTION": {
            "name": "Dubai Hub Surge",
            "description": "Massive cargo backlog at DXB/DWC.",
            "affected_nodes": ["AIR-DUBAI"],
            "threat_level": 0.65,
            "delay_hours": 24,
            "closure": False,
            "category": "CONGESTION",
            "reason": "Regional cargo surge exceeding ground handling capacity. 48h clearance backlog.",
            "mode": "air"
        },
        "HORMUZ_CLOSURE": {
            "name": "Hormuz Strait Escalation",
            "description": "Strategic maritime choke point tension.",
            "affected_nodes": ["CHOKE-HORMUZ"],
            "threat_level": 1.0,
            "delay_hours": 168,
            "closure": True,
            "category": "GEOPOLITICAL",
            "reason": "Strategic naval activity. Vessels holding position at Jebel Ali / Colombo.",
            "mode": "sea"
        }
    }

    def __init__(self):
        self.active_scenario_ids: List[str] = []

    # ---------------------------------------------------------------- what-if
    def get_scenario(self, scenario_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if scenario_id and scenario_id in self.SCENARIOS:
            return {"id": scenario_id, **self.SCENARIOS[scenario_id]}
        return None

    def disruptions_for(self, scenario_ids: Iterable[str]) -> Dict[str, Any]:
        """Hub -> disruption for the union of the given scenarios (worst case wins per hub)."""
        disruptions: Dict[str, Any] = {}
        for sid in scenario_ids or []:
            scenario = self.SCENARIOS.get(sid)
            if not scenario:
                continue
            for node in scenario["affected_nodes"]:
                current = disruptions.get(node)
                entry = {
                    "delay": scenario["delay_hours"],
                    "threat": scenario["threat_level"],
                    "closed": scenario.get("closure", False),
                    "category": scenario.get("category"),
                    "reason": scenario["reason"],
                    "scenario_id": sid,
                    "source": "SCENARIO_OVERRIDE",
                }
                if current is None:
                    disruptions[node] = entry
                else:
                    current["delay"] = max(current["delay"], entry["delay"])
                    current["threat"] = max(current["threat"], entry["threat"])
                    current["closed"] = current["closed"] or entry["closed"]
        return disruptions

    # ------------------------------------------------------ live operating picture
    def activate(self, scenario_id: str) -> bool:
        if scenario_id not in self.SCENARIOS:
            return False
        if scenario_id not in self.active_scenario_ids:
            self.active_scenario_ids.append(scenario_id)
        return True

    def deactivate(self, scenario_id: str) -> bool:
        if scenario_id in self.active_scenario_ids:
            self.active_scenario_ids.remove(scenario_id)
            return True
        return False

    def get_active_disruptions(self) -> Dict[str, Any]:
        return self.disruptions_for(self.active_scenario_ids)

    # ------------------------------------------------------------- compatibility
    def activate_scenario(self, scenario_id: Optional[str]):
        """Legacy single-scenario API: replaces the live picture with one scenario (or clears it)."""
        self.active_scenario_ids = [scenario_id] if scenario_id in self.SCENARIOS else []
        return self.SCENARIOS.get(scenario_id) if scenario_id else None

    def get_all_scenarios(self) -> List[Dict[str, Any]]:
        return [{"id": k, **v, "active": k in self.active_scenario_ids} for k, v in self.SCENARIOS.items()]
