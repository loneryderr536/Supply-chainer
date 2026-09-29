import React, { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { feature } from 'topojson-client';
import land110m from 'world-atlas/land-110m.json';
import { MODE_COLORS } from './api.js';

// Keep a leg on the short side of the antimeridian (e.g. Shanghai -> Los Angeles crosses the Pacific).
function unwrap(from, to) {
  let [lat2, lon2] = to;
  const lon1 = from[1];
  if (lon2 - lon1 > 180) lon2 -= 360;
  if (lon1 - lon2 > 180) lon2 += 360;
  return [from, [lat2, lon2]];
}

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export default function RouteMap({ network, recommendations, selected, disruptedHubs, closedHubs, intelHubs }) {
  const el = useRef(null);
  const map = useRef(null);
  const contextLayer = useRef(null);
  const routeLayer = useRef(null);

  useEffect(() => {
    map.current = L.map(el.current, { worldCopyJump: true, zoomControl: true, attributionControl: true })
      .setView([22, 60], 2);
    // Bundled coastline so the map stays readable without internet access; online tiles draw on top.
    map.current.createPane('land').style.zIndex = 150;
    const land = feature(land110m, land110m.objects.land);
    [-360, 0, 360].forEach((off) => L.geoJSON(land, {
      pane: 'land', interactive: false,
      style: { color: '#334155', weight: 0.6, fillColor: '#1e293b', fillOpacity: 1 },
      coordsToLatLng: (c) => L.latLng(c[1], c[0] + off),
    }).addTo(map.current));
    L.tileLayer('https://{s}.basemap.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
      subdomains: 'abcd',
      maxZoom: 10,
    }).addTo(map.current);
    contextLayer.current = L.layerGroup().addTo(map.current);
    routeLayer.current = L.layerGroup().addTo(map.current);
    return () => map.current.remove();
  }, []);

  // Context: chokepoints, coloured by the live picture.
  useEffect(() => {
    const layer = contextLayer.current;
    layer.clearLayers();
    (network?.nodes || []).forEach((n) => {
      const closed = closedHubs.has(n.id);
      const disrupted = disruptedHubs.has(n.id);
      const intel = intelHubs.get(n.id);
      if (n.type !== 'choke_point' && !disrupted && !intel) return;
      const color = closed ? '#ef4444' : disrupted ? '#f97316' : intel ? '#eab308' : '#64748b';
      const marker = L.circleMarker([n.lat, n.lon], {
        radius: closed || disrupted || intel ? 7 : 4, color, weight: 2, fillColor: color,
        fillOpacity: closed ? 0.9 : 0.5, className: closed || disrupted ? 'pulse-marker' : '',
      });
      const status = closed ? 'CLOSED' : disrupted ? 'DISRUPTED' : intel ? `LIVE INTEL ${Math.round(intel.score * 100)}%` : 'open';
      marker.bindTooltip(`<b>${esc(n.display_name)}</b><br/>${status}${intel ? `<br/><i>${esc(intel.headline)}</i>` : ''}`);
      marker.addTo(layer);
    });
  }, [network, disruptedHubs, closedHubs, intelHubs]);

  // Routes: every option faint, the selected one bold, legs coloured by mode.
  useEffect(() => {
    const layer = routeLayer.current;
    layer.clearLayers();
    if (!recommendations.length) return;
    const bounds = [];
    recommendations.forEach((rec, idx) => {
      const isSel = idx === selected;
      rec.legs.filter((l) => l.type !== 'transfer').forEach((leg) => {
        const pts = unwrap(leg.from_coords, leg.to_coords);
        const line = L.polyline(pts, {
          color: MODE_COLORS[leg.mode] || '#94a3b8',
          weight: isSel ? 4 : 2,
          opacity: isSel ? 0.95 : 0.3,
          dashArray: isSel ? null : '4 6',
        });
        if (isSel) {
          line.bindTooltip(
            `<b>${esc(leg.mode)}</b> → ${esc(leg.to_name)}<br/>${leg.eta}h · $${leg.cost.toLocaleString()}` +
            `<br/>delay p85 +${leg.delay_band.p85}h · threat ${Math.round(leg.threat * 100)}%` +
            (leg.intel_source !== 'NO_SIGNAL' ? `<br/><i>${esc(leg.reason)}</i>` : ''), { sticky: true });
          bounds.push(...pts);
        }
        line.addTo(layer);
      });
      if (isSel) {
        const first = rec.legs[0];
        const last = rec.legs[rec.legs.length - 1];
        L.circleMarker(first.from_coords, { radius: 6, color: '#fff', fillColor: '#22c55e', fillOpacity: 1, weight: 2 })
          .bindTooltip(`Origin: ${esc(first.from_name)}`).addTo(layer);
        L.circleMarker(last.to_coords, { radius: 6, color: '#fff', fillColor: '#ef4444', fillOpacity: 1, weight: 2 })
          .bindTooltip(`Destination: ${esc(last.to_name)}`).addTo(layer);
      }
    });
    if (bounds.length) map.current.fitBounds(L.latLngBounds(bounds), { padding: [30, 30], maxZoom: 6 });
  }, [recommendations, selected]);

  return (
    <div style={{ position: 'relative' }}>
      <div ref={el} className="route-map" role="region" aria-label="Route map" />
      <div className="map-legend">
        {Object.entries(MODE_COLORS).filter(([m]) => m !== 'TRANSFER').map(([m, c]) => (
          <span key={m}><i style={{ background: c }} />{m}</span>
        ))}
        <span><i style={{ background: '#ef4444', borderRadius: '50%' }} />CLOSED</span>
        <span><i style={{ background: '#f97316', borderRadius: '50%' }} />DISRUPTED</span>
        <span><i style={{ background: '#eab308', borderRadius: '50%' }} />LIVE INTEL</span>
      </div>
    </div>
  );
}
