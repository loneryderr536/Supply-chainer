# Supplychainer: Context-Aware Agentic Routing Engine

> **An NLP-driven Risk Assessment API & Executive Command Dashboard for Dynamic Supply Chain Graph Routing.**

Traditional supply chain routing algorithms (like Dijkstra or A*) rely on static distances. But in the real world, supply chains are disrupted by dynamic **Black Swan events**—hurricanes, worker strikes, and geopolitical blockades. 

**Supplychainer** is a dual-component platform:
1. **Agentic AI Backend**: Intercepts route requests, reads live global news along the path, applies logical context filters, and mathematically calculates the **85th-percentile worst-case delay**.
2. **Executive Command Dashboard**: A high-performance, multimodal React frontend to visualize risks, trigger live simulations (like a Suez blockage), and perform comparative intelligence auditing.

---

##  Key Features

* **Real-time Threat Intelligence**: Monitors global RSS feeds to detect local disruptions before they trap inventory.
* **Context-Aware Relevance Filter (CARF)**: Eliminates false positives (e.g., ignoring a seaport strike if the transport mode is Rail).
* **Quantile ML Risk Assessment**: Gradient Boosting quantile regressors (p50/p85/p95) trained on 50,000 synthetic leg delays drawn from distributions fitted to published UNCTAD / World Bank / STB medians and p90s, with historical incidents (Suez 2021, Red Sea 2024, …) injected as outlier clusters. See `Code/real_dataset_builder.py`.
* **Executive Dashboard**: A visually stunning 3-column command interface featuring real-time tradeoff strips, operational configuration drop-downs, and forensic audit trails.

---

##  The Architecture Pipeline

Our system decouples sensory data from mathematical risk using a 4-stage pipeline:

1. **The Targeted Fetch:** The routing algorithm requests a path (e.g., Shanghai to Rotterdam). The API evaluates the Origin, Destination, and dynamic Choke Points.
2. **The Sensory Brain (Contrastive NLP):** We utilize a `SentenceTransformer` (`all-MiniLM-L6-v2`) with **Contrastive Semantic Anchoring**. It reads live news texts, splits them via semantic chunking, and calculates a pure "Threat Margin" against a multi-domain matrix of Disasters vs. Safe baseline scenarios.
3. **The Logic Gate - CARF System:** The **CARF** prevents hallucinated delays. If the news reports a *sinking ship*, but the transport mode is an *EV Delivery Van*, CARF zeroes out the threat. It ensures spatial and modal relevance.
4. **The Decision Brain - Quantile ML:** The context-filtered NLP score, combined with tabular operational data, is fed into a **Gradient Boosting Regressor**. We use a `Quantile Loss` function (alpha=0.85) to predict the worst-case scenario buffer.

---

##  Project Structure

