"""
SQLite persistence for route history, monitored routes and alerts.

Everything the dashboard used to keep only in memory (and lose on restart) lives here. SQLite
keeps the prototype dependency-free; the schema is plain SQL so it ports to Postgres directly.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "data", "supplychainer.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    origin TEXT, destination TEXT,
    request TEXT NOT NULL,
    response TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watches (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id),
    persona TEXT NOT NULL,
    label TEXT,
    request TEXT NOT NULL,
    route TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    watch_id TEXT NOT NULL REFERENCES watches(id),
    signature TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_watch ON alerts(watch_id, acknowledged);
"""


class Store:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.getenv("SUPPLYCHAINER_DB", DEFAULT_DB)
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    # --------------------------------------------------------------------- runs
    def save_run(self, request: Dict[str, Any], response: Dict[str, Any]) -> str:
        run_id = uuid.uuid4().hex[:12]
        self._exec("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                   (run_id, time.time(), request.get("source"), request.get("destination"),
                    json.dumps(request), json.dumps(response)))
        return run_id

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        rows = self._all("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not rows:
            return None
        r = rows[0]
        return {"id": r["id"], "created_at": r["created_at"], "request": json.loads(r["request"]),
                "response": json.loads(r["response"])}

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        out = []
        for r in self._all("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)):
            resp = json.loads(r["response"])
            recs = resp.get("recommendations", [])
            out.append({
                "id": r["id"], "created_at": r["created_at"], "origin": r["origin"], "destination": r["destination"],
                "request": json.loads(r["request"]),
                "applied_scenarios": resp.get("applied_scenarios", []),
                "options": [{"persona": "/".join(c.get("personas", [c["persona"]])),
                             "eta_p50": c["eta_band"]["p50"], "total_cost": c["total_cost"],
                             "threat_level": c["threat_level"], "primary_mode": c["primary_mode"]} for c in recs],
            })
        return out

    # ------------------------------------------------------------------ watches
    def add_watch(self, run_id: str, persona: str, label: Optional[str], request: Dict[str, Any],
                  route: Dict[str, Any]) -> str:
        watch_id = uuid.uuid4().hex[:12]
        self._exec("INSERT INTO watches VALUES (?,?,?,?,?,?,?,1)",
                   (watch_id, time.time(), run_id, persona, label, json.dumps(request), json.dumps(route)))
        return watch_id

    def list_watches(self, active_only: bool = True) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM watches" + (" WHERE active = 1" if active_only else "") + " ORDER BY created_at DESC"
        return [{"id": r["id"], "created_at": r["created_at"], "run_id": r["run_id"], "persona": r["persona"],
                 "label": r["label"], "request": json.loads(r["request"]), "route": json.loads(r["route"]),
                 "active": bool(r["active"])} for r in self._all(sql)]

    def deactivate_watch(self, watch_id: str) -> bool:
        return self._exec("UPDATE watches SET active = 0 WHERE id = ?", (watch_id,)).rowcount > 0

    # ------------------------------------------------------------------- alerts
    def open_alert_exists(self, watch_id: str, signature: str) -> bool:
        return bool(self._all("SELECT 1 FROM alerts WHERE watch_id = ? AND signature = ? AND acknowledged = 0",
                              (watch_id, signature)))

    def add_alert(self, watch_id: str, signature: str, severity: str, message: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        alert = {"id": uuid.uuid4().hex[:12], "created_at": time.time(), "watch_id": watch_id,
                 "signature": signature, "severity": severity, "message": message, "payload": payload,
                 "acknowledged": False}
        self._exec("INSERT INTO alerts VALUES (?,?,?,?,?,?,?,0)",
                   (alert["id"], alert["created_at"], watch_id, signature, severity, message, json.dumps(payload)))
        return alert

    def list_alerts(self, include_acknowledged: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM alerts" + ("" if include_acknowledged else " WHERE acknowledged = 0") \
              + " ORDER BY created_at DESC LIMIT ?"
        return [{"id": r["id"], "created_at": r["created_at"], "watch_id": r["watch_id"], "signature": r["signature"],
                 "severity": r["severity"], "message": r["message"], "payload": json.loads(r["payload"]),
                 "acknowledged": bool(r["acknowledged"])} for r in self._all(sql, (limit,))]

    def acknowledge_alert(self, alert_id: str) -> bool:
        return self._exec("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,)).rowcount > 0
