import math
import time
from typing import List, Dict, Any, Optional, Tuple

import networkx as nx

from .multimodal_network import MODE_PROFILES, PRIORITY_MULTIPLIERS, create_multimodal_network
from .threat_intelligence import ContrastiveNLPEngine, CARFFilter
from .news_ingestion import DynamicNewsIngestor
from .node_resolver import NodeResolver

# Each persona prices uncertain delay at a different quantile of the ML delay distribution.
PERSONA_QUANTILE = {"FASTEST": "p50", "BALANCED": "p85", "SAFEST": "p95"}
PERSONAS = ("FASTEST", "SAFEST", "BALANCED")
SCENARIO_COST_SURCHARGE = 0.10   # war-risk / congestion surcharge on legs into a disrupted hub
PREFERRED_OFF_MODE_PENALTY = 1.5  # soft bias for routing_policy=PREFERRED
# SAFEST prices threat as risk-hours: a certain disruption (threat 1.0) costs 10 days. Additive,
# so a weak signal on a long ocean leg cannot multiply that leg's cost several times over.
SAFEST_RISK_HOURS = 240.0
Z85, Z95 = 1.036, 1.645


class RouteRecommender:
    """
    Supplychainer Unified Multimodal Optimization Engine.

    Every transit leg carries a calibrated p50/p85/p95 delay from the quantile model. FASTEST
    plans on the median, BALANCED on p85 (the model's design point) and SAFEST on p95 plus
    threat-weighted risk-hours, so the three personas differ for principled reasons rather than
    hand-tuned constants.
    """

    def __init__(self, network, predictor, simulator, scenario_mgr, demo_mode=False, intel=None):
        self.predictor = predictor
        self.simulator = simulator
        self.scenario_mgr = scenario_mgr
        self.demo_mode = demo_mode
        self.intel = intel
        self.is_warmed_up = False
        self.warmup_failed = False
        self.warmup_notes: List[str] = []

        self.nlp = intel.nlp if intel is not None else ContrastiveNLPEngine(lazy_load=True)
        self.carf = intel.carf if intel is not None else CARFFilter()
        self.news_ingestor = DynamicNewsIngestor()
        self.resolver = NodeResolver()

        print("[STARTUP] Initializing Split-Node Global Topology...")
        self.unified_graph = network if network is not None else create_multimodal_network()
        self._annotate_static_band()

        if self.demo_mode:
            self.is_warmed_up = True
        print("[STARTUP] Unified Engine Ready.")

    # ------------------------------------------------------------------ warm-up
    def _annotate_static_band(self):
        """Cache each transit edge's no-news delay band so routing only re-queries the model for threatened legs."""
        G = self.unified_graph
        for u, v, d in G.edges(data=True):
            if d["type"] == "transfer":
                d["ml_band"] = {"p50": 0.0, "p85": 0.0, "p95": 0.0, "resolution": "n/a"}
            else:
                d["ml_band"] = self.predictor.predict_delay_band(
                    G.nodes[u]["physical_id"], G.nodes[v]["physical_id"], d["transport_mode"], 0.0)

    def run_background_warmup(self):
        if self.is_warmed_up:
            return
        print("[WARMUP] Loading quantile models and NLP anchors...")
        try:
            self.predictor.warmup()
            if not self.predictor.is_trained:
                self.warmup_notes.append("Quantile model artifacts missing: using deterministic priors.")
            self._annotate_static_band()
            self.nlp.warmup()
            if not self.nlp.ready:
                # Routing still works without NLP, but say so instead of reporting "calibrated".
                self.warmup_notes.append(f"NLP engine offline ({self.nlp.warmup_error}); live news scoring disabled.")
            self.is_warmed_up = True
            print("[WARMUP] Complete." + (f" Notes: {self.warmup_notes}" if self.warmup_notes else ""))
        except Exception as e:
            print(f"[WARMUP] Error during warmup: {e}")
            self.warmup_notes.append(str(e))
            self.warmup_failed = True

    def engine_status(self) -> Dict[str, Any]:
        return {
            "ml_trained": bool(self.predictor.is_trained),
            "quantile_models": sorted(getattr(self.predictor, "models", {}).keys()),
            "nlp_ready": bool(self.nlp.ready),
            "warmed_up": self.is_warmed_up,
            "warmup_failed": self.warmup_failed,
            "notes": self.warmup_notes,
        }

    # ----------------------------------------------------------------- helpers
    def _hub_threat(self, hub_id: str, mode: str, disruptions: Dict[str, Any], live: Dict[str, Any]):
        """Best available intelligence for a leg arriving at `hub_id` by `mode`."""
        if hub_id in disruptions:
            d = disruptions[hub_id]
            return d["threat"], d["delay"], d.get("closed", False), d["reason"], d.get("category"), "SCENARIO"
        hub_live = live.get(hub_id)
        if hub_live:
            by_mode = hub_live["threat_by_mode"]
            t = by_mode.get(mode, 0.0)
            if t > 0:
                return t, 0.0, False, hub_live["headline"], hub_live.get("category"), "LIVE_NEWS"
        return 0.0, 0.0, False, None, None, None

    def _leg(self, G, u, v, d, disruptions, live) -> Dict[str, Any]:
        """Full evaluation of one edge; shared by the weight function and route composition."""
        mode = d["transport_mode"]
        u_hub, v_hub = G.nodes[u]["physical_id"], G.nodes[v]["physical_id"]
        leg = {"base_time": d["baseline_time"], "cost": d.get("cost", 0.0), "mode": mode, "type": d["type"],
               "threat": 0.0, "scenario_delay": 0.0, "closed": False, "reason": None, "category": None,
               "source": None, "ml": d.get("ml_band") or {"p50": 0.0, "p85": 0.0, "p95": 0.0}}
        if d["type"] == "transfer":
            leg["threat"] = d.get("risk", 0.0)  # handling risk from TRANSFER_PROFILES
            return leg
        threat, delay, closed, reason, category, source = self._hub_threat(v_hub, mode, disruptions, live)
        if source:
            leg.update(threat=threat, scenario_delay=delay, closed=closed, reason=reason,
                       category=category, source=source)
            band = self.predictor.predict_delay_band(u_hub, v_hub, mode, threat)
            # The announced scenario delay is already in the ETA; the model contributes only the
            # uncertainty *beyond* it, so the two are never double counted.
            leg["ml"] = {q: max(0.0, band[q] - delay) for q in ("p50", "p85", "p95")}
            leg["ml"]["resolution"] = band.get("resolution")
        return leg

    def _weight_fn(self, G, persona, disruptions, live, allowed_modes, soft_pref, blocked_modes,
                   avoid_hubs, priority, respect_closures=True):
        q = PERSONA_QUANTILE[persona]
        prio = PRIORITY_MULTIPLIERS.get(priority, 1.0)

        def weight(u, v, d):
            mode = d["transport_mode"]
            if G.nodes[v]["physical_id"] in avoid_hubs or G.nodes[u]["physical_id"] in avoid_hubs:
                return None
            if mode in blocked_modes:
                return None
            if allowed_modes is not None and mode not in allowed_modes:
                return None
            leg = self._leg(G, u, v, d, disruptions, live)
            if leg["closed"] and respect_closures:
                return None
            t = leg["base_time"] + leg["scenario_delay"] + leg["ml"][q]
            if persona == "FASTEST":
                w = t
            elif persona == "SAFEST":
                w = t + leg["threat"] * SAFEST_RISK_HOURS
            else:
                # Priority scales how much an hour is worth relative to a dollar.
                w = (t / prio) * 0.3 + (leg["cost"] / 150.0) * 0.5 + (leg["threat"] * 40.0) * 0.2
            if soft_pref and mode not in ("transfer", soft_pref):
                w *= PREFERRED_OFF_MODE_PENALTY
            return w
        return weight

    def _shortest(self, G, sources, targets, weight) -> Optional[List[str]]:
        dist, paths = nx.multi_source_dijkstra(G, sources, weight=weight)
        reachable = [t for t in targets if t in dist]
        if not reachable:
            return None
        return paths[min(reachable, key=lambda t: dist[t])]

    # --------------------------------------------------------------- recommend
    def recommend(self, source: str, destination: str, transport_preference: str = "any",
                  routing_policy: str = "STRICT", cargo_type: str = "general",
                  priority: str = "normal", scenario: str = None,
                  overrides: dict = None, scenarios: Optional[List[str]] = None,
                  include_live_scenarios: bool = True) -> dict:
        t0 = time.perf_counter()
        overrides = overrides or {}
        avoid_hubs = set(overrides.get("avoid_chokepoints", []))
        cost_ceiling = overrides.get("cost_ceiling", 999999)
        max_delay = overrides.get("max_delay", 9999)
        pref = (transport_preference or "any").lower()
        policy = (routing_policy or "STRICT").upper()

        res_s = self.resolver.resolve_entry_points(source, pref)
        res_d = self.resolver.resolve_entry_points(destination, pref)
        if "error" in res_s:
            return {"error": res_s["error"]}
        if "error" in res_d:
            return {"error": res_d["error"]}
        sources, targets = res_s["ids"], res_d["ids"]
        if res_s["hub"] == res_d["hub"]:
            return {"error": "Origin and destination resolve to the same hub."}

        # Scenario resolution is request-scoped: nothing global is mutated here.
        requested = [s for s in (scenarios or []) + ([scenario] if scenario else []) if s]
        applied = list(dict.fromkeys(requested + (self.scenario_mgr.active_scenario_ids if include_live_scenarios else [])))
        unknown = [s for s in requested if not self.scenario_mgr.get_scenario(s)]
        if unknown:
            return {"error": f"Unknown scenario(s): {unknown}"}
        disruptions = self.scenario_mgr.disruptions_for(applied)
        live = self.intel.hub_threats() if self.intel is not None else {}

        G = self.unified_graph
        allowed = {pref, "transfer", "road"} if (pref != "any" and policy == "STRICT") else None
        soft = pref if (pref != "any" and policy == "PREFERRED") else None
        blocked = {m for m, p in MODE_PROFILES.items() if cargo_type in p.get("cargo_restrictions", [])}

        candidates = []
        for persona in PERSONAS:
            wf = self._weight_fn(G, persona, disruptions, live, allowed, soft, blocked, avoid_hubs, priority)
            path = self._shortest(G, sources, targets, wf)
            if not path:
                continue
            cand = self._compose(G, path, persona, disruptions, live, origin_hub=res_s["hub"])
            if cand["total_cost"] > cost_ceiling or cand["adjusted_eta"] > max_delay * 24:
                continue
            cand["override_applied"] = bool(avoid_hubs or cost_ceiling < 999999 or max_delay < 9999)
            candidates.append(cand)

        closed_hubs = {h for h, d in disruptions.items() if d.get("closed")}
        if not candidates:
            msg = "No valid multimodal route under current strategic constraints."
            if blocked:
                msg += f" Cargo type '{cargo_type}' cannot travel by {', '.join(sorted(blocked))}."
            if closed_hubs:
                msg += f" Closed by active scenario: {', '.join(sorted(closed_hubs))}."
            return {"error": msg}

        # Merge personas that landed on the same physical path.
        final: List[Dict[str, Any]] = []
        by_sig: Dict[Tuple, Dict[str, Any]] = {}
        for c in candidates:
            sig = tuple((l["to"], l["mode"]) for l in c["legs"])
            if sig in by_sig:
                by_sig[sig]["personas"].append(c["persona"])
            else:
                by_sig[sig] = c
                final.append(c)
        final.sort(key=lambda c: c["eta_band"]["p50"])
        self._explain(final)

        hold_option = None
        if closed_hubs:
            hold_option = self._hold_option(G, sources, targets, disruptions, live, allowed, blocked,
                                            avoid_hubs, priority, final, res_s["hub"])

        return {
            "origin": source, "destination": destination,
            "origin_hub": res_s["hub"], "destination_hub": res_d["hub"],
            "active_scenario": ", ".join(self.scenario_mgr.SCENARIOS[s]["name"] for s in applied) or None,
            "applied_scenarios": applied,
            "closed_hubs": sorted(closed_hubs),
            "hold_option": hold_option,
            "request": {"transport_preference": pref, "routing_policy": policy,
                        "cargo_type": cargo_type, "priority": priority},
            "engine": {"latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                       "live_intel_hubs": len(live), **self.engine_status()},
            "recommendations": final[:3],
        }

    # ---------------------------------------------------------------- composing
    def _compose(self, G, path, persona, disruptions, live, origin_hub=None) -> Dict[str, Any]:
        legs = []
        trace = {
            "eta": {"transit": 0.0, "transfer": 0.0, "scenario": 0.0},
            "cost": {"transit": 0.0, "transfer": 0.0, "scenario": 0.0},
            "risk": {"baseline": 0.0, "scenario": 0.0, "live_news": 0.0},
            "ml": {"quantile_used": PERSONA_QUANTILE[persona], "buffer_h": {"p50": 0.0, "p85": 0.0, "p95": 0.0},
                   "legs_on_mode_prior": 0, "legs_on_trained_hub": 0},
        }
        var85, var95, sum95 = 0.0, 0.0, 0.0
        mode_km: Dict[str, float] = {}
        worst_leg = None

        # A disruption at the origin hub itself is paid once, up front.
        if origin_hub in disruptions:
            od = disruptions[origin_hub]
            trace["eta"]["scenario"] += od["delay"]
            trace["risk"]["scenario"] = max(trace["risk"]["scenario"], od["threat"])

        for u, v in zip(path, path[1:]):
            d = G[u][v]
            leg = self._leg(G, u, v, d, disruptions, live)
            nu, nv = G.nodes[u], G.nodes[v]
            l_time = leg["base_time"] + leg["scenario_delay"]
            l_cost = leg["cost"]
            if leg["source"] == "SCENARIO":
                surcharge = l_cost * SCENARIO_COST_SURCHARGE
                trace["cost"]["scenario"] += surcharge
                trace["eta"]["scenario"] += leg["scenario_delay"]
                trace["risk"]["scenario"] = max(trace["risk"]["scenario"], leg["threat"])
            elif leg["source"] == "LIVE_NEWS":
                trace["risk"]["live_news"] = max(trace["risk"]["live_news"], leg["threat"])
            else:
                trace["risk"]["baseline"] = max(trace["risk"]["baseline"], leg["threat"])

            if leg["type"] == "transfer":
                trace["eta"]["transfer"] += leg["base_time"]
                trace["cost"]["transfer"] += l_cost
            else:
                trace["eta"]["transit"] += leg["base_time"]
                trace["cost"]["transit"] += l_cost
                mode_km[leg["mode"]] = mode_km.get(leg["mode"], 0.0) + d.get("distance", 0.0)
                ml = leg["ml"]
                for q in ("p50", "p85", "p95"):
                    trace["ml"]["buffer_h"][q] += ml[q]
                var85 += ((ml["p85"] - ml["p50"]) / Z85) ** 2
                var95 += ((ml["p95"] - ml["p50"]) / Z95) ** 2
                sum95 += ml["p95"]
                res = (d.get("ml_band") or {}).get("resolution") if leg["source"] is None else ml.get("resolution")
                trace["ml"]["legs_on_trained_hub" if res == "trained_hub" else "legs_on_mode_prior"] += 1
                if worst_leg is None or ml["p85"] > worst_leg[2]["ml"]["p85"]:
                    worst_leg = (u, v, leg)

            legs.append({
                "from": nu["physical_id"], "from_name": nu.get("display_name"),
                "to": nv["physical_id"], "to_name": nv.get("display_name", nv["physical_id"]),
                "from_coords": [nu.get("lat"), nu.get("lon")], "to_coords": [nv.get("lat"), nv.get("lon")],
                "mode": leg["mode"].upper(), "type": leg["type"],
                "distance_km": round(d.get("distance", 0.0), 1),
                "eta": round(l_time, 1), "cost": round(l_cost, 2),
                "threat": round(leg["threat"], 2), "threat_category": leg["category"],
                "delay_band": {q: round(leg["ml"][q], 1) for q in ("p50", "p85", "p95")},
                "reason": leg["reason"] or "No disruption signal for this leg.",
                "intel_source": leg["source"] or "NO_SIGNAL",
            })

        base_eta = trace["eta"]["transit"] + trace["eta"]["transfer"] + trace["eta"]["scenario"]
        buf = trace["ml"]["buffer_h"]
        # Route quantiles assuming independent leg delays (normal approximation around the summed
        # medians), plus the fully-correlated worst case (sum of per-leg p95s) as an upper bound.
        eta_band = {
            "p50": round(base_eta + buf["p50"], 1),
            "p85": round(base_eta + buf["p50"] + Z85 * math.sqrt(var85), 1),
            "p95": round(base_eta + buf["p50"] + Z95 * math.sqrt(var95), 1),
            "p95_correlated": round(base_eta + sum95, 1),
        }
        total_cost = trace["cost"]["transit"] + trace["cost"]["transfer"] + trace["cost"]["scenario"]
        for section in ("eta", "cost"):
            trace[section] = {k: round(v, 1 if section == "eta" else 2) for k, v in trace[section].items()}
        trace["ml"]["buffer_h"] = {k: round(v, 1) for k, v in buf.items()}
        if worst_leg is not None:
            u, v, leg = worst_leg
            trace["ml"]["dominant_leg"] = {
                "from": G.nodes[u]["physical_id"], "to": G.nodes[v]["physical_id"], "mode": leg["mode"],
                "p85_delay_h": round(leg["ml"]["p85"] + leg["scenario_delay"], 1),
                "attribution": self.predictor.explain_delay(G.nodes[u]["physical_id"], G.nodes[v]["physical_id"],
                                                            leg["mode"], leg["threat"]),
            }

        threat = max([l["threat"] for l in legs] + [trace["risk"]["scenario"]])
        chokepoints = [l["to"] for l in legs if l["to"].startswith("CHOKE-")]
        return {
            "persona": persona,
            "personas": [persona],
            "path_nodes": list(path),
            "primary_mode": max(mode_km, key=mode_km.get).upper() if mode_km else "TRANSFER",
            "legs": legs,
            "chokepoints": chokepoints,
            "adjusted_eta": round(base_eta, 1),
            "eta_band": eta_band,
            "total_cost": round(total_cost, 2),
            "threat_level": round(threat, 2),
            "transfers": sum(1 for l in legs if l["type"] == "transfer"),
            "audit_trace": trace,
        }

    def evaluate_path(self, path_nodes: List[str], persona: str = "BALANCED",
                      scenario_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Re-price an existing route (e.g. a monitored one) under the current live picture plus
        any extra scenarios, without re-optimising it. Flags legs that are now impassable.
        """
        G = self.unified_graph
        missing = [(u, v) for u, v in zip(path_nodes, path_nodes[1:]) if not G.has_edge(u, v)]
        if missing:
            return {"error": f"Route no longer exists in the network: {missing[:3]}"}
        applied = list(dict.fromkeys((scenario_ids or []) + self.scenario_mgr.active_scenario_ids))
        disruptions = self.scenario_mgr.disruptions_for(applied)
        live = self.intel.hub_threats() if self.intel is not None else {}
        cand = self._compose(G, path_nodes, persona, disruptions, live, origin_hub=G.nodes[path_nodes[0]]["physical_id"])
        cand["blocked_at"] = sorted({G.nodes[n]["physical_id"] for n in path_nodes
                                     if disruptions.get(G.nodes[n]["physical_id"], {}).get("closed")})
        cand["affected_hubs"] = sorted({l["to"] for l in cand["legs"] if l["intel_source"] in ("SCENARIO", "LIVE_NEWS")}
                                       | ({cand["legs"][0]["from"]} if cand["legs"] and cand["legs"][0]["from"] in disruptions else set()))
        cand["applied_scenarios"] = applied
        return cand

    def _hold_option(self, G, sources, targets, disruptions, live, allowed, blocked, avoid, priority, final, origin_hub):
        """
        Compare rerouting with waiting: take the route we would normally use (no scenarios) and,
        if it runs through a closed hub, price it with the announced reopening delay.
        """
        wf = self._weight_fn(G, "FASTEST", {}, live, allowed, None, blocked, avoid, priority)
        path = self._shortest(G, sources, targets, wf)
        if not path:
            return None
        closed_on_path = sorted({G.nodes[n]["physical_id"] for n in path
                                 if disruptions.get(G.nodes[n]["physical_id"], {}).get("closed")})
        if not closed_on_path:
            return None
        cand = self._compose(G, path, "FASTEST", disruptions, live, origin_hub=origin_hub)
        best = min(final, key=lambda c: c["eta_band"]["p50"])
        delta = round(cand["eta_band"]["p50"] - best["eta_band"]["p50"], 1)
        return {
            "waits_at": closed_on_path,
            "chokepoints": cand["chokepoints"],
            "adjusted_eta": cand["adjusted_eta"],
            "eta_band": cand["eta_band"],
            "total_cost": cand["total_cost"],
            "delta_vs_best_reroute_h": delta,
            "verdict": ("REROUTE" if delta > 0 else "HOLD"),
            "note": (f"Waiting for {', '.join(closed_on_path)} to reopen is {abs(delta)}h "
                     f"{'slower' if delta > 0 else 'faster'} (p50) than the best reroute, and depends on the "
                     "announced reopening estimate holding."),
        }

    # ------------------------------------------------------------- explanations
    @staticmethod
    def _explain(cands: List[Dict[str, Any]]):
        """Explanations built only from numbers in the candidate set (no invented percentages)."""
        if not cands:
            return
        for c in cands:
            others = [o for o in cands if o is not c]
            band = c["eta_band"]
            parts = [f"{' / '.join(c['personas'])}: {c['primary_mode']} via "
                     f"{', '.join(n.replace('CHOKE-', '') for n in c['chokepoints']) or 'no chokepoints'}; "
                     f"expected {band['p50']}h (p85 {band['p85']}h, p95 {band['p95']}h), "
                     f"${c['total_cost']:,.0f}, {c['transfers']} handoffs."]
            if others:
                fastest = min(others, key=lambda o: o["eta_band"]["p50"])
                cheapest = min(others, key=lambda o: o["total_cost"])
                dt = round(fastest["eta_band"]["p50"] - band["p50"], 1)
                dc = round(cheapest["total_cost"] - c["total_cost"], 0)
                if dt > 0:
                    parts.append(f"{dt}h faster than the next option.")
                elif dt < 0:
                    parts.append(f"{-dt}h slower than {fastest['persona']}.")
                if dc > 0:
                    parts.append(f"${dc:,.0f} cheaper than the lowest-cost alternative.")
                elif dc < 0:
                    parts.append(f"costs ${-dc:,.0f} more than {cheapest['persona']}.")
                avoided = sorted({p for o in others for p in o["chokepoints"]} - set(c["chokepoints"]))
                if avoided:
                    parts.append(f"Avoids {', '.join(a.replace('CHOKE-', '') for a in avoided)}.")
            if c["audit_trace"]["eta"]["scenario"] > 0:
                parts.append(f"Includes {c['audit_trace']['eta']['scenario']}h announced scenario delay.")
            c["explanation"] = " ".join(parts)
