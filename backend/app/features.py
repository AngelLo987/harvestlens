"""Time-series feature extraction + phenological inflection point detection.

Works on any per-field series (simulated or from Earth Engine). Optical gaps (clouds)
are handled by interpolation with a coverage feature, and harvest timing falls back to
Sentinel-1 VH (cloud-independent) when the optical record around harvest is missing.
"""
from __future__ import annotations

import numpy as np

FEATURE_NAMES = [
    "harvest_doy",          # detected harvest (largest canopy drop)
    "harvest_doy_sar",      # harvest from SAR VH drop
    "ndvi_pre_harvest",     # canopy greenness just before harvest
    "ndwi_pre_harvest",     # canopy water just before harvest
    "ndvi_drop",            # size of the cut-off step
    "senescence_slope",     # NDVI slope over the 30 days before harvest (per day)
    "ndwi_slope",
    "peak_ndvi",
    "days_peak_to_harvest",
    "vh_post",              # post-harvest VH (residue / stover)
    "vh_drop",
    "vv_vh_post",
    "optical_coverage",     # fraction of cloud-free S2 observations Jul-Nov
]


def _interp(doy: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    ok = ~np.isnan(y)
    if ok.sum() < 3:
        return np.full(grid.shape, np.nan)
    return np.interp(grid, doy[ok], y[ok])


def _smooth(y: np.ndarray, k: int = 3) -> np.ndarray:
    if np.isnan(y).all():
        return y
    pad = np.pad(y, (k // 2, k // 2), mode="edge")
    return np.convolve(pad, np.ones(k) / k, mode="valid")


def detect_sar_harvest(s1_doy, vh) -> tuple[float, float, float]:
    vh_s = _smooth(np.asarray(vh, float), 3)
    window = (s1_doy >= 200) & (s1_doy <= 334)
    d = np.diff(vh_s)
    d[~window[1:]] = 0
    i = int(np.argmin(d))
    harvest = float(s1_doy[i + 1])
    pre = float(np.nanmean(vh_s[max(0, i - 2): i + 1]))
    post = float(np.nanmean(vh[i + 2: i + 6])) if i + 2 < len(vh) else float(vh[-1])
    return harvest, pre - post, post


def extract(s2_doy, ndvi, ndwi, s1_doy, vv, vh) -> dict:
    s2_doy = np.asarray(s2_doy, float)
    s1_doy = np.asarray(s1_doy, float)
    ndvi = np.asarray(ndvi, float)
    ndwi = np.asarray(ndwi, float)
    vv = np.asarray(vv, float)
    vh = np.asarray(vh, float)

    grid = np.arange(150, 335, 2.0)
    nd = _interp(s2_doy, ndvi, grid)
    nw = _interp(s2_doy, ndwi, grid)

    late = (s2_doy >= 182)
    coverage = float((~np.isnan(ndvi[late])).mean())

    h_sar, vh_drop, vh_post = detect_sar_harvest(s1_doy, vh)

    if np.isnan(nd).all():
        h_opt = np.nan
    else:
        nds = _smooth(nd, 3)
        drop = nds[:-4] - nds[4:]  # 8-day drop
        drop[grid[:-4] < 200] = -1
        j = int(np.argmax(drop))
        h_opt = float(grid[j + 2])
        # If the optical record is thin around the detected drop, trust SAR timing instead.
        near = np.abs(s2_doy - h_opt) <= 12
        if (~np.isnan(ndvi[near])).sum() < 2 or abs(h_opt - h_sar) > 25:
            h_opt = h_sar

    harvest = h_opt if not np.isnan(h_opt) else h_sar

    def at(series, d):
        return float(np.interp(d, grid, series)) if not np.isnan(series).all() else np.nan

    pre = harvest - 6
    ndvi_pre = at(nd, pre)
    ndwi_pre = at(nw, pre)
    ndvi_post = at(nd, harvest + 10)
    sen_slope = (at(nd, pre) - at(nd, pre - 30)) / 30 if not np.isnan(ndvi_pre) else np.nan
    ndwi_slope = (at(nw, pre) - at(nw, pre - 30)) / 30 if not np.isnan(ndwi_pre) else np.nan
    peak = float(np.nanmax(nd)) if not np.isnan(nd).all() else np.nan
    peak_doy = float(grid[int(np.nanargmax(nd))]) if not np.isnan(nd).all() else np.nan

    post_mask = s1_doy > harvest + 4
    vv_post = float(np.nanmean(vv[post_mask])) if post_mask.any() else np.nan
    vh_post_m = float(np.nanmean(vh[post_mask])) if post_mask.any() else vh_post

    return {
        "harvest_doy": harvest,
        "harvest_doy_sar": h_sar,
        "ndvi_pre_harvest": ndvi_pre,
        "ndwi_pre_harvest": ndwi_pre,
        "ndvi_drop": ndvi_pre - ndvi_post if not np.isnan(ndvi_pre) else np.nan,
        "senescence_slope": sen_slope,
        "ndwi_slope": ndwi_slope,
        "peak_ndvi": peak,
        "days_peak_to_harvest": harvest - peak_doy if not np.isnan(peak_doy) else np.nan,
        "vh_post": vh_post_m,
        "vh_drop": vh_drop,
        "vv_vh_post": vv_post - vh_post_m if not np.isnan(vv_post) else np.nan,
        "optical_coverage": coverage,
    }


def to_vector(f: dict) -> list[float]:
    return [f[k] for k in FEATURE_NAMES]
