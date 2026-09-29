import numpy as np
import joblib
import os
import re
import json
from itertools import combinations
from math import factorial
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd

# Production artifacts, resolved relative to the repo so the API works from any cwd.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EXEC_DIR = os.path.join(_ROOT, "Execution")
MODEL_PATH = os.path.join(EXEC_DIR, "risk_model.pkl")
MODEL_P50_PATH = os.path.join(EXEC_DIR, "risk_model_p50.pkl")
MODEL_P95_PATH = os.path.join(EXEC_DIR, "risk_model_p95.pkl")
BAND_META_PATH = os.path.join(EXEC_DIR, "quantile_band_meta.json")
ENCODER_PATH = os.path.join(EXEC_DIR, "label_encoders.pkl")
NLP_ANCHORS_PATH = os.path.join(EXEC_DIR, "nlp_anchors.pt")
CALIBRATION_PATH = os.path.join(EXEC_DIR, "calibration_profiles.json")

FEATURES = ["Leg_Type", "Origin_Node", "Destination_Node", "Transport_Mode", "Condition_Flag", "NLP_Severity_Score"]
QUANTILES = ("p50", "p85", "p95")
NLP_GRID = np.round(np.linspace(0.0, 1.0, 21), 2)

# Canonical hub -> node name the quantile model was trained on (Code/real_dataset_builder.py).
# Only hubs that genuinely appear in the training corpus for that mode are mapped; every
# other hub uses the mode prior (average over the trained hubs of that mode).
TRAINED_HUBS = {
    "sea": {"PORT-SEATTLE": "Seattle Port", "PORT-LOSANGELES": "Los Angeles Port",
            "PORT-LONGBEACH": "Los Angeles Port", "PORT-NEWYORK": "New York Port",
            "PORT-ROTTERDAM": "Rotterdam Port", "PORT-MUMBAI": "Mumbai Port",
            "PORT-SINGAPORE": "Singapore Port", "PORT-SHANGHAI": "Shanghai Port",
            "CHOKE-SUEZ": "Suez Canal"},
    "rail": {"RAIL-CHICAGO": "Chicago Rail Hub", "RAIL-HOUSTON": "Houston Port",
             "PORT-HOUSTON": "Houston Port", "HUB-DALLAS": "Dallas Corridor"},
    "air": {"AIR-ATLANTA": "Atlanta Air Hub", "AIR-DELHI": "Delhi Air Cargo", "AIR-DUBAI": "Dubai Logistics Hub"},
    "road": {},
}
# Legacy city-name aliases used by older callers.
LEGACY_CITY_MAP = {
    "Seattle": "Seattle Port", "Los Angeles": "Los Angeles Port", "Houston": "Houston Port",
    "Chicago": "Chicago Rail Hub", "St. Louis": "St. Louis Hub", "Atlanta": "Atlanta Air Hub",
    "New York": "New York Port", "Mumbai": "Mumbai Port", "Delhi": "Delhi Air Cargo",
    "Rotterdam": "Rotterdam Port", "Singapore": "Singapore Port", "Shanghai": "Shanghai Port",
    "Dubai": "Dubai Logistics Hub", "Dallas": "Dallas Corridor", "Suez": "Suez Canal",
}
# Nodes each mode was trained on (origin, destination).
MODE_NODES = {
    "sea": (["Seattle Port", "Los Angeles Port", "New York Port", "Rotterdam Port", "Mumbai Port",
             "Singapore Port", "Shanghai Port", "Suez Canal"],) * 2,
    "rail": (["Chicago Rail Hub", "Houston Port", "St. Louis Hub", "Dallas Corridor"],) * 2,
    "air": (["Atlanta Air Hub", "Delhi Air Cargo", "Dubai Logistics Hub"],) * 2,
    "road": (["Regional Hub"], ["Local Terminal"]),
}
LEG_TYPE = {"sea": "Global_Freight", "air": "Global_Freight", "rail": "Last_Mile", "road": "Last_Mile"}
PRIOR = "*"  # key for "hub not in training corpus -> use the mode prior"

