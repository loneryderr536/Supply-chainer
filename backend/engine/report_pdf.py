"""
Boardroom PDF report for a saved recommendation run.

One decision page (recommendation, hold-vs-reroute, option comparison, ETA bands, route map)
followed by leg-level detail and the model's explanation for each option.
"""
import io
import json
import os
import time
from typing import Any, Dict, List, Optional

from reportlab.graphics.shapes import Drawing, Line, Rect, String, Polygon, Circle
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from .fx import FXRates

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
RULE = colors.HexColor("#cbd5e1")
BAND = colors.HexColor("#f1f5f9")
ACCENT = colors.HexColor("#2563eb")
MODE_COLORS = {"SEA": colors.HexColor("#2563eb"), "AIR": colors.HexColor("#d97706"),
               "RAIL": colors.HexColor("#059669"), "ROAD": colors.HexColor("#ea580c")}
PERSONA_COLORS = {"FASTEST": colors.HexColor("#d97706"), "SAFEST": colors.HexColor("#059669"),
                  "BALANCED": colors.HexColor("#2563eb")}
# Natural Earth 1:110m land (public domain) in TopoJSON, as packaged by world-atlas.
LAND_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "land-110m.json")

_styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=_styles["Heading1"], fontSize=18, textColor=INK, spaceAfter=2)
H2 = ParagraphStyle("H2", parent=_styles["Heading2"], fontSize=12, textColor=INK, spaceBefore=8, spaceAfter=4)
BODY = ParagraphStyle("Body", parent=_styles["BodyText"], fontSize=8.5, leading=11, textColor=INK)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=7, leading=9, textColor=MUTED)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=7, leading=8.5, alignment=TA_LEFT)


def _label(c: Dict[str, Any]) -> str:
    return " / ".join(c.get("personas", [c["persona"]]))


def _via(c: Dict[str, Any]) -> str:
    return ", ".join(n.replace("CHOKE-", "").title() for n in c.get("chokepoints", [])) or "direct"


def _land_rings() -> List[List[tuple]]:
    """Decode world-atlas TopoJSON land polygons (quantized, delta-encoded arcs) into lon/lat rings."""
    try:
        with open(LAND_PATH) as f:
            topo = json.load(f)
    except OSError:
        return []
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(indices):
        out = []
        for i in indices:
            pts = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
            out.extend(pts if not out else pts[1:])
        return out

    rings = []
    for geom in topo["objects"]["land"]["geometries"]:
        polys = geom["arcs"] if geom["type"] == "MultiPolygon" else [geom["arcs"]]
        for poly in polys:
            rings.extend(split_antimeridian(ring(poly[0])))
    return rings


def split_antimeridian(ring: List[tuple]) -> List[List[tuple]]:
    """Cut a lon/lat ring wherever it jumps across ±180°, so it isn't drawn as a line across the map."""
    pieces, cur = [], [ring[0]]
    for a, b in zip(ring, ring[1:]):
        if abs(b[0] - a[0]) > 180:
            pieces.append(cur)
            cur = []
        cur.append(b)
    pieces.append(cur)
    return [p for p in pieces if len(p) >= 3]


def _map(recs: List[Dict[str, Any]], width: float, height: float) -> Drawing:
    """Equirectangular sketch cropped to the routes, with coastlines when available."""
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=colors.HexColor("#eef2f7"), strokeColor=RULE))
    pts = [p for c in recs for l in c["legs"] for p in (l["from_coords"], l["to_coords"]) if None not in p]
    if not pts:
        return d
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    pad = 8
    lat0, lat1 = max(-85, min(lats) - pad), min(85, max(lats) + pad)
    lon0, lon1 = max(-180, min(lons) - pad), min(180, max(lons) + pad)
    # keep the aspect ratio of the drawing
    span_lon, span_lat = lon1 - lon0, lat1 - lat0
    if span_lon / span_lat > width / height:
        extra = span_lon * height / width - span_lat
        lat0, lat1 = lat0 - extra / 2, lat1 + extra / 2
    else:
        extra = span_lat * width / height - span_lon
        lon0, lon1 = lon0 - extra / 2, lon1 + extra / 2

    def xy(lat, lon):
        return (lon - lon0) / (lon1 - lon0) * width, (lat - lat0) / (lat1 - lat0) * height

    for ring in _land_rings():
        if max(lat for _, lat in ring) < -55:
            continue  # Antarctica wraps the whole globe and draws a false horizon line
        if not any(lon0 - 30 <= lon <= lon1 + 30 and lat0 - 30 <= lat <= lat1 + 30 for lon, lat in ring[::5]):
            continue
        flat = []
        for lon, lat in ring:
            x, y = xy(lat, lon)
            flat.extend([min(max(x, -5), width + 5), min(max(y, -5), height + 5)])
        d.add(Polygon(flat, strokeColor=colors.HexColor("#94a3b8"), strokeWidth=0.4,
                      fillColor=colors.HexColor("#dde3ea")))
    for idx, c in enumerate(recs):
        for l in c["legs"]:
            if l["type"] == "transfer":
                continue
            x0, y0 = xy(*l["from_coords"])
            x1, y1 = xy(*l["to_coords"])
            d.add(Line(x0, y0, x1, y1, strokeColor=MODE_COLORS.get(l["mode"], MUTED),
                       strokeWidth=2.2 if idx == 0 else 1.0, strokeDashArray=None if idx == 0 else [3, 2]))
    first, last = recs[0]["legs"][0], recs[0]["legs"][-1]
    for (lat, lon), col, name in ((first["from_coords"], colors.HexColor("#16a34a"), first["from_name"]),
                                  (last["to_coords"], colors.HexColor("#dc2626"), last["to_name"])):
        x, y = xy(lat, lon)
        d.add(Circle(x, y, 3, fillColor=col, strokeColor=colors.white))
        label = (name or "")[:28]
        anchor = "end" if x > width * 0.7 else "start"
        d.add(String(x - 5 if anchor == "end" else x + 5, y + 4, label, fontSize=6, fontName="Helvetica",
                     fillColor=INK, textAnchor=anchor))
    return d


