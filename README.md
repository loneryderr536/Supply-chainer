# Supplychainer

**Plan shipping routes that stay smart when the world gets messy.**

Supplychainer finds the best way to move cargo between two places, by ship, plane, train or truck.
Unlike a normal route planner, it also watches for trouble: a blocked canal, a port strike, a storm.
It then tells you which route to take, how long it will *really* take, and why.

🌐 **Live demo:** https://loneryderr536.github.io/Supply-chainer/
*(The online demo shows real results recorded from the engine. Pick a route from "Demo routes". Run it
locally for the full live engine.)*

---

## What it does

- **Gives you three choices.** Every request returns up to three routes: the **fastest**, the
  **safest**, and a **balanced** one that weighs time, cost and risk.
- **Shows a realistic arrival window.** Instead of one number, you get a likely time and a
  worst-case time, e.g. *"about 30 days, 31 at worst"*.
- **Reacts to disruptions.** Close the Suez Canal and it sends ships around Africa, and tells you
  whether waiting for the canal to reopen would be faster.
- **Reads the news.** It checks live headlines, official disaster alerts (GDACS), and optionally
  ship-tracking data (AIS) for problems along your route.
- **Explains itself.** Every number has a breakdown, including *why* a leg is expected to be slow.
- **Warns you later.** Save a route and you'll get an alert if something new affects it.
- **Speaks your language.** English, हिन्दी, Español and 中文, and costs in 8 currencies.
- **Makes reports.** One click gives a PDF for the boardroom, a CSV, or a file for logistics software.

---

## Screenshots

All screenshots are from the running app (see the [`screenshots/`](screenshots) folder).

<table>
  <tr>
    <td colspan="3" align="center">
      <img src="screenshots/01_suez_reroute.png" width="100%" alt="Suez Canal blocked: ships reroute around Africa"><br>
      <b>Suez Canal blocked</b>: ships reroute around Africa, and the app shows that waiting would be 212 hours slower
    </td>
  </tr>
  <tr>
    <td width="33%" align="center" valign="top">
      <img src="screenshots/02_audit_explainability.png" width="100%" alt="Audit panel"><br>
      <b>Explains every number</b>, including why a leg is slow
    </td>
    <td width="33%" align="center" valign="top">
      <img src="screenshots/04_monitoring_alert.png" width="100%" alt="Route alert"><br>
      <b>Instant alert</b> when a disruption hits a saved route
    </td>
    <td width="33%" align="center" valign="top">
      <img src="screenshots/05_live_intel.png" width="100%" alt="Live intel"><br>
      <b>Live data</b>: GDACS disaster alerts and news
    </td>
  </tr>
  <tr>
    <td colspan="3" align="center">
      <img src="screenshots/06_hindi_inr.png" width="100%" alt="Hindi interface with rupees"><br>
      <b>Your language, your currency</b>: the full dashboard in Hindi with prices in rupees
    </td>
  </tr>
  <tr>
    <td colspan="2" width="50%" align="center" valign="top">
      <img src="screenshots/07_pdf_report.png" width="100%" alt="Boardroom PDF report"><br>
      <b>One-click boardroom PDF</b>
    </td>
    <td width="50%" align="center" valign="top">
      <img src="screenshots/03_cargo_value.png" width="100%" alt="Cargo value comparison"><br>
      <b>Cargo value counts</b>: for $5M of goods, sea is $57k cheaper overall than air
    </td>
  </tr>
</table>

---

## How it works

1. **You ask for a route**, for example Shanghai → Rotterdam by sea.
2. **It builds a map of the world's transport network**: 440 real ports, airports, rail yards
   and warehouses, connected by sea lanes, flights, railways and roads. Ships have to pass through
   real chokepoints like Suez, Malacca and Gibraltar.
3. **It checks for trouble** from three sources:
   - *Scenarios* you switch on (e.g. "Suez Canal blocked")
   - *News headlines*, read by an AI language model that judges how serious each one is
   - *Official feeds*: GDACS disaster alerts and AIS ship positions
