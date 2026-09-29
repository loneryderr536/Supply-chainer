"""NLP threat scoring, CARF modal filtering and the quantile delay model."""
import pytest

from backend.engine.threat_intelligence import CARFFilter

DISASTER = "Massive hurricane destroyed the container port, vessels sunk and berths flooded."
SAFE = "Skies are blue and traffic is flowing normally."


class TestContrastiveNLP:
    def test_disaster_scores_high_in_absolute_terms(self, engine):
        # The old truth test only checked disaster > safe, which passed with the disaster zeroed out.
        assert engine.nlp.get_semantic_score(DISASTER) > 0.5

    def test_safe_text_scores_zero(self, engine):
        assert engine.nlp.get_semantic_score(SAFE) == 0.0

    def test_diluted_black_swan_is_not_averaged_away(self, engine):
        text = ("The global economy is seeing massive growth. Stock markets are hitting all-time highs. "
                "However, a massive bomb destroyed the Suez Canal. Sports teams played well today.")
        assert engine.nlp.get_semantic_score(text) > 0.5

    @pytest.mark.parametrize("text,category", [
        ("Dockworkers begin an indefinite strike, all container terminals shut.", "LABOR"),
        ("Container ship runs aground in the canal, traffic suspended in both directions.", "INFRASTRUCTURE"),
        ("Missile attacks on merchant ships force carriers to divert around the region.", "GEOPOLITICAL"),
    ])
    def test_threat_category(self, engine, text, category):
        result = engine.nlp.analyze(text)
        assert result["score"] > 0.3
        assert result["category"] == category


class TestCARF:
    carf = CARFFilter()

    def test_sea_news_is_kept_for_sea_legs(self):
        # Previously inverted: maritime news was zeroed for maritime legs.
        assert self.carf.apply_filter(0.9, "Vessel grounded in the canal, port closed.", "sea") == 0.9

    def test_sea_news_is_suppressed_for_air_legs(self):
        assert self.carf.apply_filter(0.9, "Vessel grounded in the canal, port closed.", "air") == 0.0

    @pytest.mark.parametrize("mode,text", [
        ("rail", "Freight train derailment closes the main rail line."),
        ("road", "Truckers blockade the highway, bridge closed."),
        ("air", "Airport shut, all cargo flights cancelled."),
    ])
    def test_every_mode_is_enforced(self, mode, text):
        assert self.carf.apply_filter(0.8, text, mode) == 0.8
        for other in {"sea", "air", "rail", "road"} - {mode}:
            assert self.carf.apply_filter(0.8, text, other) == 0.0, other

    def test_punctuation_and_plurals(self):
        assert self.carf.apply_filter(0.7, "Strike at ports, vessels idle.", "sea") == 0.7

    def test_vessel_traffic_is_not_road_news(self):
        assert self.carf.apply_filter(0.7, "Canal blocked, all vessel traffic halted.", "road") == 0.0

    def test_mode_agnostic_news_applies_everywhere(self):
        for mode in ("sea", "air", "rail", "road"):
            assert self.carf.apply_filter(0.6, "War breaks out across the region.", mode) == 0.6

    def test_zero_score_stays_zero(self):
        assert self.carf.apply_filter(0.0, "port closed", "sea") == 0.0


class TestQuantileModel:
    @pytest.mark.parametrize("o,d,mode", [
        ("PORT-SHANGHAI", "CHOKE-SUEZ", "sea"), ("PORT-DURBAN", "PORT-COLOMBO", "sea"),
        ("AIR-SHANGHAI", "AIR-HEATHROW", "air"), ("RAIL-CHICAGO", "RAIL-HOUSTON", "rail"),
        ("HUB-A", "HUB-B", "road"),
    ])
    @pytest.mark.parametrize("nlp", [0.0, 0.5, 1.0])
    def test_band_is_ordered_and_calibrated(self, engine, o, d, mode, nlp):
        band = engine.predictor.predict_delay_band(o, d, mode, nlp)
        profile = engine.predictor.profiles[mode]
        assert band["p50"] <= band["p85"] <= band["p95"]
        assert profile["floor"] <= band["p50"] and band["p95"] <= profile["cap"]

    def test_news_severity_raises_worst_case(self, engine):
        calm = engine.predictor.predict_delay_band("PORT-SHANGHAI", "CHOKE-SUEZ", "sea", 0.0)
        crisis = engine.predictor.predict_delay_band("PORT-SHANGHAI", "CHOKE-SUEZ", "sea", 1.0)
        assert crisis["p85"] > calm["p85"] * 3

    def test_unknown_hubs_use_mode_prior_not_first_class(self, engine):
        # The old encoder silently mapped every unknown hub to classes[0] ("Atlanta Air Hub").
        result = engine.predictor.predict_worst_case_delay("PORT-DURBAN", "PORT-COLOMBO", "sea", nlp_score=0.0)
        assert result["node_resolution"] == "mode_prior"
        assert engine.predictor._resolve_name("PORT-DURBAN", "sea") is None

    def test_shapley_attribution_is_efficient(self, engine):
        e = engine.predictor.explain_delay("CHOKE-MALACCA", "CHOKE-BABEL", "sea", 0.85)
        total = sum(e["contributions_h"].values())
        assert total == pytest.approx(e["prediction_h"] - e["base_value_h"], abs=0.1)
        assert max(e["contributions_h"], key=lambda k: abs(e["contributions_h"][k])) == "news_severity"


class TestLiveNewsGates:
    """Real headlines returned by the live scan while building this; the first three used to score 0.27-0.65."""

    @pytest.fixture
    def monitor(self, engine):
        return engine.intel

    @pytest.mark.parametrize("hub,headline", [
        ("CHOKE-MALACCA", "Is America Really China's Adversary, or Is Geography the Bigger Threat? - Egypt Today"),
        ("AIR-CHANGI", "Singapore's Changi airport is the 'best $1.5 billion investment ever made' - The Economist"),
        ("PORT-BUSAN", "Due to the shark disturbance that appeared in the northern port of Busan, whale rice - Maeil"),
        ("CHOKE-GIBRAL", "Blue plaque recognises British divers' underwater war - Gibraltar Chronicle"),
        ("AIR-SHANGHAI", "Iran airlines cancel flights as US sanctions hit - Inquirer"),
    ])
    def test_irrelevant_headlines_are_rejected(self, monitor, hub, headline):
        assert monitor._score(hub, [{"title": headline}], "LIVE_NEWS") is None

    def test_real_disruption_is_kept_and_routed_to_the_right_mode(self, monitor):
        entry = monitor._score("PORT-LOSANGELES", [{"title":
            "Closure of Los Angeles harbor bridge looms amid elevated container dwells - Journal of Commerce"}], "LIVE_NEWS")
        assert entry is not None
        assert entry["threat_by_mode"]["road"] > 0 and entry["threat_by_mode"]["sea"] == 0.0

    def test_unverified_news_counts_at_half_weight(self, monitor):
        text = "Container ship runs aground in Suez Canal, traffic suspended in both directions."
        live = monitor._score("CHOKE-SUEZ", [{"title": text}], "LIVE_NEWS")
        analyst = monitor._score("CHOKE-SUEZ", [{"title": text}], "ANALYST")
        assert live["threat_by_mode"]["sea"] == pytest.approx(analyst["threat_by_mode"]["sea"] / 2, abs=0.002)
