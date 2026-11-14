"""Physically-plausible simulation of Sentinel-2 / Sentinel-1 time series for corn fields.

Used for the offline demo (no Earth Engine credentials needed). The real ingestion
path lives in pipeline/ingest_gee.py and produces the same per-field schema, so the
feature extractor and classifier run unchanged on either source.

Phenology encoded here (from agronomy literature):
  * Silage corn is chopped whole-plant at ~half-milk line (65% moisture), late Aug - mid Sep,
    while the canopy is still green -> abrupt NDVI/NDWI drop from a high level, and very little
    residue is left, so post-harvest VH backscatter falls to bare-soil levels.
  * Grain corn senesces in the field (black layer, dry-down to ~20-25% moisture) and is
    combined Oct - Nov -> gradual NDVI/NDWI decline weeks before harvest, and stover is left
    standing/lying, so post-harvest VH stays noticeably higher than bare soil.
"""
from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field

import numpy as np

S2_DOYS = np.arange(91, 335, 5)   # Apr 1 - Nov 30, 5-day Sentinel-2 revisit
S1_DOYS = np.arange(93, 335, 6)   # 6-day Sentinel-1 revisit


@dataclass
class County:
    id: str
    name: str
    state: str
    center: tuple[float, float]  # lon, lat
    silage_share: float
    phen_shift: float  # days; regional phenology offset (latitude / climate)
    cloud_rate: float
    n_fields: int


COUNTIES: list[County] = [
    County("yolo-ca", "Yolo County", "CA", (-121.86, 38.66), 0.38, -12.0, 0.08, 420),
    County("marathon-wi", "Marathon County", "WI", (-89.76, 44.90), 0.58, 10.0, 0.38, 520),
    County("story-ia", "Story County", "IA", (-93.46, 42.04), 0.14, 0.0, 0.28, 480),
    County("lancaster-pa", "Lancaster County", "PA", (-76.25, 40.04), 0.47, 4.0, 0.34, 400),
]
COUNTY_BY_ID = {c.id: c for c in COUNTIES}


@dataclass
class Field:
    id: str
    county_id: str
    polygon: list[list[float]]  # closed ring of [lon, lat]
    area_ha: float
    true_label: str  # "silage" | "grain" (ground truth, simulated survey)
    s2_doy: np.ndarray
    ndvi: np.ndarray
    ndwi: np.ndarray
    s1_doy: np.ndarray
    vv: np.ndarray
    vh: np.ndarray
    meta: dict = field(default_factory=dict)


def _sig(x: np.ndarray | float, mid: float, rate: float):
    return 1.0 / (1.0 + np.exp(-(np.asarray(x) - mid) / rate))


def _field_polygon(rng: np.random.Generator, lon: float, lat: float, w_m: float, h_m: float):
    dlat = h_m / 111_320
    dlon = w_m / (111_320 * math.cos(math.radians(lat)))
    # slight skew so boundaries do not look like a perfect raster
    sk = rng.normal(0, 0.04) * dlon
    ring = [
        [lon, lat],
        [lon + dlon, lat + sk * 0.2],
        [lon + dlon + sk, lat + dlat],
        [lon + sk, lat + dlat],
        [lon, lat],
    ]
    return [[round(x, 6), round(y, 6)] for x, y in ring]