def _band_chart(recs: List[Dict[str, Any]], width: float) -> Drawing:
    row, left = 16, 125
    h = row * len(recs) + 22
    d = Drawing(width, h)
    lo = min(c["adjusted_eta"] for c in recs)
    hi = max(c["eta_band"]["p95_correlated"] for c in recs)
    span = max(hi - lo, 1.0)
    scale = lambda v: left + (v - lo) / span * (width - left - 10)
    for i, c in enumerate(recs):
        y = h - 14 - i * row
        b = c["eta_band"]
        col = PERSONA_COLORS.get(c["personas"][0], ACCENT)
        d.add(String(0, y - 3, _label(c), fontSize=6.5, fontName="Helvetica", fillColor=INK))
        d.add(Line(scale(c["adjusted_eta"]), y, scale(b["p95_correlated"]), y, strokeColor=RULE, strokeWidth=1))
        d.add(Rect(scale(b["p50"]), y - 4, max(scale(b["p95"]) - scale(b["p50"]), 1.5), 8, fillColor=col,
                   strokeColor=None, fillOpacity=0.55))
        d.add(Line(scale(b["p85"]), y - 6, scale(b["p85"]), y + 6, strokeColor=INK, strokeWidth=1.2))
    for v in (lo, lo + span / 2, hi):
        d.add(String(scale(v) - 10, 0, f"{v:,.0f}h", fontSize=6, fontName="Helvetica", fillColor=MUTED))
    return d


def _shapley(dominant: Dict[str, Any], width: float) -> Optional[Drawing]:
    att = (dominant or {}).get("attribution") or {}
    contrib = att.get("contributions_h")
    if not contrib:
        return None
    row, left = 11, 100
    h = row * len(contrib) + 4
    d = Drawing(width, h)
    top = max(abs(v) for v in contrib.values()) or 1
    mid = left + (width - left - 60) / 2
    half = (width - left - 60) / 2
    for i, (k, v) in enumerate(contrib.items()):
        y = h - (i + 1) * row
        d.add(String(0, y + 1, k.replace("_", " "), fontSize=7, fontName="Helvetica", fillColor=INK))
        w = abs(v) / top * half
        d.add(Rect(mid if v >= 0 else mid - w, y, w, 7, strokeColor=None,
                   fillColor=colors.HexColor("#dc2626") if v >= 0 else colors.HexColor("#059669")))
        d.add(String(width - 55, y + 1, f"{v:+.1f} h", fontSize=7, fontName="Helvetica", fillColor=INK))
    d.add(Line(mid, 0, mid, h, strokeColor=RULE))
    return d


def _explanation(c: Dict[str, Any], money) -> str:
    f = c.get("explanation_facts")
    if not f:
        return c.get("explanation", "")
    via = ", ".join(x.title() for x in f["chokepoints"]) or "no chokepoints"
    parts = [f"{f['primary_mode']} via {via}; expected {f['p50_h']:,.1f} h (p85 {f['p85_h']:,.1f} h, "
             f"p95 {f['p95_h']:,.1f} h), {money(f['cost_usd'])} {f['cost_basis']}, {f['transfers']} handoffs."]
    if f["faster_by_h"]:
        parts.append(f"{f['faster_by_h']:,.1f} h faster than the next option.")
    if f["slower_by_h"]:
        parts.append(f"{f['slower_by_h']:,.1f} h slower than {f['slower_than']}.")
    if f["cheaper_by_usd"]:
        parts.append(f"{money(f['cheaper_by_usd'])} cheaper ({f['cost_basis']}) than the lowest-cost alternative.")
    if f["costlier_by_usd"]:
        parts.append(f"{money(f['costlier_by_usd'])} more ({f['cost_basis']}) than {f['costlier_than']}.")
    if f["avoids"]:
        parts.append(f"Avoids {', '.join(x.title() for x in f['avoids'])}.")
    if f["scenario_delay_h"]:
        parts.append(f"Includes {f['scenario_delay_h']:,.1f} h announced scenario delay.")
    return " ".join(parts)


