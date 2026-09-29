"""Cargo value, link corrections, structured feeds, FX, PDF report and storage backends."""
import os
import time

import pytest

from backend.engine.event_feeds import AISMonitor, GDACSFeed
from backend.engine.fx import FXRates
from backend.engine.multimodal_network import load_canonical_hubs, load_link_corrections
from backend.engine.store import Store
from conftest import route_hubs


class TestCargoValue:
    def test_high_value_cargo_moves_to_air_in_balanced(self, rec):
        balanced = lambda res: next(c for c in res["recommendations"] if "BALANCED" in c["personas"])
        cheap = balanced(rec.recommend("Shanghai", "Rotterdam", cargo_value_usd=0))
        precious = balanced(rec.recommend("Shanghai", "Rotterdam", cargo_value_usd=20_000_000))
        assert cheap["primary_mode"] == "SEA" and precious["primary_mode"] == "AIR"

    def test_landed_cost_adds_expected_carrying_cost(self, rec):
        for c in rec.recommend("Shanghai", "Rotterdam", cargo_value_usd=1_000_000,
                               carrying_cost_rate=0.25)["recommendations"]:
            per_hour = 1_000_000 * 0.25 / 8760
            assert c["inventory_cost"]["per_hour"] == pytest.approx(per_hour, abs=0.01)
            assert c["inventory_cost"]["p50"] == pytest.approx(per_hour * c["eta_band"]["p50"], rel=1e-3)
            assert c["landed_cost"] == pytest.approx(c["total_cost"] + c["inventory_cost"]["p50"], abs=0.02)
            assert c["explanation_facts"]["cost_basis"] == "landed"

    def test_no_value_means_freight_only(self, rec):
        c = rec.recommend("Shanghai", "Rotterdam")["recommendations"][0]
        assert c["inventory_cost"]["p50"] == 0 and c["landed_cost"] == c["total_cost"]
        assert c["explanation_facts"]["cost_basis"] == "freight"


class TestLinkCorrections:
    def test_no_corrected_link_survives(self, engine):
        G = engine.unified_graph
        for pair, mode in load_link_corrections():
            a, b = tuple(pair)
            assert not G.has_edge(f"{a}:{mode}", f"{b}:{mode}"), (a, b, mode)

    def test_red_sea_cannot_be_bypassed_by_rail(self, rec):
        for c in rec.recommend("Shanghai", "Rotterdam", scenario="RED_SEA_CONFLICT")["recommendations"]:
            assert "PORT-PORTSUDAN" not in route_hubs(c)

    def test_no_driving_across_gibraltar(self, engine):
        assert not engine.unified_graph.has_edge("PORT-ALGECIRAS:road", "PORT-TANGIER:road")


GDACS_XML = """<?xml version="1.0"?>
<rss xmlns:gdacs="http://www.gdacs.org" xmlns:georss="http://www.georss.org/georss"><channel>
<item><title>Red alert for tropical cyclone TEST</title><link>https://example.org/tc</link>
  <pubDate>Tue, 29 Sep 2026 10:00:00 GMT</pubDate><gdacs:eventtype>TC</gdacs:eventtype>
  <gdacs:alertlevel>Red</gdacs:alertlevel><gdacs:iscurrent>true</gdacs:iscurrent>
  <georss:point>31.0 122.0</georss:point></item>
<item><title>Green flood alert</title><gdacs:eventtype>FL</gdacs:eventtype>
  <gdacs:alertlevel>Green</gdacs:alertlevel><georss:point>31.2 121.5</georss:point></item>
<item><title>Orange flood alert</title><gdacs:eventtype>FL</gdacs:eventtype>
  <gdacs:alertlevel>Orange</gdacs:alertlevel><gdacs:iscurrent>false</gdacs:iscurrent>
  <georss:point>31.2 121.5</georss:point></item>
</channel></rss>"""


