"""Graph topology, scenario handling and route optimisation, checked on the numbers."""
import networkx as nx
import pytest

from conftest import route_hubs


class TestNetwork:
    def test_transit_edges_are_bidirectional(self, engine):
        G = engine.unified_graph
        one_way = [(u, v) for u, v, d in G.edges(data=True) if d["type"] == "transit" and not G.has_edge(v, u)]
        assert one_way == []

    @pytest.mark.parametrize("gate", ["CHOKE-SUEZ", "CHOKE-CAPEGOOD", "CHOKE-BABEL", "CHOKE-HORMUZ",
                                      "CHOKE-MALACCA", "CHOKE-GIBRAL", "CHOKE-PANAMA"])
    def test_chokepoints_are_reachable(self, engine, gate):
        assert engine.unified_graph.in_degree(f"{gate}:sea") > 0

    def test_default_asia_europe_lane_uses_suez(self, rec):
        c = rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", transport_preference="sea")["recommendations"][0]
        assert ["CHOKE-MALACCA", "CHOKE-BABEL", "CHOKE-SUEZ", "CHOKE-GIBRAL"] == c["chokepoints"]

    def test_missing_chennai_airport_is_registered(self, engine):
        assert engine.unified_graph.has_node("AIR-CHENNAI:air")


class TestScenarios:
    def test_suez_block_reroutes_every_persona_via_cape(self, rec):
        res = rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", transport_preference="sea", scenario="SUEZ_BLOCK")
        assert res["closed_hubs"] == ["CHOKE-SUEZ"]
        for c in res["recommendations"]:
            assert "CHOKE-SUEZ" not in route_hubs(c)
            assert "CHOKE-CAPEGOOD" in c["chokepoints"]

    def test_suez_block_reports_hold_vs_reroute(self, rec):
        res = rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", transport_preference="sea", scenario="SUEZ_BLOCK")
        hold = res["hold_option"]
        assert hold["waits_at"] == ["CHOKE-SUEZ"]
        assert hold["verdict"] == "REROUTE" and hold["delta_vs_best_reroute_h"] > 0
        # The hold ETA really contains the 240h salvage window.
        baseline = rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", transport_preference="sea")
        assert hold["adjusted_eta"] == pytest.approx(baseline["recommendations"][0]["adjusted_eta"] + 240, abs=0.2)

    def test_red_sea_threat_and_delay_are_reported(self, rec):
        res = rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", transport_preference="sea", scenario="RED_SEA_CONFLICT")
        via_babel = [c for c in res["recommendations"] if "CHOKE-BABEL" in c["chokepoints"]]
        assert via_babel, "at least the FASTEST option should still accept the Red Sea"
        c = via_babel[0]
        assert c["threat_level"] == 0.85
        assert c["audit_trace"]["eta"]["scenario"] == 72
        babel_leg = next(l for l in c["legs"] if l["to"] == "CHOKE-BABEL")
        assert babel_leg["intel_source"] == "SCENARIO" and babel_leg["threat_category"] == "GEOPOLITICAL"
        # The risk-averse personas take the Cape instead.
        safest = next(c for c in res["recommendations"] if "SAFEST" in c["personas"])
        assert "CHOKE-BABEL" not in safest["chokepoints"]

    def test_hormuz_closure_bypasses_the_strait(self, rec):
        res = rec.recommend("PORT-JEBEL", "PORT-SINGAPORE", transport_preference="sea", scenario="HORMUZ_CLOSURE")
        for c in res["recommendations"]:
            assert "CHOKE-HORMUZ" not in route_hubs(c)

    def test_scenario_delay_counted_once_per_hub(self, rec):
        # Chennai flood hits the destination hub; transfers inside the hub must not re-add the 48h.
        res = rec.recommend("Mumbai", "Chennai", scenario="CHENNAI_FLOOD")
        for c in res["recommendations"]:
            assert c["audit_trace"]["eta"]["scenario"] == 48

    def test_what_if_does_not_leak_into_global_state(self, rec):
        rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", scenario="SUEZ_BLOCK")
        assert rec.scenario_mgr.active_scenario_ids == []
        res = rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", transport_preference="sea")
        assert res["applied_scenarios"] == []

    def test_globally_active_scenario_applies_to_new_requests(self, rec):
        rec.scenario_mgr.activate("SUEZ_BLOCK")
        res = rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", transport_preference="sea")
        assert res["applied_scenarios"] == ["SUEZ_BLOCK"]
        assert all("CHOKE-SUEZ" not in route_hubs(c) for c in res["recommendations"])

    def test_unknown_scenario_is_an_error(self, rec):
        assert "error" in rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", scenario="NOPE")


