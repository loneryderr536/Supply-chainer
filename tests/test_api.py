"""HTTP API: history, exports, monitoring and alerts, end to end."""
import csv
import io
import os
import time

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["SUPPLYCHAINER_DB"] = str(tmp_path_factory.mktemp("db") / "test.db")
    os.environ["LIVE_INTEL"] = "false"  # no network in tests
    from fastapi.testclient import TestClient
    from backend import main

    with TestClient(main.app) as c:
        deadline = time.time() + 180
        while not main.recommender.is_warmed_up and time.time() < deadline:
            time.sleep(0.5)
        assert main.recommender.is_warmed_up
        yield c, main


@pytest.fixture(autouse=True)
def reset(client):
    _, main = client
    main.scenario_mgr.active_scenario_ids.clear()
    main.intel.clear()
    yield


def recommend(c, **kw):
    body = {"source": "PORT-SHANGHAI", "destination": "PORT-ROTTERDAM", "transport_preference": "sea", **kw}
    r = c.post("/api/recommend", json=body)
    assert r.status_code == 200
    return r.json()


def test_status_reports_real_component_state(client):
    c, _ = client
    s = c.get("/api/status").json()
    assert s["ml_trained"] and s["nlp_ready"]
    assert s["quantile_models"] == ["p50", "p85", "p95"]


def test_network_has_coordinates_for_the_map(client):
    c, _ = client
    net = c.get("/api/network").json()
    suez = next(n for n in net["nodes"] if n["id"] == "CHOKE-SUEZ")
    assert suez["lat"] == pytest.approx(30.59, abs=0.1)
    assert any(e["mode"] == "sea" and "CHOKE-CAPEGOOD" in (e["source"], e["target"]) for e in net["edges"])


def test_history_and_exports(client):
    c, _ = client
    res = recommend(c, scenario="SUEZ_BLOCK")
    run_id = res["run_id"]
    assert any(h["id"] == run_id for h in c.get("/api/history").json())

    r = c.get(f"/api/runs/{run_id}/export.csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert rows and {"option", "mode", "eta_h", "delay_p85_h", "intel_source"} <= set(rows[0])
    assert all(row["to"] != "CHOKE-SUEZ" for row in rows)
    totals = [row for row in rows if row["sequence"] == "TOTAL"]
    assert len(totals) == len(res["recommendations"])

    plan = c.get(f"/api/runs/{run_id}/export.json").json()
    assert plan["schema"] == "supplychainer.shipment_plan.v1"
    moves = plan["plans"][0]["movements"]
    assert moves[0]["planned_start_offset_h"] == 0
    assert moves[1]["planned_start_offset_h"] == pytest.approx(moves[0]["planned_duration_h"], abs=0.1)


def test_scenario_activation_alerts_monitored_route(client):
    c, main = client
    received = []
    main.monitor.on_alert = received.append
    try:
        res = recommend(c)
        persona = res["recommendations"][0]["persona"]
        watch = c.post("/api/watches", json={"run_id": res["run_id"], "persona": persona, "label": "SH-RTM weekly"}).json()
        assert watch["route"]["chokepoints"][2] == "CHOKE-SUEZ"

        out = c.post("/api/scenarios/SUEZ_BLOCK/activate").json()
        assert out["active_scenarios"] == ["SUEZ_BLOCK"]
        alerts = [a for a in out["alerts_raised"] if a["watch_id"] == watch["id"]]
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert["severity"] == "CRITICAL"
        assert alert["payload"]["blocked_at"] == ["CHOKE-SUEZ"]
        assert "CHOKE-CAPEGOOD" in alert["payload"]["alternative"]["chokepoints"]
        assert received and received[-1]["id"] == alert["id"]

        # Re-activating the same picture must not spam a duplicate alert.
        again = c.post("/api/scenarios/SUEZ_BLOCK/activate").json()
        assert not [a for a in again["alerts_raised"] if a["watch_id"] == watch["id"]]

        assert c.post(f"/api/alerts/{alert['id']}/ack").json()["ok"]
        assert all(a["id"] != alert["id"] for a in c.get("/api/alerts").json())
    finally:
        main.monitor.on_alert = main.alert_hub.publish
        c.delete(f"/api/watches/{watch['id']}")


def test_unrelated_scenario_does_not_alert(client):
    c, _ = client
    res = recommend(c)
    watch = c.post("/api/watches", json={"run_id": res["run_id"], "persona": res["recommendations"][0]["persona"]}).json()
    out = c.post("/api/scenarios/DUBAI_AIR_CONGESTION/activate").json()
    assert not [a for a in out["alerts_raised"] if a["watch_id"] == watch["id"]]
    c.delete(f"/api/watches/{watch['id']}")


def test_intel_report_endpoint_feeds_routing(client):
    c, _ = client
    out = c.post("/api/intel/report", json={"hub_id": "CHOKE-SUEZ",
                                            "text": "Container ship runs aground in Suez Canal, traffic suspended."}).json()
    assert out["intel"]["threat_by_mode"]["sea"] > 0.8
    res = recommend(c)
    suez_legs = [l for r in res["recommendations"] for l in r["legs"] if l["to"] == "CHOKE-SUEZ"]
    assert all(l["intel_source"] == "LIVE_NEWS" for l in suez_legs)
    assert c.get("/api/intel").json()["threats"][0]["hub_id"] == "CHOKE-SUEZ"


def test_supplier_what_if_does_not_leak_into_routing(client):
    c, main = client
    c.post("/api/suppliers", json={"category": "Electronics", "scenario": "SUEZ_BLOCK"})
    assert main.scenario_mgr.active_scenario_ids == []
    assert recommend(c)["applied_scenarios"] == []