class TestGDACS:
    def test_parse_keeps_current_known_hazards(self):
        events = GDACSFeed.parse(GDACS_XML)
        assert [(e["type"], e["level"]) for e in events] == [("TC", "Red"), ("FL", "Green")]

    def test_red_cyclone_hits_shanghai_all_modes_green_flood_ignored(self):
        feed = GDACSFeed(load_canonical_hubs())
        picture = feed.match(GDACSFeed.parse(GDACS_XML))
        port = picture["PORT-SHANGHAI"]
        assert port["details"]["hazard"] == "Tropical cyclone" and port["category"] == "WEATHER"
        assert 0.45 <= port["threat_by_mode"]["sea"] <= 0.9
        assert "Flood" not in {e["details"]["hazard"] for e in picture.values()}
        assert "PORT-ROTTERDAM" not in picture

    def test_picture_feeds_routing(self, rec):
        feed = GDACSFeed(load_canonical_hubs())
        rec.intel.replace_source("GDACS", feed.match(GDACSFeed.parse(GDACS_XML)))
        c = rec.recommend("PORT-NINGBO", "PORT-SHANGHAI", transport_preference="sea")["recommendations"][0]
        arrival = [l for l in c["legs"] if l["type"] == "transit"][-1]  # threats are charged on arrival
        assert arrival["to"] == "PORT-SHANGHAI" and arrival["intel_source"] == "GDACS"
        assert arrival["threat_category"] == "WEATHER" and c["audit_trace"]["risk"]["live_news"] > 0.4


def _position(mmsi, lat, lon, sog, status):
    return {"MessageType": "PositionReport", "MetaData": {"MMSI": mmsi},
            "Message": {"PositionReport": {"Latitude": lat, "Longitude": lon, "Sog": sog,
                                           "NavigationalStatus": status}}}


class TestAIS:
    def hubs(self):
        return {h["id"]: h for h in load_canonical_hubs()}

    def test_disabled_without_key(self):
        assert AISMonitor(self.hubs(), api_key="").status["state"] == "disabled"

    def test_congestion_detected_from_holding_vessels(self):
        ais = AISMonitor(self.hubs(), api_key="", min_vessels=15)
        for i in range(30):  # MMSI 0 included on purpose
            ais.ingest(_position(i, 30.5, 32.4, 0.1 if i < 24 else 12.0, 1 if i < 24 else 0), now=1000.0)
        stats = ais.zone_stats(now=1000.0)["CHOKE-SUEZ"]
        assert stats == {"vessels": 30, "holding": 24, "holding_share": 0.8}
        entry = ais.picture(now=1000.0)["CHOKE-SUEZ"]
        assert entry["category"] == "CONGESTION" and entry["threat_by_mode"]["sea"] > 0.4

    def test_normal_traffic_and_stale_reports_raise_nothing(self):
        ais = AISMonitor(self.hubs(), api_key="", min_vessels=15, window_s=3600)
        for i in range(30):
            ais.ingest(_position(i, 30.5, 32.4, 11.0 if i % 4 else 0.0, 0), now=1000.0)
        assert ais.picture(now=1000.0) == {}  # 25% holding is normal anchorage
        assert ais.zone_stats(now=1000.0 + 7200)["CHOKE-SUEZ"]["vessels"] == 0  # aged out

    def test_positions_outside_zones_are_ignored(self):
        ais = AISMonitor(self.hubs(), api_key="")
        ais.ingest(_position(1, 0.0, 0.0, 0.0, 1), now=1.0)
        assert ais.messages == 0