class TestRequestSemantics:
    def test_strict_sea_starts_on_the_water(self, rec):
        res = rec.recommend("Shanghai", "Rotterdam", transport_preference="sea")
        c = res["recommendations"][0]
        assert c["path_nodes"][0] == "PORT-SHANGHAI:sea"
        assert {l["mode"] for l in c["legs"]} <= {"SEA", "TRANSFER", "ROAD"}

    def test_strict_air_has_no_sea_legs(self, rec):
        for c in rec.recommend("Shanghai", "Rotterdam", transport_preference="air")["recommendations"]:
            assert "SEA" not in {l["mode"] for l in c["legs"]}

    def test_cargo_restrictions_are_enforced(self, rec):
        for c in rec.recommend("Shanghai", "Rotterdam", cargo_type="perishable_urgent")["recommendations"]:
            assert "SEA" not in {l["mode"] for l in c["legs"]}

    def test_preferred_policy_is_a_soft_bias(self, rec):
        any_res = rec.recommend("Shanghai", "Rotterdam", transport_preference="any")
        pref = rec.recommend("Shanghai", "Rotterdam", transport_preference="rail", routing_policy="PREFERRED")
        rail_km = lambda res: sum(l["distance_km"] for c in res["recommendations"] for l in c["legs"] if l["mode"] == "RAIL")
        assert rail_km(pref) > rail_km(any_res)
        assert any(l["mode"] != "RAIL" for c in pref["recommendations"] for l in c["legs"] if l["type"] == "transit")

    def test_hub_input_needs_no_artificial_first_mile(self, rec):
        c = rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM")["recommendations"]
        assert all(c_["legs"][0]["type"] == "transit" for c_ in c)


class TestAccounting:
    @pytest.mark.parametrize("scenario", [None, "RED_SEA_CONFLICT", "LA_PORT_STRIKE"])
    def test_totals_match_legs(self, rec, scenario):
        dest = "PORT-LOSANGELES" if scenario == "LA_PORT_STRIKE" else "PORT-ROTTERDAM"
        for c in rec.recommend("PORT-SHANGHAI", dest, transport_preference="sea", scenario=scenario)["recommendations"]:
            leg_cost = sum(l["cost"] for l in c["legs"])
            assert c["total_cost"] == pytest.approx(leg_cost + c["audit_trace"]["cost"]["scenario"], abs=0.05)
            assert c["adjusted_eta"] == pytest.approx(sum(l["eta"] for l in c["legs"]), abs=0.5)

    def test_eta_band_is_consistent(self, rec):
        for c in rec.recommend("Shanghai", "Rotterdam", scenario="RED_SEA_CONFLICT")["recommendations"]:
            b = c["eta_band"]
            assert c["adjusted_eta"] <= b["p50"] <= b["p85"] <= b["p95"] <= b["p95_correlated"]

    def test_ml_model_is_used_in_routing(self, rec):
        c = rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", transport_preference="sea")["recommendations"][0]
        ml = c["audit_trace"]["ml"]
        assert ml["buffer_h"]["p85"] > 0
        assert ml["dominant_leg"]["attribution"]["method"] == "exact_shapley_interventional"

    def test_explanations_contain_no_invented_percentages(self, rec):
        for c in rec.recommend("Shanghai", "Rotterdam")["recommendations"]:
            assert "%" not in c["explanation"]
            assert str(c["eta_band"]["p50"]) in c["explanation"]


class TestLiveIntel:
    def test_analyst_report_changes_routing_inputs(self, rec):
        before = rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", transport_preference="sea")["recommendations"][0]
        entry = rec.intel.report("PORT-ROTTERDAM",
                                 "Dockworkers at the Port of Rotterdam begin a five-day strike, container terminals shut.")
        assert entry["category"] == "LABOR"
        assert entry["threat_by_mode"]["sea"] > 0.3 and entry["threat_by_mode"]["rail"] == 0.0
        repriced = rec.evaluate_path(before["path_nodes"], "BALANCED")
        assert "PORT-ROTTERDAM" in repriced["affected_hubs"]
        assert repriced["eta_band"]["p85"] > before["eta_band"]["p85"]

    def test_benign_report_records_no_threat(self, rec):
        entry = rec.intel.report("PORT-ROTTERDAM", "Port of Rotterdam reports smooth operations this week.")
        assert entry["score"] == 0.0
        assert rec.intel.hub_threats() == {}


class TestRegistryIntegrity:
    def test_hub_ids_are_unique(self):
        import json
        from backend.engine.multimodal_network import load_canonical_hubs
        ids = [h["id"] for h in load_canonical_hubs()]
        assert len(ids) == len(set(ids))

    def test_elk_grove_is_its_own_facility(self, engine):
        G = engine.unified_graph
        assert G.has_node("HUB-ELKGROVE:air") and not G.has_node("HUB-CHICAGO:air")
        assert G.nodes["HUB-CHICAGO:road"]["display_name"] == "Chicago Strategic DC"
