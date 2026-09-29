from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List
import asyncio
import csv
import io
import json
import os
from contextlib import asynccontextmanager

from fastapi.middleware.cors import CORSMiddleware
from .engine.threat_intelligence import ThreatIntelligencePredictor, ContrastiveNLPEngine, CARFFilter
from .engine.multimodal_network import create_multimodal_network, load_canonical_hubs
from .engine.route_recommender import RouteRecommender
from .engine.scenario_manager import ScenarioManager
from .engine.supplier_scorer import SupplierScorer
from .engine.news_ingestion import IntelMonitor
from .engine.store import Store
from .engine.monitoring import RouteMonitor, shipment_plan, audit_rows, CSV_COLUMNS

# The US-only simulator pipeline (graph_model / simulator / baseline / weather_integration) was
# instantiated here but never used by any endpoint; it is no longer loaded at startup.

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"
LIVE_INTEL = os.getenv("LIVE_INTEL", "true").lower() == "true"
INTEL_REFRESH_S = int(os.getenv("INTEL_REFRESH_S", "900"))

predictor = ThreatIntelligencePredictor(lazy_load=True)
canonical_hubs = load_canonical_hubs()
hub_index = {h["id"]: h for h in canonical_hubs}
multimodal_net = create_multimodal_network()
scenario_mgr = ScenarioManager()
intel = IntelMonitor(canonical_hubs, ContrastiveNLPEngine(lazy_load=True), CARFFilter())
recommender = RouteRecommender(multimodal_net, predictor, None, scenario_mgr, demo_mode=DEMO_MODE, intel=intel)
supplier_scorer = SupplierScorer(os.path.join(os.path.dirname(__file__), 'data', 'suppliers.json'))
store = Store()


class AlertHub:
    """Fan-out of alerts to every connected dashboard WebSocket."""
    def __init__(self):
        self.clients: set = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def publish(self, alert: dict):
        if self.loop is None:
            return
        msg = json.dumps({"type": "alert", "alert": alert})
        for ws in list(self.clients):
            asyncio.run_coroutine_threadsafe(self._send(ws, msg), self.loop)

    async def _send(self, ws, msg):
        try:
            await ws.send_text(msg)
        except Exception:
            self.clients.discard(ws)


alert_hub = AlertHub()
monitor = RouteMonitor(store, recommender, on_alert=alert_hub.publish)


async def _intel_loop():
    while True:
        await asyncio.sleep(INTEL_REFRESH_S)
        await asyncio.to_thread(_scan_and_evaluate)


def _scan_and_evaluate():
    result = intel.scan()
    if result.get("threats"):
        monitor.evaluate_all("live_news_scan")
    return result


