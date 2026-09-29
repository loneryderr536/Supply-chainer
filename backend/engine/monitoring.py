"""
Route monitoring: re-evaluates saved routes whenever the live picture changes (a scenario is
activated or new intelligence arrives) and raises alerts with a concrete alternative.
"""
import json
import os
import threading
from typing import Any, Callable, Dict, List, Optional

import requests

from .store import Store

EXPORT_SCHEMA = "supplychainer.shipment_plan.v1"
ALERT_SCHEMA = "supplychainer.route_alert.v1"


def _summary(c: Dict[str, Any]) -> Dict[str, Any]:
    return {"persona": "/".join(c.get("personas", [c["persona"]])), "primary_mode": c["primary_mode"],
            "chokepoints": c["chokepoints"], "adjusted_eta": c["adjusted_eta"], "eta_band": c["eta_band"],
            "total_cost": c["total_cost"], "landed_cost": c.get("landed_cost", c["total_cost"]),
            "threat_level": c["threat_level"]}


class RouteMonitor:
    def __init__(self, store: Store, recommender, webhook_url: Optional[str] = None,
                 on_alert: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.store = store
        self.recommender = recommender
        self.webhook_url = webhook_url if webhook_url is not None else os.getenv("ALERT_WEBHOOK_URL")
        self.on_alert = on_alert
        self.webhook_log: List[Dict[str, Any]] = []

    def watch(self, run_id: str, persona: str, label: Optional[str] = None) -> Dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise KeyError(f"run {run_id} not found")
        route = next((c for c in run["response"].get("recommendations", [])
                      if persona in c.get("personas", [c["persona"]])), None)
        if route is None:
            raise KeyError(f"persona {persona} not in run {run_id}")
        watch_id = self.store.add_watch(run_id, persona, label, run["request"], route)
        return {"id": watch_id, "run_id": run_id, "persona": persona, "label": label, "route": _summary(route)}

    def evaluate_all(self, trigger: str) -> List[Dict[str, Any]]:
        """Re-price every active watch; returns the alerts that were newly raised."""
        raised = []
        for w in self.store.list_watches():
            alert = self._evaluate(w, trigger)
            if alert:
                raised.append(alert)
        return raised

    def _evaluate(self, w: Dict[str, Any], trigger: str) -> Optional[Dict[str, Any]]:
        baseline = w["route"]
        now = self.recommender.evaluate_path(baseline["path_nodes"], w["persona"])
        if "error" in now:
            return None
        affected = now["affected_hubs"]
        if not affected:
            return None

        eta_delta = round(now["eta_band"]["p50"] - baseline["eta_band"]["p50"], 1)
        blocked = now["blocked_at"]
        if blocked:
            severity = "CRITICAL"
        elif eta_delta >= 48 or now["threat_level"] >= 0.8:
            severity = "HIGH"
        elif eta_delta >= 12 or now["threat_level"] >= 0.5:
            severity = "MEDIUM"
        else:
            return None  # touched by a signal, but not operationally material

        signature = json.dumps({"hubs": affected, "scenarios": sorted(now["applied_scenarios"]),
                                "blocked": blocked}, sort_keys=True)
        if self.store.open_alert_exists(w["id"], signature):
            return None

        req = w["request"]
        alt = self.recommender.recommend(
            source=req["source"], destination=req["destination"],
            transport_preference=req.get("transport_preference", "any"),
            routing_policy=req.get("routing_policy", "STRICT"), cargo_type=req.get("cargo_type", "general"),
            priority=req.get("priority", "normal"), overrides=req.get("overrides"),
            cargo_value_usd=req.get("cargo_value_usd", 0.0), carrying_cost_rate=req.get("carrying_cost_rate", 0.25))
        alternative = None
        if "recommendations" in alt:
            same = [c for c in alt["recommendations"] if w["persona"] in c["personas"]]
            best = (same or alt["recommendations"])[0]
            alternative = {**_summary(best), "legs": [l["to"] for l in best["legs"] if l["type"] != "transfer"],
                           "eta_saving_vs_current_h": round(now["eta_band"]["p50"] - best["eta_band"]["p50"], 1)
                           if not blocked else None}

        route_name = w["label"] or f"{req['source']} -> {req['destination']} ({w['persona']})"
        if blocked:
            message = f"{route_name}: route is impassable at {', '.join(blocked)}."
        else:
            message = f"{route_name}: expected arrival slips {eta_delta:+}h (p50) at {', '.join(affected)}."
        if alternative and (blocked or (alternative["eta_saving_vs_current_h"] or 0) > 0):
            message += f" Suggested reroute via {', '.join(alternative['chokepoints']) or alternative['primary_mode']}" \
                       f" (p50 {alternative['eta_band']['p50']}h, ${alternative['total_cost']:,.0f})."

        payload = {
            "schema": ALERT_SCHEMA, "trigger": trigger, "watch_id": w["id"], "run_id": w["run_id"],
            "route_label": route_name, "affected_hubs": affected, "blocked_at": blocked,
            "applied_scenarios": now["applied_scenarios"],
            "baseline": _summary(baseline), "current": _summary(now),
            "eta_delta_p50_h": eta_delta, "alternative": alternative,
        }
        alert = self.store.add_alert(w["id"], signature, severity, message, payload)
        self._dispatch(alert)
        return alert

    def _dispatch(self, alert: Dict[str, Any]):
        if self.on_alert:
            self.on_alert(alert)
        if self.webhook_url:
            threading.Thread(target=self._post_webhook, args=(alert,), daemon=True).start()

    def _post_webhook(self, alert: Dict[str, Any]):
        try:
            r = requests.post(self.webhook_url, json=alert, timeout=5)
            self.webhook_log.append({"alert_id": alert["id"], "status": r.status_code})
        except Exception as e:
            self.webhook_log.append({"alert_id": alert["id"], "error": str(e)})


def shipment_plan(run: Dict[str, Any], persona: Optional[str] = None) -> Dict[str, Any]:
    """
    TMS/ERP-friendly export of a recommendation run: one plan per option, each leg a movement
    with explicit mode, origin/destination facility codes, planned hours, cost and risk.
    """
    resp, req = run["response"], run["request"]
    options = resp.get("recommendations", [])
    if persona:
        options = [c for c in options if persona in c.get("personas", [c["persona"]])]
    plans = []
    for c in options:
        clock = 0.0
        movements = []
        for i, l in enumerate(c["legs"], start=1):
            movements.append({
                "sequence": i, "movement_type": "HANDLING" if l["type"] == "transfer" else "TRANSPORT",
                "mode": l["mode"], "origin_facility": l["from"], "destination_facility": l["to"],
                "destination_name": l["to_name"], "distance_km": l["distance_km"],
                "planned_start_offset_h": round(clock, 1), "planned_duration_h": l["eta"],
                "delay_buffer_h": l["delay_band"], "cost_usd": l["cost"],
                "risk_score": l["threat"], "risk_category": l["threat_category"], "risk_source": l["intel_source"],
            })
            clock += l["eta"]
        plans.append({
            "option": "/".join(c.get("personas", [c["persona"]])), "primary_mode": c["primary_mode"],
            "planned_transit_h": c["adjusted_eta"], "eta_quantiles_h": c["eta_band"],
            "total_cost_usd": c["total_cost"], "inventory_carrying_cost_usd": c.get("inventory_cost", {}).get("p50", 0.0),
            "landed_cost_usd": c.get("landed_cost", c["total_cost"]),
            "max_risk_score": c["threat_level"], "movements": movements,
        })
    return {
        "schema": EXPORT_SCHEMA, "run_id": run["id"], "generated_at": run["created_at"],
        "origin": resp.get("origin_hub"), "destination": resp.get("destination_hub"),
        "request": req, "applied_scenarios": resp.get("applied_scenarios", []),
        "hold_option": resp.get("hold_option"), "plans": plans,
    }


CSV_COLUMNS = ["run_id", "option", "sequence", "type", "mode", "from", "to", "to_name", "distance_km",
               "eta_h", "delay_p50_h", "delay_p85_h", "delay_p95_h", "cost_usd", "threat", "threat_category",
               "intel_source", "reason"]


def audit_rows(run: Dict[str, Any]) -> List[List[Any]]:
    rows = []
    for c in run["response"].get("recommendations", []):
        option = "/".join(c.get("personas", [c["persona"]]))
        for i, l in enumerate(c["legs"], start=1):
            rows.append([run["id"], option, i, l["type"], l["mode"], l["from"], l["to"], l["to_name"],
                         l["distance_km"], l["eta"], l["delay_band"]["p50"], l["delay_band"]["p85"],
                         l["delay_band"]["p95"], l["cost"], l["threat"], l["threat_category"] or "",
                         l["intel_source"], l["reason"]])
        rows.append([run["id"], option, "TOTAL", "", c["primary_mode"], "", "", "", "",
                     c["adjusted_eta"], c["eta_band"]["p50"], c["eta_band"]["p85"], c["eta_band"]["p95"],
                     c["total_cost"], c["threat_level"], "", "", c.get("explanation", "")])
    return rows