```
Smart_Supply_Chain/
├── backend/
│   ├── main.py                  # FastAPI app + all HTTP/WebSocket routes (the real entry point)
│   ├── requirements.txt
│   ├── data/
│   │   ├── canonical_hubs.json       # 440 real-world ports/airports/rail yards/road hubs
│   │   ├── canonical_locations.json  # city -> per-mode hub lookup
│   │   └── suppliers.json            # sample supplier records for Supplier Intelligence
│   └── engine/
│       ├── multimodal_network.py     # builds the routable graph from canonical_hubs.json
│       ├── route_recommender.py      # Dijkstra routing + persona weighting (the core solver)
│       ├── threat_intelligence.py    # NLP threat scoring + CARF filter + ML quantile predictor
│       ├── news_ingestion.py         # live RSS pull with an offline fallback per mode
│       ├── scenario_manager.py       # the 6 scripted disruption scenarios
│       ├── supplier_scorer.py        # supplier ranking + procurement advice
│       ├── node_resolver.py          # resolves a city/hub name to a graph entry node
│       ├── sea_lanes.py              # ocean basins + chokepoint gates for maritime edges
│       ├── store.py                  # SQLite: route history, watches, alerts
│       ├── monitoring.py             # watch re-evaluation, alerts, CSV/TMS exports
│       └── (baseline.py, graph_model.py, simulator.py, optimizer.py, evaluator.py,
│            benchmark_runner.py, or_baseline.py, weather_integration.py, live_routing.py)
│            — an earlier, US-only prototype pipeline. Not used by the live app — see below.
├── Execution/
│   ├── risk_model.pkl             # trained Gradient Boosting quantile regressor (p85)
│   ├── risk_model_p50.pkl / risk_model_p95.pkl  # band siblings (Code/train_quantile_band.py)
│   ├── quantile_band_meta.json    # hold-out coverage + Shapley background sample
│   ├── label_encoders.pkl         # categorical encoders for the model above
│   ├── nlp_anchors.pt             # precomputed disaster/safe embedding anchors
│   ├── calibration_profiles.json  # per-mode floor/cap delay calibration
│   └── api.py                     # an older, standalone prototype API — not used by main.py
└── frontend/
    ├── package.json, vite.config.js
    └── src/
        ├── App.jsx                   # top-level view switcher
        ├── RouteRecommender.jsx      # main routing dashboard (calls /api/recommend)
        ├── SupplierIntelligence.jsx  # supplier ranking dashboard (calls /api/suppliers)
        └── BenchmarkCharts.jsx       # static, hardcoded benchmark charts — not live-wired
```

**Two things that look like the main entry point but aren't, so you don't lose time in the wrong file:**
- `Execution/api.py` is an older, standalone prototype with its own `/predict_route_risk`
  endpoint. The real, live API is `backend/main.py`.
- The following files under `backend/engine/` belong to an earlier, US-only prototype and are
  **not** wired into `main.py`'s actual request path: `baseline.py`, `graph_model.py`,
  `simulator.py`, `optimizer.py`, `evaluator.py`, `benchmark_runner.py`, `or_baseline.py`,
  `weather_integration.py`, `live_routing.py`. The live system is the `RouteRecommender` /
  `ThreatIntelligencePredictor` / `multimodal_network` pipeline described above — that's where
  your time is best spent.

---

##  Decision Superiority Benchmarks

Supplychainer shifts logistics from geometric shortest paths to optimal business decisions:
* **Suez Canal Failure**: Reroutes automatically via Cape of Good Hope, avoiding infinite delay backlogs.
* **Persona trade-offs**: FASTEST, BALANCED and SAFEST price delay at p50, p85 and p95 respectively, so they diverge when uncertainty rises (e.g. under `RED_SEA_CONFLICT` FASTEST keeps Suez while SAFEST/BALANCED take the Cape). Cargo value / inventory carrying cost is not modelled yet.
* **Latency**: ~25-130 ms per `/api/recommend` on a laptop (three personas over 931 virtual nodes); the first request that needs a Shapley attribution on a mode-prior leg takes up to ~0.5 s, then it is cached.

---

##  How to Run Locally

### 1. Backend Service (FastAPI)

Run everything from the **project root** (this folder, `Smart_Supply_Chain/`) — not from inside
`backend/`. `backend/main.py` uses relative imports (`from .engine...`), which only resolve
when it's imported as part of the `backend` package, and its model-loading code references
`./Execution/risk_model.pkl` relative to the working directory you launch from. Both of those
require the project root as your working directory.

The ML model (`risk_model.pkl`) and categorical encoders (`label_encoders.pkl`) are pre-trained and included in `Execution/`. You **do not** need the proprietary CSV dataset to run the API.

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
```
*The first boot may take 10-20 seconds to load the HuggingFace transformer weights into memory. API Docs available at `http://127.0.0.1:8000/docs`.*

### 2. Executive Frontend (React/Vite)

```bash
cd frontend
npm install
npm run dev
```
*Access the dashboard at `http://localhost:5173` (or the port specified by Vite).*

---

##  Tests

```bash
pip install -r requirements.txt
pytest            # ~75 tests: NLP/CARF, quantile model, graph topology, every scenario, API, alerts
```
The tests assert numbers, not just "no crash": e.g. under `SUEZ_BLOCK` no option may touch
`CHOKE-SUEZ`, the hold option must contain exactly the 240h salvage window, and a scenario delay
must be counted once per hub even when the route transfers modes inside it.