# Shapley players; Transport_Mode and Leg_Type are deterministic twins so they share one player.
PLAYERS = {
    "origin": ["Origin_Node"],
    "destination": ["Destination_Node"],
    "transport_mode": ["Transport_Mode", "Leg_Type"],
    "weather_condition": ["Condition_Flag"],
    "news_severity": ["NLP_Severity_Score"],
}


class ThreatIntelligencePredictor:
    """
    Quantile ML delay model (p50 / p85 / p95) with per-mode calibration and Shapley attribution.

    The production p85 model plus its p50/p95 siblings are evaluated once at warm-up over a
    grid of (origin, destination, NLP severity) per mode. Routing then reads delays from that
    table in O(1), which is what makes it cheap enough to use inside Dijkstra's weight function.
    """

    def __init__(self, lazy_load=False):
        self.is_trained = False
        self.model = None
        self.models: Dict[str, Any] = {}
        self.encoders = None
        self.profiles = {}
        self.band_meta = {}
        self._tables: Dict[str, Dict[str, Any]] = {}
        self._explain_cache: Dict[Tuple, Dict[str, Any]] = {}
        if not lazy_load:
            self.warmup()

    # ------------------------------------------------------------------ loading
    def warmup(self):
        if self.is_trained:
            return
        print("[PREDICTOR] Starting warmup...")
        if not os.path.exists(MODEL_PATH) or not os.path.exists(ENCODER_PATH):
            print("CRITICAL: Production models missing. Running in deterministic fallback mode.")
            return

        self.model = joblib.load(MODEL_PATH)
        self.encoders = joblib.load(ENCODER_PATH)
        self.models = {"p85": self.model}
        for key, path in (("p50", MODEL_P50_PATH), ("p95", MODEL_P95_PATH)):
            if os.path.exists(path):
                self.models[key] = joblib.load(path)
        if os.path.exists(BAND_META_PATH):
            with open(BAND_META_PATH) as f:
                self.band_meta = json.load(f)
        if len(self.models) < 3:
            print("WARNING: p50/p95 siblings missing (run Code/train_quantile_band.py); band collapses to p85.")

        if os.path.exists(CALIBRATION_PATH):
            with open(CALIBRATION_PATH, "r") as f:
                self.profiles = json.load(f)
            print(f"Calibration Layer: Loaded {len(self.profiles)} mode profiles from historical p5/p95 analysis.")
        else:
            print("WARNING: Calibration profiles missing. Using defensive fallbacks.")

        self._build_tables()
        self.is_trained = True
        print(f"Quantile Brain Loaded: {sorted(self.models)} models, delay tables for {sorted(self._tables)}.")

    def _encode_rows(self, rows: List[Dict[str, Any]]) -> pd.DataFrame:
        df = pd.DataFrame(rows)
        for col in FEATURES[:5]:
            df[col] = self.encoders[col].transform(df[col].astype(str))
        return df[FEATURES]

    def _build_tables(self):
        """Pre-evaluate every model on every trained (origin, destination) pair and NLP grid point."""
        for mode, (origins, dests) in MODE_NODES.items():
            rows = [{"Leg_Type": LEG_TYPE[mode], "Origin_Node": o, "Destination_Node": d,
                     "Transport_Mode": mode, "Condition_Flag": "Clear", "NLP_Severity_Score": float(s)}
                    for o in origins for d in dests for s in NLP_GRID]
            X = self._encode_rows(rows)
            grids = {}
            for q in QUANTILES:
                model = self.models.get(q, self.model)
                grid = model.predict(X).reshape(len(origins), len(dests), len(NLP_GRID))
                if origins == dests and len(origins) > 1:
                    # the corpus never contains o == d legs, so keep those cells out of the priors
                    for i in range(len(origins)):
                        grid[i, i, :] = np.nan
                grids[q] = grid
            self._tables[mode] = {"origins": origins, "dests": dests, "grids": grids}

    # ---------------------------------------------------------------- inference
    @staticmethod
    def training_node(hub_id: str, mode: str) -> Optional[str]:
        return TRAINED_HUBS.get(mode, {}).get(hub_id)

    def _lookup(self, mode: str, origin: Optional[str], dest: Optional[str], q: str, nlp: float) -> float:
        t = self._tables[mode]
        grid = t["grids"][q]
        oi = t["origins"].index(origin) if origin in t["origins"] else None
        di = t["dests"].index(dest) if dest in t["dests"] else None
        if oi is not None and di is not None and not np.isnan(grid[oi, di, 0]):
            curve = grid[oi, di, :]
        elif oi is not None:
            curve = np.nanmean(grid[oi, :, :], axis=0)
        elif di is not None:
            curve = np.nanmean(grid[:, di, :], axis=0)
        else:
            curve = np.nanmean(grid, axis=(0, 1))
        return float(np.interp(min(max(nlp, 0.0), 1.0), NLP_GRID, curve))

    def _calibrate(self, mode: str, raw: float) -> Tuple[float, str]:
        profile = self.profiles.get(mode, {"floor": 0.0, "cap": 240.0})
        capped = min(max(0.0, raw), profile["cap"])
        final = max(capped, profile["floor"])
        if final == profile["floor"] and capped < profile["floor"]:
            reason = f"Baseline Operational Friction (Historical p5: {profile['floor']}h)"
        elif capped < raw:
            reason = f"Operational Cap Applied (Historical p95 Bound: {profile['cap']}h)"
        else:
            reason = "Quantile Disruption Prediction"
        return final, reason

    def predict_delay_band(self, origin_hub: str, dest_hub: str, transport_mode: str,
                           nlp_score: float = 0.0) -> Dict[str, Any]:
        """
        Calibrated p50/p85/p95 dwell/delay (hours) for one transit leg between two canonical hubs.
        """
        mode = transport_mode.lower()
        if not self.is_trained or mode not in self._tables:
            priors = {"road": 2.5, "sea": 48.0, "air": 12.0, "rail": 18.0}
            d = priors.get(mode, 12.0)
            return {"p50": d * 0.5, "p85": d, "p95": d * 1.6, "resolution": "deterministic_prior",
                    "calibration_reason": "Deterministic Operational Prior (Engine Warming)"}

        o = self.training_node(origin_hub, mode) or (MODE_NODES[mode][0][0] if mode == "road" else None)
        d = self.training_node(dest_hub, mode) or (MODE_NODES[mode][1][0] if mode == "road" else None)
        band, reasons = {}, {}
        for q in QUANTILES:
            band[q], reasons[q] = self._calibrate(mode, self._lookup(mode, o, d, q, nlp_score))
        # Independently trained quantile models can cross; enforce p50 <= p85 <= p95.
        ordered = sorted(band[q] for q in QUANTILES)
        band = {q: round(v, 2) for q, v in zip(QUANTILES, ordered)}
        exact = mode == "road" or (o is not None and d is not None)
        band["resolution"] = "trained_hub" if exact else "mode_prior"
        band["calibration_reason"] = reasons["p85"]
        return band

    def predict_worst_case_delay(self, origin: str, destination: str, transport_mode: str,
                                 leg_type: str = None, condition_flag: str = "Clear",
                                 nlp_score: float = 0.0) -> Dict[str, Any]:
        """
        Stage 4: p85 Quantile Prediction with calibration (single-leg API, kept for compatibility).
        Accepts canonical hub IDs, trained node names or legacy city names.
        """
        mode = transport_mode.lower()
        o = self._resolve_name(origin, mode)
        d = self._resolve_name(destination, mode)
        if not self.is_trained:
            band = self.predict_delay_band(origin, destination, mode, nlp_score)
            return {"raw_model_prediction": band["p85"], "calibrated_delay": band["p85"],
                    "baseline_systemic_friction": band["p85"], "final_delay_presented": band["p85"],
                    "calibration_reason": band["calibration_reason"], "p_quantile": 0.85, "is_defensible": True}
        try:
            row = {"Leg_Type": leg_type or LEG_TYPE.get(mode, "Global_Freight"), "Transport_Mode": mode,
                   "Condition_Flag": condition_flag, "NLP_Severity_Score": nlp_score}
            if o is not None and d is not None:
                raw = float(self.model.predict(self._encode_rows([{**row, "Origin_Node": o, "Destination_Node": d}]))[0])
            else:
                raw = self._lookup(mode, o, d, "p85", nlp_score)
            final, reason = self._calibrate(mode, raw)
            profile = self.profiles.get(mode, {"floor": 0.0, "cap": 240.0})
            return {
                "raw_model_prediction": round(raw, 2),
                "calibrated_delay": round(min(max(0.0, raw), profile["cap"]), 2),
                "baseline_systemic_friction": profile["floor"],
                "final_delay_presented": round(final, 2),
                "calibration_reason": reason,
                "node_resolution": "trained_hub" if (o and d) else "mode_prior",
                "p_quantile": 0.85,
                "is_defensible": True,
            }
        except Exception as e:
            print(f"Calibration Inference Error: {e}")
            return {"final_delay_presented": 0.0, "calibration_reason": "Inference Error"}

    def _resolve_name(self, name: str, mode: str) -> Optional[str]:
        if mode == "road":
            return None
        trained = set(MODE_NODES.get(mode, ([], []))[0])
        for candidate in (TRAINED_HUBS.get(mode, {}).get(name), LEGACY_CITY_MAP.get(name), name):
            if candidate in trained:
                return candidate
        return None

    # ----------------------------------------------------------- explainability
    def explain_delay(self, origin_hub: str, dest_hub: str, transport_mode: str,
                      nlp_score: float, quantile: str = "p85") -> Dict[str, Any]:
        """
        Exact Shapley attribution (all 2^5 coalitions) of the raw quantile prediction for one leg,
        using an interventional value function over the training background sample.

        For a hub outside the training corpus the origin/destination player takes the mode prior
        (uniform over trained hubs of that mode), matching what predict_delay_band() reports.
        Contributions are in hours and sum to (prediction - background mean).
        """
        mode = transport_mode.lower()
        if not self.is_trained or not self.band_meta.get("background"):
            return {}
        nlp_q = round(float(nlp_score), 2)
        key = (origin_hub, dest_hub, mode, nlp_q, quantile)
        if key in self._explain_cache:
            return self._explain_cache[key]

        model = self.models.get(quantile, self.model)
        background = pd.DataFrame(self.band_meta["background"])[FEATURES]
        origins, dests = MODE_NODES[mode]
        o = self.training_node(origin_hub, mode) or (origins[0] if mode == "road" else None)
        d = self.training_node(dest_hub, mode) or (dests[0] if mode == "road" else None)
        enc = lambda col, v: int(self.encoders[col].transform([v])[0])
        choices = {
            "Origin_Node": [enc("Origin_Node", x) for x in ([o] if o else origins)],
            "Destination_Node": [enc("Destination_Node", x) for x in ([d] if d else dests)],
            "Transport_Mode": [enc("Transport_Mode", mode)],
            "Leg_Type": [enc("Leg_Type", LEG_TYPE[mode])],
            "Condition_Flag": [enc("Condition_Flag", "Clear")],
            "NLP_Severity_Score": [nlp_q],
        }

        names = list(PLAYERS)
        value = {}
        frames, index = [], []
        for r in range(len(names) + 1):
            for coalition in combinations(names, r):
                cols = [c for p in coalition for c in PLAYERS[p]]
                block = background.copy()
                variants = [block]
                for col in cols:
                    expanded = []
                    for frame in variants:
                        for v in choices[col]:
                            f = frame.copy()
                            f[col] = v
                            expanded.append(f)
                    variants = expanded
                stacked = pd.concat(variants, ignore_index=True)
                frames.append(stacked)
                index.append((frozenset(coalition), len(stacked)))
        preds = model.predict(pd.concat(frames, ignore_index=True))
        pos = 0
        for coalition, n in index:
            value[coalition] = float(preds[pos:pos + n].mean())
            pos += n

        n = len(names)
        phi = {}
        for p in names:
            others = [x for x in names if x != p]
            total = 0.0
            for r in range(len(others) + 1):
                w = factorial(r) * factorial(n - r - 1) / factorial(n)
                for s in combinations(others, r):
                    s = frozenset(s)
                    total += w * (value[s | {p}] - value[s])
            phi[p] = round(total, 2)

        result = {
            "method": "exact_shapley_interventional",
            "quantile": quantile,
            "base_value_h": round(value[frozenset()], 2),
            "prediction_h": round(value[frozenset(names)], 2),
            "contributions_h": dict(sorted(phi.items(), key=lambda kv: -abs(kv[1]))),
            "node_resolution": "trained_hub" if (o and d) else "mode_prior",
        }
        self._explain_cache[key] = result
        return result


