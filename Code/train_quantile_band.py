"""
Train the p50 / p95 siblings of the production p85 delay model.

The production model (Execution/risk_model.pkl) was produced by Code/real_dataset_builder.py
(seeded, so fully reproducible) followed by Code/ML_Model_Real.py. This script rebuilds the
same dataset in memory, checks that a p85 model retrained here agrees with the shipped one,
then trains alpha=0.50 and alpha=0.95 models with identical hyper-parameters so the API can
report a p50/p85/p95 delay band instead of a single number.

It also stores a small background sample used for Shapley attributions at inference time.

Run from the project root:  python Code/train_quantile_band.py
"""
import json
import os
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXEC = os.path.join(ROOT, "Execution")
FEATURES = ["Leg_Type", "Origin_Node", "Destination_Node", "Transport_Mode", "Condition_Flag", "NLP_Severity_Score"]
CATEGORICAL = FEATURES[:5]


def build_dataset() -> pd.DataFrame:
    # Run the seeded builder up to (not including) its CSV export, so nothing is written to disk.
    with open(os.path.join(ROOT, "Code", "real_dataset_builder.py")) as f:
        source = f.read().split("# --- EXPORT ---")[0]
    ns: dict = {}
    exec(compile(source, "real_dataset_builder.py", "exec"), ns)
    return ns["df"]


def main():
    df = build_dataset()
    encoders = joblib.load(os.path.join(EXEC, "label_encoders.pkl"))
    for col in CATEGORICAL:
        df[col] = encoders[col].transform(df[col].astype(str))

    X, y = df[FEATURES], df["Delay_Hours"]
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    shipped = joblib.load(os.path.join(EXEC, "risk_model.pkl"))
    models = {}
    for alpha in (0.50, 0.85, 0.95):
        m = GradientBoostingRegressor(loss="quantile", alpha=alpha, n_estimators=400,
                                      learning_rate=0.05, max_depth=6, random_state=42)
        m.fit(X_train, y_train)
        coverage = float(np.mean(y_test <= m.predict(X_test)))
        print(f"alpha={alpha:.2f}  empirical coverage on hold-out: {coverage:.3f}")
        models[alpha] = (m, coverage)

    drift = np.abs(models[0.85][0].predict(X_test) - shipped.predict(X_test))
    print(f"retrained p85 vs shipped p85: mean |diff| = {drift.mean():.3f}h, max = {drift.max():.3f}h")
    if drift.mean() > 1.0:
        sys.exit("Retrained p85 does not reproduce the shipped model; refusing to write siblings.")

    joblib.dump(models[0.50][0], os.path.join(EXEC, "risk_model_p50.pkl"))
    joblib.dump(models[0.95][0], os.path.join(EXEC, "risk_model_p95.pkl"))

    background = X_train.sample(n=64, random_state=7)
    meta = {
        "features": FEATURES,
        "background": background.to_dict(orient="list"),
        "holdout_coverage": {str(a): round(c, 4) for a, (_, c) in models.items()},
        "p85_reproduction_mean_abs_diff_h": round(float(drift.mean()), 4),
    }
    with open(os.path.join(EXEC, "quantile_band_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print("Wrote risk_model_p50.pkl, risk_model_p95.pkl, quantile_band_meta.json")


if __name__ == "__main__":
    main()
