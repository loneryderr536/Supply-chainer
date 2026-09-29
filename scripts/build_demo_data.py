"""
Record real engine output for the static GitHub Pages demo.

Runs the actual backend in-process (models, live GDACS + news scan), sends a fixed set of demo
requests, and saves every response the dashboard needs as static files under
frontend/public/demo/. The Pages build of the dashboard reads these files instead of calling a
server, so the demo shows genuine results without hosting Python.

    python scripts/build_demo_data.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "frontend", "public", "demo")
sys.path.insert(0, ROOT)
os.environ["SUPPLYCHAINER_DB"] = os.path.join(tempfile.mkdtemp(), "demo.db")  # never touch the real DB

CURRENCIES = ["USD", "EUR", "GBP", "INR", "CNY", "JPY", "AED", "SGD"]

# (label, request). Each request is recorded exactly as the dashboard would send it.
PRESETS = [
    ("Shanghai → Rotterdam · sea · Suez Canal blocked",
     {"source": "Shanghai", "destination": "Rotterdam", "transport_preference": "sea", "scenario": "SUEZ_BLOCK"}),
    ("Shanghai → Rotterdam · sea · normal",
     {"source": "Shanghai", "destination": "Rotterdam", "transport_preference": "sea"}),
    ("Shanghai → Rotterdam · sea · Red Sea escalation",
     {"source": "Shanghai", "destination": "Rotterdam", "transport_preference": "sea", "scenario": "RED_SEA_CONFLICT"}),
    ("Shanghai → Rotterdam · any mode · normal",
     {"source": "Shanghai", "destination": "Rotterdam", "transport_preference": "any"}),
    ("Shanghai → Rotterdam · any mode · $5M cargo",
     {"source": "Shanghai", "destination": "Rotterdam", "transport_preference": "any", "cargo_value_usd": 5_000_000}),
    ("Shanghai → Rotterdam · any mode · $10M cargo",
     {"source": "Shanghai", "destination": "Rotterdam", "transport_preference": "any", "cargo_value_usd": 10_000_000}),
    ("Jebel Ali → Singapore · sea · Hormuz closed",
     {"source": "PORT-JEBEL", "destination": "PORT-SINGAPORE", "transport_preference": "sea", "scenario": "HORMUZ_CLOSURE"}),
    ("Jebel Ali → Singapore · sea · normal",
     {"source": "PORT-JEBEL", "destination": "PORT-SINGAPORE", "transport_preference": "sea"}),
    ("Shanghai → Los Angeles · sea · LA port strike",
     {"source": "PORT-SHANGHAI", "destination": "PORT-LOSANGELES", "transport_preference": "sea", "scenario": "LA_PORT_STRIKE"}),
    ("Shanghai → Los Angeles · sea · normal",
     {"source": "PORT-SHANGHAI", "destination": "PORT-LOSANGELES", "transport_preference": "sea"}),
    ("Mumbai → Chennai · any mode · Chennai flooding",
     {"source": "Mumbai", "destination": "Chennai", "transport_preference": "any", "scenario": "CHENNAI_FLOOD"}),
    ("Mumbai → Chennai · any mode · normal",
     {"source": "Mumbai", "destination": "Chennai", "transport_preference": "any"}),
    ("Shanghai → Rotterdam · perishable cargo",
     {"source": "Shanghai", "destination": "Rotterdam", "transport_preference": "any", "cargo_type": "perishable_urgent"}),
]


def save(rel, data, binary=False):
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if binary:
        with open(path, "wb") as f:
            f.write(data)
    elif isinstance(data, str):
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def main():
    from fastapi.testclient import TestClient
    from backend import main as app_main

    shutil.rmtree(OUT, ignore_errors=True)
    with TestClient(app_main.app) as c:
        print("warming up the engine and scanning live feeds...")
        deadline = time.time() + 600
        while time.time() < deadline and not (app_main.recommender.is_warmed_up and app_main.intel.last_scan
                                              and app_main.gdacs.status.get("state") in ("ok", "error")):
            time.sleep(1)

        presets = []
        for label, body in PRESETS:
            full = {"routing_policy": "STRICT", "cargo_type": "general", "priority": "normal",
                    "carrying_cost_rate": 0.25, "cargo_value_usd": 0, **body}
            resp = c.post("/api/recommend", json=full).json()
            if "run_id" not in resp:
                print("  skipped (no route):", label, resp.get("error"))
                continue
            run_id = resp["run_id"]
            save(f"runs/{run_id}.json", c.get(f"/api/runs/{run_id}").json())
            save(f"exports/{run_id}.csv", c.get(f"/api/runs/{run_id}/export.csv").text)
            save(f"exports/{run_id}.json", c.get(f"/api/runs/{run_id}/export.json").json())
            for cur in CURRENCIES:
                save(f"pdf/{run_id}_{cur}.pdf", c.get(f"/api/runs/{run_id}/report.pdf?currency={cur}").content, binary=True)
            presets.append({"id": run_id, "label": label, "request": full})
            print("  recorded:", label)

        # Supplier rankings for every category x scenario (inventory advice is recomputed in the browser).
        categories = sorted({s["category"] for s in json.load(open(os.path.join(ROOT, "backend", "data", "suppliers.json")))})
        for cat in categories:
            for sc in [None] + [s["id"] for s in c.get("/api/scenarios").json()]:
                data = c.post("/api/suppliers", json={"category": cat, "scenario": sc}).json()
                save(f"suppliers/{cat}_{sc or 'NORMAL'}.json", data)

        save("network.json", c.get("/api/network").json())
        save("scenarios.json", c.get("/api/scenarios").json())
        save("intel.json", c.get("/api/intel").json())
        save("feeds.json", c.get("/api/feeds").json())
        save("fx.json", c.get("/api/fx").json())
        save("history.json", c.get("/api/history?limit=100").json())
        save("manifest.json", {"generated_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                               "presets": presets, "currencies": CURRENCIES})
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(OUT) for f in fs)
    print(f"done: {len(presets)} demo routes, {size / 1e6:.1f} MB in {OUT}")


if __name__ == "__main__":
    main()