def _table(rows, widths, header_bg=INK):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_bg), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    return t


def build_report(run: Dict[str, Any], currency: str = "USD", fx: Optional[FXRates] = None) -> bytes:
    fx = fx or FXRates()
    # Built-in PDF fonts cover $, €, £ and ¥ but not ₹, so fall back to the ISO code where needed.
    money = lambda usd: fx.format(usd, currency, ascii_safe=True)
    resp, req = run["response"], run["request"]
    recs = resp.get("recommendations", [])
    if not recs:
        raise ValueError("run has no recommendations")
    with_value = any(c.get("inventory_cost", {}).get("per_hour", 0) > 0 for c in recs)
    best = next((c for c in recs if "BALANCED" in c.get("personas", [])), recs[0])

    buf = io.BytesIO()
    page = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=f"Supplychainer route report {run['id']}",
                            author="Supplychainer")
    W = page[0] - 28 * mm
    rates = fx.rates()

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(14 * mm, 7 * mm, f"Supplychainer · run {run['id']} · generated "
                                           f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · "
                                           f"amounts in {currency} ({rates['source']})")
        canvas.drawRightString(page[0] - 14 * mm, 7 * mm, f"Page {_doc.page}")
        canvas.restoreState()

    story = []
    start = recs[0]["legs"][0].get("from_name") or resp.get("origin")
    end = recs[0]["legs"][-1].get("to_name") or resp.get("destination")
    story.append(Paragraph(f"Route decision: {start} → {end}", H1))
    scen = ", ".join(resp.get("applied_scenarios") or []) or "none"
    ctx = (f"{resp.get('origin_hub')} → {resp.get('destination_hub')} · mode {req.get('transport_preference', 'any')} "
           f"({req.get('routing_policy', 'STRICT')}) · cargo {req.get('cargo_type', 'general')} · "
           f"priority {req.get('priority', 'normal')} · scenarios: {scen} · planned "
           f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(run['created_at']))}")
    if with_value:
        ctx += (f" · cargo value {money(req.get('cargo_value_usd', 0))} at "
                f"{req.get('carrying_cost_rate', 0.25) * 100:.0f}%/yr carrying cost")
    story.append(Paragraph(ctx, SMALL))
    story.append(Spacer(1, 6))

    # --- recommendation box
    b = best["eta_band"]
    cost_line = f"freight {money(best['total_cost'])}"
    if with_value:
        cost_line += f", landed {money(best['landed_cost'])} incl. {money(best['inventory_cost']['p50'])} carrying cost"
    rec_text = (f"<b>Recommendation: {_label(best)}</b>, {best['primary_mode']} via {_via(best)}. "
                f"Expected arrival in <b>{b['p50']:,.0f} h</b> ({b['p50'] / 24:.1f} days); plan for "
                f"{b['p85']:,.0f} h (p85), worst case {b['p95']:,.0f} h (p95). Cost: {cost_line}. "
                f"Peak leg risk {best['threat_level'] * 100:.0f}%.")
    hold = resp.get("hold_option")
    if hold:
        rec_text += (f"<br/><b>Hold vs reroute:</b> waiting at {', '.join(hold['waits_at'])} for reopening would "
                     f"arrive at p50 {hold['eta_band']['p50']:,.0f} h, {abs(hold['delta_vs_best_reroute_h']):,.0f} h "
                     f"{'later' if hold['delta_vs_best_reroute_h'] > 0 else 'sooner'} than the best reroute "
                     f"→ <b>{hold['verdict']}</b>.")
    box = Table([[Paragraph(rec_text, BODY)]], colWidths=[W])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eff6ff")),
                             ("BOX", (0, 0), (-1, -1), 0.8, ACCENT), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                             ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(box)

    # --- comparison table + bands | map
    header = ["Option", "Mode", "Via", "Plan h", "p50 h", "p85 h", "p95 h", "Freight"] + (["Landed"] if with_value else []) + ["Risk"]
    rows = [header]
    for c in recs:
        e = c["eta_band"]
        rows.append([Paragraph(_label(c), CELL), c["primary_mode"], Paragraph(_via(c), CELL), f"{c['adjusted_eta']:,.0f}", f"{e['p50']:,.0f}",
                     f"{e['p85']:,.0f}", f"{e['p95']:,.0f}", money(c["total_cost"])] +
                    ([money(c["landed_cost"])] if with_value else []) + [f"{c['threat_level'] * 100:.0f}%"])
    left_w = W * 0.55
    base = [0.2, 0.08, 0.2, 0.08, 0.08, 0.08, 0.08, 0.12] + ([0.12] if with_value else []) + [0.06]
    widths = [left_w * x / sum(base) for x in base]
    left = [Paragraph("Options", H2), _table(rows, widths), Spacer(1, 6),
            Paragraph("ETA bands (bar p50–p95, tick p85, line plan → fully-correlated worst case)", SMALL),
            _band_chart(recs, left_w)]
    ordered = [best] + [c for c in recs if c is not best]  # recommended route drawn solid
    right = [Paragraph("Routes", H2), _map(ordered, W - left_w - 16, 190),
             Paragraph("Recommended route solid, alternatives dashed. Blue sea, amber air, green rail, orange road.", SMALL)]
    layout = Table([[left, right]], colWidths=[left_w + 10, W - left_w - 10])
    layout.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(layout)

    # --- detail per option
    for c in recs:
        story.append(PageBreak())
        e = c["eta_band"]
        story.append(Paragraph(f"{_label(c)}: {c['primary_mode']} via {_via(c)}", H2))
        story.append(Paragraph(_explanation(c, money), BODY))
        story.append(Spacer(1, 4))
        rows = [["#", "Mode", "To", "km", "Hours", "Delay p85", "Cost", "Risk", "Signal"]]
        for i, l in enumerate(c["legs"], 1):
            signal = "" if l["intel_source"] == "NO_SIGNAL" else f"{l['intel_source']}: {l['reason']}"
            rows.append([str(i), "handoff" if l["type"] == "transfer" else l["mode"], Paragraph(l["to_name"] or l["to"], CELL),
                         f"{l['distance_km']:,.0f}" if l["type"] != "transfer" else "", f"{l['eta']:,.1f}",
                         f"+{l['delay_band']['p85']:,.1f}", money(l["cost"]), f"{l['threat'] * 100:.0f}%",
                         Paragraph(signal[:160], CELL)])
        story.append(_table(rows, [W * x for x in (0.03, 0.07, 0.2, 0.06, 0.06, 0.07, 0.09, 0.05, 0.37)]))
        tr = c["audit_trace"]
        facts = (f"Transit {tr['eta']['transit']:,.1f} h + transfers {tr['eta']['transfer']:,.1f} h + announced "
                 f"scenario delay {tr['eta']['scenario']:,.1f} h = plan {c['adjusted_eta']:,.1f} h. Model delay buffer "
                 f"p50/p85/p95: +{tr['ml']['buffer_h']['p50']:,.1f} / +{tr['ml']['buffer_h']['p85']:,.1f} / "
                 f"+{tr['ml']['buffer_h']['p95']:,.1f} h. Route band p50 {e['p50']:,.1f} h, p85 {e['p85']:,.1f} h, "
                 f"p95 {e['p95']:,.1f} h (independent legs); {e['p95_correlated']:,.1f} h if all legs are late together. "
                 f"Costs: linehaul {money(tr['cost']['transit'])}, transfers {money(tr['cost']['transfer'])}, "
                 f"disruption surcharge {money(tr['cost']['scenario'])}.")
        if with_value:
            facts += (f" Inventory carrying cost {money(c['inventory_cost']['p50'])} expected "
                      f"({money(c['inventory_cost']['p95'])} at p95); landed {money(c['landed_cost'])}.")
        story.append(Spacer(1, 4))
        story.append(Paragraph(facts, BODY))
        dom = tr["ml"].get("dominant_leg")
        chart = _shapley(dom, W * 0.5)
        if chart:
            story.append(KeepTogether([
                Paragraph(f"Why the slowest leg ({dom['from']} → {dom['to']}, p85 {dom['p85_delay_h']:,.1f} h) is slow: "
                          f"exact Shapley attribution of the p85 delay model, hours relative to a "
                          f"{dom['attribution']['base_value_h']:,.1f} h baseline", SMALL), chart]))

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Method: legs are priced with gradient-boosted quantile models (p50/p85/p95) trained on synthetic delays "
        "fitted to published UNCTAD, World Bank and STB statistics; FASTEST plans on p50, BALANCED on p85 "
        "(plus cargo carrying cost when a value is given), SAFEST on p95 plus 240 risk-hours per unit of threat. "
        "Route bands assume independent legs. Sea distances are great-circle between chokepoint waypoints. "
        f"Currency conversion: {rates['source']}{' as of ' + rates['as_of'] if rates.get('as_of') else ''}.", SMALL))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
