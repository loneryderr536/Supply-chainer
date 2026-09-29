import React, { useState } from 'react';
import { Bell, Radio, Eye, Trash2, Check, Send, Newspaper, RefreshCw, X, Satellite } from 'lucide-react';
import { api, SEVERITY_COLORS, hubName } from './api.js';
import { useI18n } from './i18n.jsx';

const SOURCE_LABEL = { LIVE_NEWS: 'NEWS', ANALYST: 'ANALYST', GDACS: 'GDACS', AIS: 'AIS' };

export default function LiveOps({ scenarios, onScenariosChanged, watches, onWatchesChanged, alerts, onAlertsChanged,
                                  hubs, intel, onIntelChanged, feeds, onFeedsChanged }) {
  const { t, num, locale } = useI18n();
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  const [intelHub, setIntelHub] = useState('');
  const [intelText, setIntelText] = useState('');
  const [intelResult, setIntelResult] = useState(null);

  const toggleScenario = async (s) => {
    setBusy(s.id);
    setNote(null);
    try {
      if (s.active) {
        await api.post(`/api/scenarios/${s.id}/deactivate`);
      } else {
        const out = await api.post(`/api/scenarios/${s.id}/activate`);
        setNote(t('ops.activated', { name: t(`scenario.${s.id}`), n: out.alerts_raised.length }));
      }
      await Promise.all([onScenariosChanged(), onAlertsChanged()]);
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(null);
    }
  };

  const submitIntel = async () => {
    if (!intelHub || !intelText.trim()) return;
    setBusy('intel');
    try {
      const out = await api.post('/api/intel/report', { hub_id: intelHub.trim().toUpperCase(), text: intelText });
      setIntelResult(out.intel);
      await Promise.all([onIntelChanged(), onAlertsChanged()]);
    } catch (e) {
      setIntelResult({ error: e.message });
    } finally {
      setBusy(null);
    }
  };

  const rescan = async () => {
    setBusy('scan');
    try {
      const out = await api.post('/api/intel/scan');
      setNote(out.status === 'ok' ? t('ops.scanned', { n: out.hubs_scanned, t: out.threats })
                                  : `News scan ${out.status}${out.reason ? `: ${out.reason}` : ''}.`);
      await Promise.all([onIntelChanged(), onAlertsChanged()]);
    } catch (e) { setNote(e.message); } finally { setBusy(null); }
  };

  const refreshGdacs = async () => {
    setBusy('gdacs');
    try {
      await api.post('/api/feeds/gdacs/refresh');
      await Promise.all([onFeedsChanged(), onAlertsChanged()]);
    } catch (e) { setNote(e.message); } finally { setBusy(null); }
  };

  const confirmIntel = async (entry) => {
    await api.post('/api/intel/report', { hub_id: entry.hub_id, text: entry.headline });
    await Promise.all([onIntelChanged(), onAlertsChanged()]);
  };

  const dismiss = async (entry) => {
    await api.del(`/api/intel/${entry.hub_id}?source=${entry.source}`);
    onIntelChanged();
  };

  const feedLine = (f) => {
    if (f.state === 'ok' && f.source === 'GDACS') return t('ops.feed.ok', { n: f.events, h: f.hubs_exposed });
    if (f.state === 'disabled') return f.reason;
    if (f.state === 'connected') return `connected · ${f.messages ?? 0} msgs`;
    return f.error ? `${f.state}: ${f.error}` : f.state;
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      <section>
        <h3 className="ops-title"><Bell size={13} /> {t('ops.alerts', { n: alerts.length })}</h3>
        {alerts.length === 0 && <p className="ops-empty">{t('ops.noalerts')}</p>}
        {alerts.map((a) => (
          <div key={a.id} className="alert-card" style={{ borderLeftColor: SEVERITY_COLORS[a.severity] || '#64748b' }}>
            <div className="alert-head">
              <span style={{ color: SEVERITY_COLORS[a.severity] }}>{a.severity}</span>
              <span>{new Date(a.created_at * 1000).toLocaleTimeString(locale)}</span>
            </div>
            <div className="alert-body">{a.message}</div>
            <button className="ops-btn" onClick={async () => { await api.post(`/api/alerts/${a.id}/ack`); onAlertsChanged(); }}>
              <Check size={12} /> {t('ops.ack')}
            </button>
          </div>
        ))}
      </section>

      <section>
        <h3 className="ops-title"><Radio size={13} /> {t('ops.picture')}</h3>
        <p className="ops-empty">{t('ops.picture.hint')}</p>
        {scenarios.map((s) => (
          <button key={s.id} className={`scenario-toggle ${s.active ? 'on' : ''}`} disabled={busy === s.id}
                  onClick={() => toggleScenario(s)} aria-pressed={s.active}>
            <span>{t(`scenario.${s.id}`)}</span>
            <span className="scenario-state">{s.active ? t('ops.live') : t('ops.off')}{s.closure ? ` · ${t('ops.closure')}` : ''}</span>
          </button>
        ))}
        {note && <p className="ops-note">{note}</p>}
      </section>

      <section>
        <h3 className="ops-title"><Eye size={13} /> {t('ops.watches', { n: watches.length })}</h3>
        {watches.length === 0 && <p className="ops-empty">{t('ops.nowatches')}</p>}
        {watches.map((w) => (
          <div key={w.id} className="watch-row">
            <div>
              <div style={{ fontWeight: 700 }}>{w.label || `${w.request.source} → ${w.request.destination}`}</div>
              <div className="ops-empty">
                {t(`persona.${w.persona}`)} · p50 {num(w.route.eta_band.p50, 0)}h · {w.route.chokepoints.map(hubName).join(', ') || w.route.primary_mode}
              </div>
            </div>
            <button className="icon-btn" aria-label={t('ops.stop')} title={t('ops.stop')}
                    onClick={async () => { await api.del(`/api/watches/${w.id}`); onWatchesChanged(); }}>
              <Trash2 size={13} />
            </button>
          </div>
        ))}
      </section>

      <section>
        <h3 className="ops-title"><Satellite size={13} /> {t('ops.feeds')}</h3>
        {(feeds || []).map((f) => (
          <div key={f.source} className="feed-row">
            <span className={`intel-src src-${f.source}`}>{f.source}</span>
            <span className="ops-empty" style={{ flex: 1 }}>{feedLine(f)}</span>
            {f.source === 'GDACS' && (
              <button className="icon-btn" aria-label={t('ops.feed.refresh')} title={t('ops.feed.refresh')}
                      disabled={busy === 'gdacs'} onClick={refreshGdacs}><RefreshCw size={13} /></button>
            )}
          </div>
        ))}
      </section>

      <section>
        <h3 className="ops-title" style={{ justifyContent: 'space-between' }}>
          <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}><Newspaper size={13} /> {t('ops.intel', { n: intel.length })}</span>
          <button className="icon-btn" aria-label={t('ops.rescan')} title={t('ops.rescan')} disabled={busy === 'scan'} onClick={rescan}>
            <RefreshCw size={13} />
          </button>
        </h3>
        <p className="ops-empty">{t('ops.intel.hint')}</p>
        {intel.map((entry) => (
          <div key={`${entry.hub_id}-${entry.source}`} className="intel-row">
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 700 }}>
                {hubName(entry.hub_id)} · {Math.round(entry.score * 100)}% {entry.category ? t(`cat.${entry.category}`) : ''}
                <span className={`intel-src src-${entry.source}`}>
                  {entry.source === 'LIVE_NEWS' ? t('ops.unverified') : SOURCE_LABEL[entry.source] || entry.source}
                </span>
              </div>
              <div className="ops-empty">
                {entry.link ? <a href={entry.link} target="_blank" rel="noreferrer" style={{ color: '#93c5fd' }}>{entry.headline}</a> : entry.headline}
              </div>
              <div className="ops-empty">
                {t('ops.applied', { v: Object.entries(entry.threat_by_mode).filter(([, v]) => v > 0)
                  .map(([m, v]) => `${t(`legmode.${m.toUpperCase()}`)} ${Math.round(v * 100)}%`).join(' · ') || t('none') })}
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {entry.source === 'LIVE_NEWS' && (
                <button className="icon-btn" aria-label={t('ops.confirm')} title={t('ops.confirm')} onClick={() => confirmIntel(entry)}>
                  <Check size={13} />
                </button>
              )}
              <button className="icon-btn" aria-label={t('ops.dismiss')} title={t('ops.dismiss')} onClick={() => dismiss(entry)}>
                <X size={13} />
              </button>
            </div>
          </div>
        ))}
      </section>

      <section>
        <h3 className="ops-title"><Send size={13} /> {t('ops.report')}</h3>
        <input className="sc-input" list="hub-ids" placeholder={t('ops.hub')} value={intelHub}
               onChange={(e) => setIntelHub(e.target.value)} aria-label={t('ops.hub')} />
        <datalist id="hub-ids">{hubs.map((h, i) => <option key={`${h.id}-${i}`} value={h.id}>{h.display_name}</option>)}</datalist>
        <textarea className="sc-input" rows={3} style={{ marginTop: 6, width: '100%', resize: 'vertical' }}
                  placeholder={t('ops.text')} value={intelText} onChange={(e) => setIntelText(e.target.value)}
                  aria-label={t('ops.text')} />
        <button className="ops-btn" style={{ marginTop: 6 }} disabled={busy === 'intel'} onClick={submitIntel}>{t('ops.submit')}</button>
        {intelResult && (
          <div className="audit-trace-box" style={{ marginTop: 8 }}>
            {intelResult.error ? intelResult.error : intelResult.score > 0 ? (
              <>
                <div>{t('ops.score', { p: Math.round(intelResult.score * 100), cat: t(`cat.${intelResult.category}`) })}</div>
                <div>{t('ops.carf', { v: Object.entries(intelResult.threat_by_mode)
                  .map(([m, v]) => `${t(`legmode.${m.toUpperCase()}`)} ${Math.round(v * 100)}%`).join(' · ') })}</div>
              </>
            ) : <div>{intelResult.note}</div>}
          </div>
        )}
      </section>
    </div>
  );
}