# Fallback labels for anchor files saved before categories were stored alongside the matrix
# (row order of Code/precompute_nlp.py DISASTER_CORPUS).
DISASTER_CATEGORIES = [
    "INFRASTRUCTURE",  # Ever Given grounding
    "GEOPOLITICAL",    # Red Sea attacks
    "LABOR",           # Canadian port strike
    "LABOR",           # US rail labour dispute
    "CYBER",           # NotPetya
    "PUBLIC_HEALTH",   # Ningbo COVID shutdown
    "LABOR",           # UK HGV driver shortage
    "CONGESTION",      # berthing congestion
    "CUSTOMS",         # customs IT outage
    "LABOR",           # trucker strikes / blockades
    "CONGESTION",      # storage shortage
    "REGULATORY",      # border inspections
    "WEATHER",         # Pakistan floods
]


class ContrastiveNLPEngine:
    """Stage 2: Contrastive NLP threat scoring against historical disaster/safe anchors."""

    def __init__(self, lazy_load=False):
        self._ready = False
        # Margins of 0.05-0.08 are typical for benign real-world logistics headlines, so use the
        # original research engine's floor (Code/nlp_engine.py) rather than 0.04.
        self.noise_floor = 0.08
        # Same calibration as the original research engine (Code/nlp_engine.py): a margin of
        # ~0.29 between the best disaster and best safe anchor saturates the score at 1.0.
        self.calibration_multiplier = 3.5
        self.warmup_error = None
        if not lazy_load:
            self.warmup()

    @property
    def ready(self) -> bool:
        return self._ready

    def warmup(self):
        if self._ready:
            return
        print("[NLP ENGINE] Starting warmup...")
        try:
            import torch
            from sentence_transformers import SentenceTransformer, util
            self.model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
            self.util = util
            if not os.path.exists(NLP_ANCHORS_PATH):
                raise FileNotFoundError(NLP_ANCHORS_PATH)
            # Anchors were saved from a CUDA session; without map_location this fails on every CPU host.
            anchors = torch.load(NLP_ANCHORS_PATH, map_location="cpu")
            self.disaster_matrix = anchors["disaster_matrix"].cpu()
            self.safe_matrix = anchors["safe_matrix"].cpu()
            self.categories = anchors.get("disaster_categories", DISASTER_CATEGORIES)
            if len(self.categories) != self.disaster_matrix.shape[0]:
                raise ValueError("disaster_categories does not match the anchor matrix")
            self._ready = True
            print("NLP Brain: Loaded Historical Anchor Matrix.")
        except Exception as e:
            self.warmup_error = str(e)
            print(f"[NLP ENGINE] Warmup failed: {e}")
            self._ready = False

    @staticmethod
    def _chunks(text: str) -> List[str]:
        parts = re.split(r"(?<=[.!?])\s+|\s+\|\s+", text)
        return [p.strip() for p in parts if len(p.split()) >= 3] or [text.strip()]

    def analyze(self, news_text: str) -> Dict[str, Any]:
        """Score + category of the most threatening sentence/headline in `news_text`."""
        empty = {"score": 0.0, "margin": 0.0, "category": None}
        if not self._ready or not news_text or len(news_text.strip()) < 5:
            return empty
        chunks = self._chunks(news_text)
        emb = self.model.encode(chunks, convert_to_tensor=True)
        d = self.util.cos_sim(emb, self.disaster_matrix).cpu().numpy()
        s = self.util.cos_sim(emb, self.safe_matrix).cpu().numpy()
        # Margin is computed per chunk so one alarming headline is not diluted by benign ones.
        margins = d.max(axis=1) - s.max(axis=1)
        i = int(np.argmax(margins))
        margin = float(margins[i])
        if margin <= self.noise_floor:
            return {"score": 0.0, "margin": round(margin, 4), "category": None}
        return {
            "score": float(min(1.0, margin * self.calibration_multiplier)),
            "margin": round(margin, 4),
            "category": self.categories[int(np.argmax(d[i]))],
        }

    def get_semantic_score(self, news_text: str) -> float:
        return self.analyze(news_text)["score"]