Environment switches: `LIVE_INTEL=false` disables the Google News scan, `SUPPLYCHAINER_DB` sets
the SQLite path (default `backend/data/supplychainer.db`), `ALERT_WEBHOOK_URL` receives every
route alert as JSON.

##  API Usage Example

**Endpoint:** `POST /api/recommend`

```json
{
  "source": "Shanghai",
  "destination": "Rotterdam",
  "transport_preference": "sea",
  "routing_policy": "STRICT",
  "cargo_type": "general",
  "priority": "normal",
  "scenario": "SUEZ_BLOCK"
}
```

**Response** (abbreviated; real output with `LIVE_INTEL=false`, since live headlines change the numbers):
```json
{
  "applied_scenarios": ["SUEZ_BLOCK"],
  "closed_hubs": ["CHOKE-SUEZ"],
  "hold_option": { "verdict": "REROUTE", "delta_vs_best_reroute_h": 211.4,
                   "note": "Waiting for CHOKE-SUEZ to reopen is 211.4h slower (p50) than the best reroute..." },
  "recommendations": [{
    "personas": ["FASTEST", "SAFEST", "BALANCED"],
    "chokepoints": ["CHOKE-MALACCA", "CHOKE-CAPEGOOD"],
    "adjusted_eta": 659.3,
    "eta_band": { "p50": 721.1, "p85": 733.3, "p95": 749.1, "p95_correlated": 769.6 },
    "total_cost": 3461.07,
    "audit_trace": { "ml": { "buffer_h": { "p50": 61.8, "p85": 82.9, "p95": 110.3 },
                             "dominant_leg": { "attribution": { "method": "exact_shapley_interventional", "...": "..." } } } }
  }],
  "run_id": "1133cb7cefa0"
}
```

| Endpoint | Purpose |
|---|---|
| `POST /api/recommend` | Route options (saved to history, returns `run_id`) |
| `GET /api/history`, `GET /api/runs/{id}` | Route history |
| `GET /api/runs/{id}/export.csv` | Leg-level audit trail |
| `GET /api/runs/{id}/export.json` | TMS/ERP shipment plan (`supplychainer.shipment_plan.v1`) |
| `POST /api/scenarios/{id}/activate` / `deactivate` | Change the live disruption picture; re-checks monitored routes |
| `POST /api/watches`, `GET /api/watches`, `DELETE /api/watches/{id}` | Monitor a route |
| `GET /api/alerts`, `POST /api/alerts/{id}/ack` | Alerts (also pushed over `/ws` and to `ALERT_WEBHOOK_URL`) |
| `GET /api/intel`, `POST /api/intel/scan`, `POST /api/intel/report`, `DELETE /api/intel/{hub}` | Live news / analyst intelligence |
| `GET /api/network` | Hubs with coordinates + edges per mode (drives the map) |

---

##  Prelim Changes: What Was Broken

Each of these ran without an error and produced a wrong number or decision.