4. **A filter keeps only what matters.** A port strike affects ships, not trains, so train routes
   ignore it.
5. **A machine-learning model predicts delays** for every leg of the trip, as a likely case and a
   bad case.
6. **It finds the best routes** for each of the three styles (fastest, safest, balanced).
   If you enter the cargo's value, the balanced route also counts the cost of money tied up in
   transit, so expensive goods lean towards faster options.
7. **You get the answer**: routes on a map, arrival windows, costs, risks and a plain explanation.
   You can save it, monitor it, or export it.

### Flowchart

```mermaid
flowchart TD
    A[You: origin, destination, cargo] --> B[World transport network<br/>440 hubs · sea · air · rail · road]
    C[Scenarios<br/>e.g. Suez blocked] --> E
    D[News · GDACS alerts · AIS ships] --> F[AI reads & scores the news] --> G[Relevance filter<br/>keeps only what affects each mode] --> E
    B --> E[Delay prediction<br/>machine-learning model]
    E --> H[Route finder<br/>Fastest · Safest · Balanced]
    H --> I[Dashboard<br/>map · arrival window · cost · risk · explanation]
    I --> J[Save & monitor]
    I --> K[PDF · CSV · TMS export]
    J --> L[Alert if a new disruption hits your route]
```

---

## Tech stack

| Part | What we used |
|---|---|
| **Website (frontend)** | React, Vite, Leaflet (maps) |
| **Server (backend)** | Python, FastAPI |
| **Route finding** | NetworkX (graph search) |
| **Delay prediction** | scikit-learn (gradient boosting) |
| **Reading the news** | Sentence-Transformers (AI language model) |
| **Database** | SQLite by default, or PostgreSQL |
| **Reports** | ReportLab (PDF) |
| **Live data** | Google News, GDACS disaster alerts, aisstream.io (ships), open.er-api.com (exchange rates) |
| **Testing** | pytest (108 tests) |

---

## Run it yourself

You need **Python 3.11** and **Node.js 18+**. Run these from the project folder.

**1. Start the server**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --port 8000
```

**2. Start the website** (in a second terminal)

```bash
cd frontend
npm install
npm run dev
```

If it says *permission denied*, use `node node_modules/vite/bin/vite.js` instead of `npm run dev`.

**3. Open** http://localhost:5173

Optional extras:

- **Live ship tracking:** get a free key at aisstream.io and start the server with
  `AISSTREAM_API_KEY=your-key`.
- **PostgreSQL:** start the server with `DATABASE_URL=postgresql://user:pass@localhost/supplychainer`.
- **Run the tests:** `pytest`
- **Refresh the online demo:** `python scripts/build_demo_data.py`, then push. GitHub Pages rebuilds automatically.

---

## What we fixed

The original prototype ran without errors but gave wrong answers. The main problems we found and fixed:

- **Ships sailed straight through the blocked Suez Canal.** Now they reroute, and the app compares
  waiting against rerouting.
- **Serious disasters were scored as zero risk** because the news scoring was inverted.
- **The relevance filter was backwards**: shipping news was ignored for ships.
- **The delay-prediction model was loaded but never used.** Now it prices every leg.
- **Most sea lanes only went one way**, so the Red Sea and Hormuz scenarios changed nothing.
- **The data had impossible routes**, like trains across the Red Sea and a road across the Strait
  of Gibraltar. 26 were removed.
- **The explanations contained made-up numbers** (e.g. "reduces cost by 396%"). Now they only use
  real figures.

---

## Good to know

- The delay model was trained on data for 16 major hubs; for other places it uses an average for
  that transport type.
- News scoring isn't perfect, so unconfirmed headlines count at half weight and can be dismissed.
- Live ship tracking needs a free aisstream.io key.

---

## Demo video

▶️ **Watch the demo:** [VIDEO LINK HERE](#)

---

*Built for the TatHack prelim challenge, in association with Arvind and the TatHack team. Licensed under Apache 2.0.*