class TestIntelMerging:
    def test_sources_merge_per_mode_and_analyst_supersedes_news(self, rec):
        intel = rec.intel
        base = {"hub_id": "PORT-ROTTERDAM", "link": None, "published": None, "observed_at": time.time()}
        intel.replace_source("GDACS", {"PORT-ROTTERDAM": {**base, "source": "GDACS", "score": 0.6, "confidence": 1.0,
                                                          "headline": "storm", "category": "WEATHER",
                                                          "threat_by_mode": {"sea": 0.2, "road": 0.6, "rail": 0.6}}})
        news = intel._score("PORT-ROTTERDAM", [{"title": "Dockworkers at the Port of Rotterdam strike, terminals shut"}],
                            "LIVE_NEWS")
        intel._picture[("PORT-ROTTERDAM", "LIVE_NEWS")] = news
        merged = intel.hub_threats()["PORT-ROTTERDAM"]
        assert merged["threat_by_mode"]["road"] == 0.6 and merged["threat_by_mode"]["sea"] == news["threat_by_mode"]["sea"]
        assert sorted(merged["sources"]) == ["GDACS", "LIVE_NEWS"]
        intel.report("PORT-ROTTERDAM", "Dockworkers at the Port of Rotterdam strike, terminals shut")
        assert {e["source"] for e in intel.entries()} == {"GDACS", "ANALYST"}


class TestFX:
    def test_offline_reference_rates_are_labelled(self):
        fx = FXRates(live=False)
        rates = fx.rates()
        assert rates["source"].startswith("approximate") and rates["rates"]["USD"] == 1.0

    def test_formatting(self):
        fx = FXRates(live=False)
        assert fx.format(1000, "USD") == "$1,000"
        assert fx.format(1000, "INR").startswith("₹")
        assert fx.format(1000, "INR", ascii_safe=True).startswith("INR ")
        assert fx.format(1000, "EUR", ascii_safe=True).startswith("€")


class TestPDF:
    def test_report_renders_in_any_currency(self, rec):
        from backend.engine.report_pdf import build_report
        resp = rec.recommend("PORT-SHANGHAI", "PORT-ROTTERDAM", transport_preference="sea",
                             scenario="SUEZ_BLOCK", cargo_value_usd=2_000_000)
        run = {"id": "test", "created_at": time.time(), "request": {"cargo_value_usd": 2_000_000}, "response": resp}
        for currency in ("USD", "INR"):
            pdf = build_report(run, currency, FXRates(live=False))
            assert pdf.startswith(b"%PDF") and len(pdf) > 10_000


def _stores():
    params = [pytest.param(("sqlite", None), id="sqlite")]
    url = os.getenv("TEST_DATABASE_URL")
    params.append(pytest.param(("postgres", url), id="postgres",
                               marks=pytest.mark.skipif(not url, reason="set TEST_DATABASE_URL to test Postgres")))
    return params


class TestStore:
    @pytest.fixture(params=_stores())
    def store(self, request, tmp_path):
        kind, url = request.param
        if kind == "sqlite":
            return Store(path=str(tmp_path / "t.db"), url="")
        s = Store(url=url)
        for table in ("alerts", "watches", "runs", "live_scenarios"):
            s._exec(f"DELETE FROM {table}")
        return s

    def test_round_trip(self, store):
        run_id = store.save_run({"source": "A", "destination": "B"}, {"recommendations": []})
        assert store.get_run(run_id)["request"]["source"] == "A"
        watch = store.add_watch(run_id, "FASTEST", "label", {}, {"x": 1})
        alert = store.add_alert(watch, "sig", "HIGH", "msg", {"k": 1})
        assert store.open_alert_exists(watch, "sig")
        assert store.acknowledge_alert(alert["id"]) and not store.open_alert_exists(watch, "sig")
        assert abs(store.list_runs()[0]["created_at"] - time.time()) < 5

    def test_live_scenarios_persist(self, store, tmp_path):
        store.set_live_scenario("SUEZ_BLOCK", True)
        store.set_live_scenario("SUEZ_BLOCK", True)  # idempotent
        store.set_live_scenario("HORMUZ_CLOSURE", True)
        store.set_live_scenario("HORMUZ_CLOSURE", False)
        reopened = Store(path=store.path, url="") if store.backend == "sqlite" else Store(url=os.getenv("TEST_DATABASE_URL"))
        assert reopened.load_live_scenarios() == ["SUEZ_BLOCK"]