| Area | Symptom | Fix |
|---|---|---|
| NLP threshold | `margin >= noise_floor` returned 0.0, so real disasters scored 0 | Threat only when margin exceeds the floor |
| NLP calibration | Multiplier 0.35 (research engine: 3.5); a clear disaster maxed out near 0.1 | Restored 3.5, per-sentence margins so one alarming headline is not diluted |
| NLP on CPU | Anchors saved from CUDA; `torch.load` failed on CPU hosts, warm-up still reported "complete" | `map_location="cpu"`, anchors re-saved on CPU, warm-up reports degraded state honestly |
| CARF | Inverted: maritime news was zeroed for *sea* legs; rail/road never filtered; `"port,"` didn't match | Keep own-mode and mode-agnostic news, suppress other-mode news, all four modes, regex tokens |
| Quantile model | Loaded, never called in routing; unknown hubs silently encoded as "Atlanta Air Hub"; `Leg_Type` always `Global_Freight` | Per-leg p50/p85/p95 in every route, mode prior for untrained hubs, training-consistent leg type |
| Sea graph | 260 of 342 sea links one-way; Cape/Hormuz/Bab-el-Mandeb/Malacca/Gibraltar had no inbound edge; LA had no sea link | Links are two-way; ports are assigned to ocean basins and cross-basin voyages must pass the real chokepoints |
| `SUEZ_BLOCK` | Every persona still sailed through the blocked canal (ETA 649.9h) | Closures are impassable; Cape reroute plus an explicit hold-vs-reroute comparison |
| Red Sea / Hormuz / LA scenarios | No route could reach those hubs, so the scenarios changed nothing | Now reroute or re-price (see tests) |
| Scenario accounting | Delay re-added on every virtual node of a hub (e.g. road → sea transfer); risk surcharge in trace but not in total | Once per hub visit; surcharge included in `total_cost` |
| Global state | `recommend()` and `/api/suppliers` activated scenarios globally, so concurrent users leaked what-ifs into each other | What-ifs are request-scoped; a separate, explicit live picture |
| Node resolver | Always entered through the road hub, ignoring the requested mode | Mode-aware entry; a hub ID may start on any of its modes |
| Request options | `PREFERRED`, `cargo_type`, `priority` accepted but ignored | Soft 1.5× bias, cargo restrictions, priority-weighted time |
| Explanations | "reduces cost by 396%" = `cost × 0.15`; "risk reduced by 95%" = `1 - threat` | Built only from numbers in the candidate set |
| Registry | Two facilities shared `HUB-CHICAGO` (silently merged); `AIR-CHENNAI` referenced 30× but missing | Split into `HUB-ELKGROVE`; added Chennai airport; builder rejects duplicate IDs |
| Live news | Ingestor never called; `socket.setdefaulttimeout` changed the timeout process-wide | Wired in with per-request timeouts (see below) |
| Tests | `truth_tests.py` passed with the disaster zeroed (`0.0 > -0.05`); `test_v6.py` never checked Suez was avoided | Absolute checks + the pytest suite |

##  Prelim Changes: What Was Built

* **Quantile band + explainability.** p50/p95 siblings of the production p85 model are trained on the
  same seeded dataset (`Code/train_quantile_band.py`; the retrained p85 matches the shipped one to
  0.36h mean, hold-out coverage 0.51/0.85/0.95). FASTEST plans on p50, BALANCED on p85, SAFEST on p95
  plus risk-hours. The worst leg of each route gets an exact Shapley attribution (all coalitions,
  no extra dependency). Route bands assume independent legs; `p95_correlated` is the upper bound.
* **Live intelligence that actually feeds routing.** Chokepoints and major hubs are scanned on Google News.
  A headline must name the hub and describe a disruption event before NLP scores it; the hub's name is
  masked before scoring because the safe anchors mention real ports. Unverified news counts at 50%.
  Analysts can file or confirm reports (`/api/intel/report`). Threat type (LABOR, GEOPOLITICAL, WEATHER…)
  comes from the nearest historical anchor.
* **Interactive map.** Leaflet with a bundled coastline (works offline), mode-coloured legs, closed/disrupted/intel hubs.
* **Route monitoring and alerts.** Monitor any option; activating a scenario or new intel re-prices it and raises
  CRITICAL/HIGH/MEDIUM alerts with a concrete alternative. Alerts are pushed over the WebSocket and to a webhook.
* **Persistence and exports.** SQLite history, watches and alerts survive restarts; CSV audit trail and a
  TMS/ERP shipment-plan JSON with per-movement offsets, delay buffers and risk provenance.

**Known limits.** The quantile model was trained on 16 hubs, so most legs use the mode prior (reported per
route). Aviation headlines still score weakly: one safe anchor is an air-cargo sentence close to most
aviation news. Sea distances are great-circle between waypoints. Live scenario activations are in memory
(history, watches and alerts are persisted). `BenchmarkCharts.jsx` and `benchmarks/decision_superiority.py`
are pre-existing and not wired to the current engine.

