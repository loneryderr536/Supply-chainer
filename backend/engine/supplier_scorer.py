import json
import os
from typing import List, Dict, Any, Optional

class SupplierScorer:
    """
    Supplychainer Supplier Intelligence Engine.
    Provides deterministic, disruption-aware supplier ranking and procurement advice.
    """
    def __init__(self, data_path: str):
        self.data_path = data_path
        self.suppliers = self._load_suppliers()

    def _load_suppliers(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.data_path):
            with open(self.data_path, 'r') as f:
                return json.load(f)
        return []

    def get_ranked_suppliers(self, category: str, active_disruptions: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Ranks suppliers based on cost, lead time, reliability, and active disruption impact.
        """
        filtered = [s for s in self.suppliers if s['category'] == category]
        
        scored_suppliers = []
        for s in filtered:
            # Deterministic Scoring Base
            # 1. Cost Score (0.3)
            cost_score = max(0.0, 1.0 - (s['unit_cost'] / 1000.0)) # Normalized to $1k cap; costlier suppliers score 0
            
            # 2. Lead Time Score (0.3)
            # Base lead time + disruption penalty
            effective_lead_time = s['base_lead_time_days']
            disruption_penalty = 0.0
            
            if active_disruptions:
                for node, impact in active_disruptions.items():
                    if node == s['location_hub'] or node in s.get('transit_choke_points', []):
                        # Lead time penalty: half of the disruption delay, in days (buffer stock absorbs the rest)
                        penalty_days = impact['delay'] / 24.0 * 0.5 
                        effective_lead_time += penalty_days
                        disruption_penalty += penalty_days

            lead_time_score = max(0, 1.0 - (effective_lead_time / 30.0)) # 30 day normalization
            
            # 3. Reliability Score (0.4)
            # Historical reliability - risk inflation from disruptions
            risk_inflation = 0.0
            if active_disruptions:
                for node, impact in active_disruptions.items():
                    if node == s['location_hub'] or node in s.get('transit_choke_points', []):
                        risk_inflation += impact['threat'] * 0.3
            
            reliability_score = max(0.03, min(0.98, s['historical_reliability'] - risk_inflation))
            
            # Total Decision Score
            total_score = (cost_score * 0.3) + (lead_time_score * 0.3) + (reliability_score * 0.4)
            
            # Audit Trace for Explainability
            audit_trace = {
                "scores": {
                    "cost": round(cost_score, 2),
                    "lead_time": round(lead_time_score, 2),
                    "reliability": round(reliability_score, 2)
                },
                "penalties": {
                    "lead_time_impact": round(disruption_penalty, 1),
                    "risk_inflation": round(risk_inflation, 2)
                },
                "effective_metrics": {
                    "lead_time_days": round(effective_lead_time, 1),
                    "stability_index": round(reliability_score * 100, 0)
                }
            }
            
            scored_suppliers.append({
                **s,
                "effective_lead_time": round(effective_lead_time, 1),
                "decision_score": round(total_score, 2),
                "audit_trace": audit_trace
            })
            
        # Rank by decision score
        scored_suppliers.sort(key=lambda x: x['decision_score'], reverse=True)
        for i, s in enumerate(scored_suppliers):
            s['rank'] = i + 1
            
        return scored_suppliers

    def get_procurement_advice(self, current_inventory: int, safety_stock: int, demand_forecast: int) -> Dict[str, Any]:
        """
        Determines if a procurement escalation is required based on inventory shortage logic.
        """
        projected_stock = current_inventory - demand_forecast
        shortage = safety_stock - projected_stock
        
        status = "HEALTHY"
        recommendation = "Maintain current replenishment schedule."
        urgency = "LOW"
        
        if projected_stock <= 0:
            status = "CRITICAL_SHORTAGE"
            recommendation = "EMERGENCY REPLENISHMENT REQUIRED. Projected stockout in current cycle."
            urgency = "CRITICAL"
        elif projected_stock < safety_stock:
            status = "SAFETY_STOCK_VIOLATION"
            recommendation = "Expedite sourcing from high-reliability suppliers to restore safety buffers."
            urgency = "HIGH"
            
        return {
            "status": status,
            "shortage_quantity": max(0, shortage),
            "recommendation": recommendation,
            "urgency_level": urgency,
            "projected_inventory": projected_stock
        }
