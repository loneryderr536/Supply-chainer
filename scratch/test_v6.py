import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.engine.multimodal_network import create_multimodal_network
from backend.engine.threat_intelligence import ThreatIntelligencePredictor
from backend.engine.route_recommender import RouteRecommender
from backend.engine.scenario_manager import ScenarioManager

G = create_multimodal_network()
predictor = ThreatIntelligencePredictor()
scenario_mgr = ScenarioManager()

recommender = RouteRecommender(G, predictor, None, scenario_mgr, demo_mode=True)

print("\n--- TEST: SUEZ BLOCKAGE SCENARIO ---")
res_scenario = recommender.recommend(
    source="PORT-SHANGHAI", 
    destination="PORT-ROTTERDAM", 
    transport_preference="sea", 
    routing_policy="STRICT",
    scenario="SUEZ_BLOCK"
)

import json
print(json.dumps(res_scenario, indent=2))

print("\n--- TEST: NORMAL CONDITIONS ---")
res_normal = recommender.recommend(
    source="PORT-SHANGHAI", 
    destination="PORT-ROTTERDAM", 
    transport_preference="sea", 
    routing_policy="STRICT",
    scenario=None
)
print(json.dumps(res_normal, indent=2))

# Verify
assert "CHOKE-SUEZ" in [l["to"] for l in res_normal["recommendations"][0]["legs"]], "Normal conditions must go through Suez!"
assert len(res_scenario["recommendations"]) > 0, "Scenario should have recommendations."
for rec in res_scenario["recommendations"]:
    assert "CHOKE-SUEZ" not in [l["to"] for l in rec["legs"]], "SUEZ_BLOCK must not route through the blocked canal!"
    assert "CHOKE-CAPEGOOD" in [l["to"] for l in rec["legs"]], "SUEZ_BLOCK should divert via the Cape of Good Hope"
assert "SUEZ_BLOCK" in res_scenario["active_scenario"] or "Suez Canal Blockage" in res_scenario["active_scenario"], "Scenario did not activate!"

print("\n--- TEST: FAILURES CONSOLIDATION ---")
res_fail = recommender.recommend(
    source="AIR-COK", 
    destination="RAIL-CHICAGO", 
    transport_preference="any", 
    routing_policy="STRICT",
    scenario=None
)
print(json.dumps(res_fail, indent=2))

print("\n--- ALL TESTS PASSED ---")
