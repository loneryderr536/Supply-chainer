import React, { useState, useEffect } from 'react';
import { 
  Shield, AlertTriangle, Clock, TrendingUp, Info, 
  BarChart3, Package, Truck, Database, Activity, CheckCircle2, ShieldAlert, Zap
} from 'lucide-react';
import { api } from './api.js';

export default function SupplierIntelligence({ onNavigate }) {
  const [suppliers, setSuppliers] = useState([]);
  const [advice, setAdvice] = useState(null);
  const [inventory, setInventory] = useState(1000);
  const [safetyStock, setSafetyStock] = useState(1500);
  const [forecast, setForecast] = useState(800);
  const [category, setCategory] = useState('Electronics');
  const [scenario, setScenario] = useState(null);
  const [scenarios, setScenarios] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const fetchScenarios = async () => {
      try {
        setScenarios(await api.get('/api/scenarios'));
      } catch (e) { console.error(e); }
    };
    fetchScenarios();
  }, []);

  useEffect(() => {
    fetchSourcingData();
  }, [category, scenario, inventory, safetyStock, forecast]);

  const fetchSourcingData = async () => {
    setLoading(true);
    try {
      const data = await api.post('/api/suppliers', {
        category,
        current_inventory: inventory,
        safety_stock: safetyStock,
        demand_forecast: forecast,
        scenario
      });
      setSuppliers(data.suppliers);
      setAdvice(data.advice);
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  return (
    <div className="sc-layout animate-fade-in">
      <div className="sc-header">
        <div className="sc-title-group">
          <h2>
            <Database color="#8b5cf6" />
            Supplier Intelligence
          </h2>
          <p className="sc-subtitle">Strategic Sourcing Decision Matrix</p>
        </div>
        <div style={{display: 'flex', gap: '1rem'}}>
          <button className="sc-badge-active" style={{cursor: 'pointer', borderColor: '#8b5cf6', color: '#8b5cf6'}} onClick={() => onNavigate('recommend')}>
            Route Recommender
          </button>
        </div>
      </div>

      <div className="sc-command-panel">
        <div className="sc-input-group">
          <label className="sc-label">Product Category</label>
          <select value={category} onChange={e => setCategory(e.target.value)} className="sc-select">
            <option value="Electronics">Electronics</option>
            <option value="Raw Materials">Raw Materials</option>
            <option value="Chemicals">Chemicals</option>
          </select>
        </div>

        <div className="sc-input-group">
          <label className="sc-label">Inventory State (Units)</label>
          <div className="sc-select-grid">
            <input type="number" value={inventory} onChange={e => setInventory(parseInt(e.target.value))} className="sc-input" style={{paddingLeft: '1rem'}} placeholder="Inventory" />
            <input type="number" value={safetyStock} onChange={e => setSafetyStock(parseInt(e.target.value))} className="sc-input" style={{paddingLeft: '1rem'}} placeholder="Safety Target" />
          </div>
        </div>

        <div className="sc-input-group">
          <label className="sc-label">Global Disruption</label>
          <select value={scenario || ''} onChange={e => setScenario(e.target.value || null)} className="sc-select">
            <option value="">Operational Normal</option>
            {scenarios.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
      </div>

      <div className="sc-results-grid" style={{gridTemplateColumns: '1fr 2fr'}}>
        {/* Advice Card */}
        {advice && (
          <div className="sc-path-card" style={{borderColor: advice.urgency_level === 'CRITICAL' ? '#ef4444' : '#8b5cf6'}}>
            <div className="sc-card-header" style={{background: advice.urgency_level === 'CRITICAL' ? 'rgba(239, 68, 68, 0.1)' : 'rgba(139, 92, 246, 0.1)'}}>
              <div style={{display: 'flex', alignItems: 'center', gap: '8px', color: advice.urgency_level === 'CRITICAL' ? '#ef4444' : '#8b5cf6'}}>
                <ShieldAlert size={20} />
                <span style={{fontWeight: 800, fontSize: '12px'}}>{advice.urgency_level} STATUS</span>
              </div>
            </div>
            <div className="sc-card-body">
              <h3 style={{fontSize: '18px', fontWeight: 800, marginBottom: '1rem'}}>{advice.recommendation}</h3>
              <p style={{fontSize: '14px', color: '#94a3b8', lineHeight: 1.6}}>
                Status: {advice.status.replace(/_/g, ' ')} · Projected inventory: {advice.projected_inventory} units
                {advice.shortage_quantity > 0 && ` · Shortfall vs. safety stock: ${advice.shortage_quantity} units`}
              </p>
            </div>
          </div>
        )}

        {/* Suppliers Table */}
        <div className="sc-path-card">
          <div className="sc-card-header">
            <h3 style={{fontSize: '14px', fontWeight: 700}}>Qualified Global Suppliers</h3>
          </div>
          <div style={{overflowX: 'auto'}}>
            <table style={{width: '100%', borderCollapse: 'collapse', fontSize: '13px'}}>
              <thead>
                <tr style={{borderBottom: '1px solid #1e293b', textAlign: 'left'}}>
                  <th style={{padding: '16px', color: '#94a3b8'}}>Supplier</th>
                  <th style={{padding: '16px', color: '#94a3b8'}}>Risk Score</th>
                  <th style={{padding: '16px', color: '#94a3b8'}}>Effective Lead Time</th>
                  <th style={{padding: '16px', color: '#94a3b8'}}>Decision Score</th>
                </tr>
              </thead>
              <tbody>
                {suppliers.map(s => {
                  // Risk = 1 - disruption-adjusted reliability. (1 - decision_score) is NOT risk:
                  // the decision score is mostly cost and lead time, so it flagged cheap-but-slow
                  // suppliers as risky and hid the effect of active disruptions.
                  const riskPct = Math.round((1 - s.audit_trace.scores.reliability) * 100);
                  return (
                    <tr key={s.id} style={{borderBottom: '1px solid #0f172a'}}>
                      <td style={{padding: '16px'}}>
                        <div style={{fontWeight: 700}}>{s.name}</div>
                        <div style={{fontSize: '11px', color: '#64748b'}}>{s.location_hub}</div>
                      </td>
                      <td style={{padding: '16px'}}>
                        <div style={{display: 'flex', alignItems: 'center', gap: '6px', color: riskPct > 20 ? '#ef4444' : '#10b981'}}>
                          <Activity size={14} /> {riskPct}%
                        </div>
                      </td>
                      <td style={{padding: '16px', fontFamily: 'JetBrains Mono'}}>{s.effective_lead_time} days</td>
                      <td style={{padding: '16px'}}>
                         <div style={{height: '6px', width: '60px', background: '#1e293b', borderRadius: '3px', overflow: 'hidden'}}>
                           <div style={{height: '100%', width: `${Math.round(s.decision_score * 100)}%`, background: '#3b82f6'}}></div>
                         </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
