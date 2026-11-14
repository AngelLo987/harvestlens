"""Silage vs. grain classifier: bagged gradient-boosted trees with uncertainty intervals.

HistGradientBoosting handles NaN natively (missing optical data). Bagging gives a
distribution of probabilities per field -> mean probability plus a 90% interval.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split

from . import features as F
from .simulate import generate_training_set

N_BAGS = 12


class HarvestClassifier:
    def __init__(self):
        self.models: list[HistGradientBoostingClassifier] = []
        self.validation: dict = {}
        self.reference: dict = {}

    def fit(self, seed: int = 0):
        data = generate_training_set()
        X = np.array([F.to_vector(F.extract(*row[1:])) for row in data])
        y = np.array([1 if row[0] == "silage" else 0 for row in data])
        Xtr, Xva, ytr, yva = train_test_split(X, y, test_size=0.25, random_state=seed, stratify=y)
        rng = np.random.default_rng(seed)
        self.models = []
        for b in range(N_BAGS):
            idx = rng.integers(0, len(Xtr), len(Xtr))
            m = HistGradientBoostingClassifier(max_iter=180, learning_rate=0.08, max_leaf_nodes=15,
                                               l2_regularization=1.0, random_state=b)
            m.fit(Xtr[idx], ytr[idx])
            self.models.append(m)
        p = self.predict_proba(Xva)[0]
        yhat = (p >= 0.5).astype(int)
        cm = confusion_matrix(yva, yhat, labels=[1, 0])
        self.validation = {
            "n_train": int(len(Xtr)),
            "n_validation": int(len(Xva)),
            "accuracy": round(float(accuracy_score(yva, yhat)), 4),
            "f1_silage": round(float(f1_score(yva, yhat)), 4),
            "confusion": {"labels": ["silage", "grain"], "matrix": cm.tolist()},
        }
        # Class medians for explanations
        for cls, name in [(1, "silage"), (0, "grain")]:
            sub = X[y == cls]
            self.reference[name] = {k: float(np.nanmedian(sub[:, i])) for i, k in enumerate(F.FEATURE_NAMES)}
        return self

    def predict_proba(self, X: np.ndarray):
        P = np.stack([m.predict_proba(X)[:, 1] for m in self.models])
        return P.mean(0), np.percentile(P, 5, axis=0), np.percentile(P, 95, axis=0)


def doy_to_date(doy: float, year: int = 2025) -> str:
    if doy is None or np.isnan(doy):
        return "n/a"
    return (dt.date(year, 1, 1) + dt.timedelta(days=int(round(doy)) - 1)).strftime("%b %d")


def explain(feat: dict, label: str, prob_silage: float, lo: float, hi: float, ref: dict) -> dict:
    """Evidence statements + anomaly flags for a single field."""
    ev = []
    h = feat["harvest_doy"]
    gap = ref["grain"]["harvest_doy"] - h
    ev.append({
        "signal": "Harvest timing",
        "value": doy_to_date(h),
        "supports": "silage" if h < (ref["silage"]["harvest_doy"] + ref["grain"]["harvest_doy"]) / 2 else "grain",
        "detail": f"Canopy removal detected {doy_to_date(h)} — {abs(gap):.0f} days "
                  f"{'before' if gap > 0 else 'after'} the typical grain harvest.",
    })
    if not np.isnan(feat["ndvi_pre_harvest"]):
        v = feat["ndvi_pre_harvest"]
        ev.append({
            "signal": "Greenness at cut (NDVI)",
            "value": f"{v:.2f}",
            "supports": "silage" if v > 0.55 else "grain",
            "detail": "Crop was removed while still green (whole-plant chop)." if v > 0.55
            else "Canopy had already senesced before harvest (dry-down for grain).",
        })
    if not np.isnan(feat["ndwi_pre_harvest"]):
        v = feat["ndwi_pre_harvest"]
        ev.append({
            "signal": "Canopy moisture at cut (NDWI)",
            "value": f"{v:.2f}",
            "supports": "silage" if v > 0.1 else "grain",
            "detail": "High plant moisture at harvest, consistent with ~65% moisture silage." if v > 0.1
            else "Low canopy water — kernels dried down before harvest.",
        })
    v = feat["vh_post"]
    ev.append({
        "signal": "Post-harvest radar (S1 VH)",
        "value": f"{v:.1f} dB",
        "supports": "silage" if v < -20.6 else "grain",
        "detail": "Low backscatter: bare soil, little residue left (whole plant removed)." if v < -20.6
        else "Elevated backscatter from standing/lying stover left by a combine.",
    })

    flags = []
    conf = max(prob_silage, 1 - prob_silage)
    if conf < 0.75:
        flags.append("low_confidence")
    if hi - lo > 0.3:
        flags.append("model_disagreement")
    if feat["optical_coverage"] < 0.4:
        flags.append("cloud_gaps")
    votes = [e["supports"] for e in ev]
    if votes.count(label) < len(votes) / 2 and conf < 0.98:
        flags.append("conflicting_signals")
    if abs(feat["harvest_doy"] - feat["harvest_doy_sar"]) > 21:
        flags.append("optical_sar_timing_mismatch")
    return {"evidence": ev, "flags": flags}
