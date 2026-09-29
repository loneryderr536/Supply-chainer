import React, { useState, useEffect } from 'react';
import BenchmarkCharts from './BenchmarkCharts.jsx';
import RouteRecommender from './RouteRecommender.jsx';
import SupplierIntelligence from './SupplierIntelligence.jsx';
import { api, DEMO } from './api.js';

export default function App() {
  const [network, setNetwork] = useState({ nodes: [], edges: [] });
  const [status, setStatus] = useState(null);
  const [alertTick, setAlertTick] = useState(0);
  const [currentView, setCurrentView] = useState('recommend');

  useEffect(() => {
    api.get('/api/network').then(setNetwork).catch(console.error);
    if (DEMO) {
      setStatus({ engine_status: 'DEMO' });  // no live server behind the static demo
      return undefined;
    }

    // One socket for engine status (every 2s) and pushed route alerts; reconnects if the API restarts.
    let ws;
    let retry;
    let closed = false;
    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'alert') setAlertTick((t) => t + 1);
        else setStatus(msg);
      };
      ws.onclose = () => {
        setStatus(null);
        if (!closed) retry = setTimeout(connect, 3000);
      };
    };
    connect();
    return () => { closed = true; clearTimeout(retry); ws && ws.close(); };
  }, []);

  if (currentView === 'suppliers') {
    return <SupplierIntelligence onNavigate={setCurrentView} />;
  }
  if (currentView === 'benchmark') {
    return <BenchmarkCharts onBack={() => setCurrentView('recommend')} />;
  }
  return <RouteRecommender onNavigate={setCurrentView} status={status} network={network} alertTick={alertTick} />;
}