def _startup_warmup():
    recommender.run_background_warmup()
    if LIVE_INTEL and not DEMO_MODE:
        _scan_and_evaluate()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Supplychainer Engine Active: Canonical Global Registry Loaded.")
    alert_hub.loop = asyncio.get_running_loop()
    tasks = []
    if not DEMO_MODE:
        tasks.append(asyncio.create_task(asyncio.to_thread(_startup_warmup)))
        if LIVE_INTEL:
            tasks.append(asyncio.create_task(_intel_loop()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Smart Supply Chain API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RecommendRequest(BaseModel):
    source: str  # Canonical Hub ID, alias or City Name
    destination: str
    cargo_type: str = "general"  # general | perishable_urgent | hazardous_waste | oversize_heavy
    priority: str = "normal"  # low | normal | urgent
    budget_sensitivity: str = "medium"
    transport_preference: str = "any"  # sea, air, rail, road, any
    routing_policy: str = "STRICT"  # STRICT or PREFERRED
    scenario: Optional[str] = None  # what-if scenario for this request only
    scenarios: Optional[List[str]] = None
    overrides: Optional[dict] = None
    save: bool = True


class SourcingRequest(BaseModel):
    category: str = "Electronics"
    current_inventory: int = 1000
    safety_stock: int = 1500
    demand_forecast: int = 800
    scenario: Optional[str] = None


class WatchRequest(BaseModel):
    run_id: str
    persona: str
    label: Optional[str] = None


class IntelReport(BaseModel):
    hub_id: str
    text: str


# ------------------------------------------------------------------ registry
@app.get("/api/scenarios")
def get_scenarios():
    """Returns available disruption scenarios (with their live activation state)."""
    return scenario_mgr.get_all_scenarios()


@app.get("/api/hubs")
def get_hubs():
    """Returns the full canonical hub registry."""
    return canonical_hubs


@app.get("/api/hubs/search")
def search_hubs(q: str = Query(..., min_length=1)):
    """Search hubs by display_name, aliases, or country."""
    q = q.lower()
    results = []
    for hub in canonical_hubs:
        if (q in hub["display_name"].lower() or
                any(q in a.lower() for a in hub["aliases"]) or
                q in hub["country"].lower() or
                q in hub["id"].lower()):
            results.append(hub)
    return results


@app.get("/api/network")
def get_network():
    """Physical hubs with coordinates and one edge per (hub pair, mode), for the map."""
    nodes = [{"id": h["id"], "display_name": h["display_name"], "type": h["type"], "modes": h["modes"],
              "lat": h["lat"], "lon": h["lon"], "country": h["country"], "importance": h.get("importance", 5)}
             for h in canonical_hubs]
    edges, seen = [], set()
    for u, v, data in multimodal_net.edges(data=True):
        if data["type"] == "transfer":
            continue
        a, b = multimodal_net.nodes[u]["physical_id"], multimodal_net.nodes[v]["physical_id"]
        key = (min(a, b), max(a, b), data["transport_mode"])
        if key in seen:
            continue
        seen.add(key)
        edges.append({"source": key[0], "target": key[1], "mode": data["transport_mode"],
                      "baseline_time": round(data["baseline_time"], 1), "distance_km": data.get("distance")})
    return {"nodes": nodes, "edges": edges}


@app.get("/api/status")
def get_status():
    return {
        **recommender.engine_status(),
        "is_supplychainer": True,
        "geo_scope": "Global (Canonical)",
        "hub_count": len(canonical_hubs),
        "active_scenarios": scenario_mgr.active_scenario_ids,
        "monitored_routes": len(store.list_watches()),
        "open_alerts": len(store.list_alerts()),
        "intel": {"threat_hubs": len(intel.hub_threats()), "last_scan": intel.last_scan},
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    alert_hub.clients.add(websocket)
    try:
        while True:
            if recommender.warmup_failed:
                status_msg = "WARM-UP FAILED"
            elif not recommender.is_warmed_up:
                status_msg = "WARMING RISK ENGINE"
            elif recommender.warmup_notes:
                status_msg = "DEGRADED"
            else:
                status_msg = "FULLY OPERATIONAL"
            await websocket.send_text(json.dumps({
                "type": "status",
                "engine_status": status_msg,
                "notes": recommender.warmup_notes,
                "ml_trained": predictor.is_trained,
                "nlp_ready": recommender.nlp.ready,
                "active_scenarios": scenario_mgr.active_scenario_ids,
                "intel_threat_hubs": len(intel.hub_threats()),
                "open_alerts": len(store.list_alerts()),
                "hub_registry": "Synchronized",
            }))
            await asyncio.sleep(2.0)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:
        print(f"WebSocket closed: {e}")
    finally:
        alert_hub.clients.discard(websocket)


@app.get("/api/cities")
def get_cities():
    """Returns the city-to-hub mapping for multimodal resolution."""
    path = os.path.join(os.path.dirname(__file__), 'data', 'canonical_locations.json')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return {}


# ------------------------------------------------------------------- routing
@app.post("/api/recommend")
def recommend_routes(req: RecommendRequest):
    result = recommender.recommend(
        source=req.source,
        destination=req.destination,
        cargo_type=req.cargo_type,
        priority=req.priority,
        transport_preference=req.transport_preference,
        routing_policy=req.routing_policy,
        scenario=req.scenario,
        scenarios=req.scenarios,
        overrides=req.overrides,
    )
    if req.save and "recommendations" in result:
        result["run_id"] = store.save_run(req.model_dump(exclude={"save"}), result)
    return result


@app.get("/api/history")
def get_history(limit: int = Query(50, ge=1, le=500)):
    return store.list_runs(limit)


def _run_or_404(run_id: str):
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, f"run {run_id} not found")
    return run


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return _run_or_404(run_id)


@app.get("/api/runs/{run_id}/export.csv")
def export_run_csv(run_id: str):
    run = _run_or_404(run_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    w.writerows(audit_rows(run))
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="supplychainer_{run_id}.csv"'})


@app.get("/api/runs/{run_id}/export.json")
def export_run_json(run_id: str, persona: Optional[str] = None):
    """TMS/ERP shipment-plan payload (schema supplychainer.shipment_plan.v1)."""
    return shipment_plan(_run_or_404(run_id), persona)


# ---------------------------------------------------------- live picture / alerts
@app.post("/api/scenarios/{scenario_id}/activate")
async def activate_scenario(scenario_id: str):
    if not scenario_mgr.activate(scenario_id):
        raise HTTPException(404, f"unknown scenario {scenario_id}")
    alerts = await asyncio.to_thread(monitor.evaluate_all, f"scenario_activated:{scenario_id}")
    return {"active_scenarios": scenario_mgr.active_scenario_ids, "alerts_raised": alerts}


@app.post("/api/scenarios/{scenario_id}/deactivate")
def deactivate_scenario(scenario_id: str):
    scenario_mgr.deactivate(scenario_id)
    return {"active_scenarios": scenario_mgr.active_scenario_ids}


@app.post("/api/watches")
def add_watch(req: WatchRequest):
    try:
        return monitor.watch(req.run_id, req.persona, req.label)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/api/watches")
def list_watches():
    return [{**w, "route": {k: w["route"][k] for k in ("persona", "primary_mode", "chokepoints", "adjusted_eta",
                                                        "eta_band", "total_cost", "threat_level")}}
            for w in store.list_watches()]


@app.delete("/api/watches/{watch_id}")
def remove_watch(watch_id: str):
    if not store.deactivate_watch(watch_id):
        raise HTTPException(404, "watch not found")
    return {"ok": True}


@app.get("/api/alerts")
def list_alerts(include_acknowledged: bool = False):
    return store.list_alerts(include_acknowledged)


@app.post("/api/alerts/{alert_id}/ack")
def ack_alert(alert_id: str):
    if not store.acknowledge_alert(alert_id):
        raise HTTPException(404, "alert not found")
    return {"ok": True}


@app.get("/api/intel")
def get_intel():
    return {"threats": list(intel.hub_threats().values()), "last_scan": intel.last_scan,
            "watchlist_size": len(intel.watchlist), "nlp_ready": intel.nlp.ready}


@app.post("/api/intel/report")
async def report_intel(req: IntelReport):
    """Analyst / structured-feed report about a hub; scored by NLP + CARF and fed into routing."""
    if req.hub_id not in hub_index:
        raise HTTPException(404, f"unknown hub {req.hub_id}")
    try:
        entry = await asyncio.to_thread(intel.report, req.hub_id, req.text)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    alerts = await asyncio.to_thread(monitor.evaluate_all, f"intel_report:{req.hub_id}") if entry.get("score") else []
    return {"intel": entry, "alerts_raised": alerts}


@app.delete("/api/intel/{hub_id}")
def clear_intel(hub_id: str):
    intel.clear(hub_id)
    return {"ok": True}


@app.post("/api/intel/scan")
async def scan_intel():
    return await asyncio.to_thread(_scan_and_evaluate)


# ------------------------------------------------------------------ suppliers
@app.post("/api/suppliers")
def get_suppliers(req: SourcingRequest):
    # Request-scoped: the old code activated the scenario globally, leaking it into routing.
    active_disruptions = scenario_mgr.disruptions_for(
        list(dict.fromkeys(([req.scenario] if req.scenario else []) + scenario_mgr.active_scenario_ids)))

    ranked_suppliers = supplier_scorer.get_ranked_suppliers(req.category, active_disruptions)
    advice = supplier_scorer.get_procurement_advice(req.current_inventory, req.safety_stock, req.demand_forecast)

    return {
        "suppliers": ranked_suppliers,
        "advice": advice,
        "active_disruptions": active_disruptions
    }
