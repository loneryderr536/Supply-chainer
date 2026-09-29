import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture(scope="session")
def engine():
    """A fully warmed engine (real quantile models + NLP anchors), shared across tests."""
    from backend.engine.multimodal_network import create_multimodal_network, load_canonical_hubs
    from backend.engine.news_ingestion import IntelMonitor
    from backend.engine.route_recommender import RouteRecommender
    from backend.engine.scenario_manager import ScenarioManager
    from backend.engine.threat_intelligence import ThreatIntelligencePredictor, ContrastiveNLPEngine, CARFFilter

    intel = IntelMonitor(load_canonical_hubs(), ContrastiveNLPEngine(lazy_load=True), CARFFilter())
    rec = RouteRecommender(create_multimodal_network(), ThreatIntelligencePredictor(lazy_load=True), None,
                           ScenarioManager(), intel=intel)
    rec.run_background_warmup()
    assert rec.predictor.is_trained and rec.nlp.ready, rec.warmup_notes
    return rec


@pytest.fixture
def rec(engine):
    """Per-test view of the engine with the live picture reset before and after."""
    engine.scenario_mgr.active_scenario_ids.clear()
    engine.intel.clear()
    yield engine
    engine.scenario_mgr.active_scenario_ids.clear()
    engine.intel.clear()


def route_hubs(candidate):
    return [leg["to"] for leg in candidate["legs"]]
