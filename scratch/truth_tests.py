import sys
import os

# Ensure we can import from backend
sys.path.append(os.getcwd())

import numpy as np
import joblib

def test_production_loading():
    print("TEST: Production Artifact Loading")
    try:
        from backend.engine.threat_intelligence import ThreatIntelligencePredictor
        predictor = ThreatIntelligencePredictor()
        print("SUCCESS: Production artifacts (.pkl) loaded correctly.")
        return predictor
    except Exception as e:
        print(f"FAILED: {e}")
        return None

def test_nlp_reality():
    print("\nTEST: SentenceTransformer Reality Check")
    try:
        from backend.engine.threat_intelligence import ContrastiveNLPEngine
        nlp = ContrastiveNLPEngine()
        if not nlp._ready:
            print("FAILED: NLP Engine not ready.")
            return
        
        # Test real inference
        score = nlp.get_semantic_score("Massive hurricane destroyed the container port and blocked all maritime routes.")
        print(f"SUCCESS: Real NLP Score for disruption: {score:.3f}")
        
        score_safe = nlp.get_semantic_score("Skies are blue and traffic is flowing perfectly through the terminal.")
        print(f"SUCCESS: Real NLP Score for safe text: {score_safe:.3f}")
        
        # A relative check (score > score_safe) passed even when the disaster was zeroed out and the
        # safe text went negative. Require an absolute separation instead.
        if score >= 0.5 and score_safe == 0.0:
            print("VERIFIED: Contrastive NLP distinguishes threat from safety.")
        else:
            print("CRITICAL: NLP failed to distinguish signals.")
            
    except Exception as e:
        print(f"FAILED: {e}")

def test_quantile_calibration(predictor):
    if not predictor: return
    print("\nTEST: Calibration & Friction Floor Truth")
    try:
        res = predictor.predict_worst_case_delay(
            origin="Seattle", 
            destination="Chicago", 
            transport_mode="sea", 
            nlp_score=0.0
        )
        print(f"SUCCESS: No Disruption Sea Delay: {res['final_delay_presented']}h (Friction Floor: {res['baseline_systemic_friction']}h)")
        
        res_disrupt = predictor.predict_worst_case_delay(
            origin="Seattle", 
            destination="Chicago", 
            transport_mode="road", 
            nlp_score=1.0
        )
        print(f"SUCCESS: Severe Disruption Road Delay: {res_disrupt['final_delay_presented']}h (Capped from: {res_disrupt['raw_model_prediction']}h)")
        
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    p = test_production_loading()
    test_nlp_reality()
    test_quantile_calibration(p)