def simulate_series(rng: np.random.Generator, label: str, county: County, hard: bool = False):
    """Return (ndvi, ndwi, vv, vh, meta) for one field."""
    shift = county.phen_shift + rng.normal(0, 6)
    sos = 150 + shift + rng.normal(0, 5)          # start of season (emergence/green-up)
    peak = rng.uniform(0.78, 0.9)
    base = rng.uniform(0.14, 0.22)

    if label == "silage":
        harvest = 247 + shift + rng.normal(0, 8)
        sen_mid, sen_rate = harvest + 25, 9.0     # senescence would start after harvest
        residue_ndvi = rng.uniform(0.14, 0.2)
        residue_vh = rng.normal(-22.0, 0.9)
    else:
        harvest = 290 + shift + rng.normal(0, 11)
        sen_mid, sen_rate = 252 + shift + rng.normal(0, 6), rng.uniform(7, 11)
        residue_ndvi = rng.uniform(0.2, 0.28)
        residue_vh = rng.normal(-19.2, 0.9)

    if hard:
        # Edge cases: late-chopped silage after early frost / early-harvested high-moisture grain
        if label == "silage":
            harvest += rng.uniform(15, 30)
            sen_mid = harvest - rng.uniform(5, 15)
            residue_vh += rng.uniform(1.0, 2.5)
        else:
            harvest -= rng.uniform(25, 40)
            sen_mid = harvest - rng.uniform(0, 8)
            residue_vh -= rng.uniform(1.0, 2.5)

    def ndvi_at(d):
        canopy = base + (peak - base) * _sig(d, sos + 20, 7) * (1 - 0.75 * _sig(d, sen_mid, sen_rate))
        cut = _sig(d, harvest, 1.2)
        return canopy * (1 - cut) + residue_ndvi * cut

    def ndwi_at(d):
        water = -0.25 + 0.65 * _sig(d, sos + 25, 8) * (1 - 0.85 * _sig(d, sen_mid + 4, sen_rate))
        cut = _sig(d, harvest, 1.2)
        return water * (1 - cut) + (-0.28) * cut

    ndvi = ndvi_at(S2_DOYS) + rng.normal(0, 0.025, S2_DOYS.size)
    ndwi = ndwi_at(S2_DOYS) + rng.normal(0, 0.025, S2_DOYS.size)

    # Cloud mask: more cloud in spring and late fall
    seasonal = 1 + 0.6 * (_sig(S2_DOYS, 290, 10) + (1 - _sig(S2_DOYS, 130, 10)))
    cloudy = rng.random(S2_DOYS.size) < county.cloud_rate * seasonal
    ndvi[cloudy] = np.nan
    ndwi[cloudy] = np.nan

    # SAR: VH rises with biomass; drops at harvest to residue level
    biomass = _sig(S1_DOYS, sos + 30, 9) * (1 - 0.35 * _sig(S1_DOYS, sen_mid + 10, 10))
    cut = _sig(S1_DOYS, harvest, 1.5)
    vh = (-23.5 + 9.5 * biomass) * (1 - cut) + residue_vh * cut + rng.normal(0, 0.7, S1_DOYS.size)
    vv = (-14.5 + 6.0 * biomass) * (1 - cut) + (residue_vh + 8.5) * cut + rng.normal(0, 0.7, S1_DOYS.size)

    meta = {"sim_harvest_doy": float(harvest), "hard_case": bool(hard)}
    return ndvi, ndwi, vv, vh, meta


def generate_county(county: County, seed: int = 7) -> list[Field]:
    rng = np.random.default_rng(zlib.crc32(county.id.encode()) + seed)
    fields: list[Field] = []
    cols = int(math.ceil(math.sqrt(county.n_fields * 1.35)))
    spacing = 860.0  # metres between field origins
    lon0, lat0 = county.center
    origin_lat = lat0 - (cols / 2) * spacing / 111_320
    origin_lon = lon0 - (cols / 2) * spacing / (111_320 * math.cos(math.radians(lat0)))
    idx = 0
    for r in range(cols):
        for c in range(cols):
            if idx >= county.n_fields:
                break
            if rng.random() < 0.26:  # gaps: roads, towns, other crops
                continue
            lat = origin_lat + r * spacing / 111_320
            lon = origin_lon + c * spacing / (111_320 * math.cos(math.radians(lat)))
            w, h = rng.uniform(420, 800), rng.uniform(380, 800)
            label = "silage" if rng.random() < county.silage_share else "grain"
            hard = rng.random() < 0.07
            ndvi, ndwi, vv, vh, meta = simulate_series(rng, label, county, hard)
            idx += 1
            fields.append(Field(
                id=f"{county.id.split('-')[0][:3].upper()}-{idx:04d}",
                county_id=county.id,
                polygon=_field_polygon(rng, lon, lat, w, h),
                area_ha=round(w * h / 10_000, 1),
                true_label=label,
                s2_doy=S2_DOYS, ndvi=ndvi, ndwi=ndwi,
                s1_doy=S1_DOYS, vv=vv, vh=vh, meta=meta,
            ))
    return fields


def generate_training_set(n: int = 3000, seed: int = 99):
    """Labelled training fields drawn across all regions (stand-in for NASS/FSA survey labels)."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        county = COUNTIES[rng.integers(len(COUNTIES))]
        label = "silage" if rng.random() < 0.45 else "grain"
        hard = rng.random() < 0.07
        ndvi, ndwi, vv, vh, _ = simulate_series(rng, label, county, hard)
        out.append((label, S2_DOYS, ndvi, ndwi, S1_DOYS, vv, vh))
    return out