class CARFFilter:
    """
    Stage 3: Context-Aware Relevance Filter.

    A threat is suppressed for a leg only when the news is clearly about a *different* mode
    (e.g. an airport closure scored against a sea leg). News that mentions the leg's own mode,
    or no mode at all (war, floods, pandemic), is kept.
    """

    def __init__(self):
        self.relevance_map = {
            "air": ["airport", "airports", "flight", "flights", "airspace", "aviation", "airline",
                    "airlines", "air cargo", "aircraft", "runway", "plane", "planes"],
            "sea": ["port", "ports", "seaport", "vessel", "vessels", "ship", "ships", "shipping",
                    "canal", "ocean", "maritime", "dock", "docks", "dockworkers", "berth", "berthing",
                    "container ship", "strait", "naval", "tanker", "tankers", "transshipment", "shipping lane"],
            "rail": ["rail", "railway", "railways", "railroad", "track", "tracks", "locomotive", "train",
                     "trains", "derailment", "derailed", "freight train", "station", "signalling", "signaling"],
            "road": ["highway", "highways", "motorway", "truck", "trucks", "trucker", "truckers", "trucking",
                     "lorry", "lorries", "hgv", "traffic jam", "bridge", "road", "roads", "delivery", "interstate",
                     "toll", "border crossing", "checkpoint"],
        }
        self._patterns = {
            mode: re.compile(r"\b(" + "|".join(re.escape(k) for k in kws) + r")\b")
            for mode, kws in self.relevance_map.items()
        }

    def mode_signals(self, news_context: str) -> set:
        text = news_context.lower()
        return {mode for mode, pat in self._patterns.items() if pat.search(text)}

    def apply_filter(self, semantic_score: float, news_context: str, transport_mode: str) -> float:
        if semantic_score <= 0:
            return 0.0
        mode = transport_mode.lower()
        if mode not in self.relevance_map:
            return semantic_score  # transfers / unknown modes: keep the signal
        signals = self.mode_signals(news_context)
        if signals and mode not in signals:
            return 0.0
        return semantic_score

    def max_pool_threats(self, scores: List[float]) -> float:
        return float(np.max(scores)) if scores else 0.0