---

## 💻 Tech Stack

* **Frontend**: React, Vite, Vanilla CSS (Executive Dark-Mode Aesthetic)
* **Backend**: FastAPI, Python, Uvicorn
* **Machine Learning**: Scikit-Learn (Gradient Boosting with Quantile Loss)
* **NLP**: HuggingFace Sentence-Transformers
* **Data Ops**: Pandas, NumPy, NetworkX

---

##  TatHack Prelim Challenge

This repository is your starting point. There are two things to work on, and you're free to
lean into either or both:

**1. Fix what's broken.** The codebase has a handful of intentionally introduced issues. None
of them crash the app or throw a visible error — they're logic bugs that quietly produce the
wrong number or the wrong decision while everything still "runs fine." Don't trust that a
feature works just because it doesn't error out: test it against a real scenario (for example,
activate the `SUEZ_BLOCK` scenario in the dashboard and check whether the reported threat and
delay actually reflect it) and check the numbers, not just the absence of a crash.

**2. Build what's missing.** Pick one or more ideas from the list below — or bring your own —
and extend the platform. We're not scoring on how many features you bolt on; we're scoring on
whether what you build is genuinely useful, correctly wired end-to-end (not just a UI mockup),
and whether you can explain the trade-offs you made.

---

##  Where You Can Take This

Supplychainer is a working prototype, not a finished product. Here's where the biggest
opportunities are if you want to push it toward something a real logistics team could rely on
— pick what's interesting, you don't need to attempt all of it:

### Smarter AI/ML
- **Wire the trained ML model into live routing.** `ThreatIntelligencePredictor.predict_worst_case_delay()`
  — the p85 quantile model — is loaded and warmed up at startup but never actually called by
  `RouteRecommender.recommend()` today. Only the NLP+CARF semantic score currently feeds route
  weighting. Connecting the model's real delay prediction into the routing decision is one of
  the most meaningful upgrades available in this codebase.
- Predict multiple quantiles (p50 / p85 / p95) instead of a single point estimate, for a
  confidence band instead of one number.
- Add real explainability (e.g. SHAP or permutation importance) to the model's predictions,
  surfaced through the existing `audit_trace`.
- Generalize `CARFFilter` to rail and road with the same rigor it already applies to air/sea —
  it defines relevance keywords for all four modes but only enforces two of them today.
- Categorize threat *type* (strike / weather / geopolitical / infrastructure), not just
  magnitude, so downstream logic can react differently to different kinds of disruption.

### Product & Experience
- Replace the placeholder map text in `App.jsx`'s default view with a real interactive map
  (Leaflet/Mapbox) driven by the existing `/api/network` endpoint.
- Route history — persist and compare past recommendations instead of losing them on refresh.
- Export a route's full audit trail as PDF/CSV for a "boardroom-ready" report.
- Real-time alerts when a newly activated scenario affects a route you've already generated.
- A mobile-responsive layout — the current dashboard assumes a wide desktop screen.

### Global Reach & Data Coverage
- Expand beyond the current ~300 canonical hubs to more regions and secondary ports/airports.
- Multi-language UI — the dashboard is English-only right now.
- Multi-currency cost display instead of a single implicit currency.
- Replace or augment Google News RSS with a richer, more verifiable disruption signal (e.g.
  structured event feeds, AIS vessel tracking, region-specific weather alerts).
- Accessibility: keyboard navigation, screen-reader labels, color-contrast-safe risk indicators.

### Reliability & Scale
- Persist state in a real database instead of in-memory Python objects — right now a restart
  wipes everything, and there's no per-user or per-company data isolation.
- Add authentication and basic multi-tenancy.
- Add automated tests — there currently aren't any, for either the backend or the frontend.
- Cache or pre-compute more of the graph-weighting work so the engine scales past a few
  hundred nodes without the per-request cost growing with it.

### Integrations
- A webhook or export format a real TMS/ERP system could actually consume.
- An API key / rate-limiting layer, if this were ever exposed publicly.

*In association with Arvind and TatHack Team.*
