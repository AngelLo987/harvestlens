"""Real-data ingestion: Sentinel-2 + Sentinel-1 time series per field via Google Earth Engine.

Writes backend/data/fields_gee.json in the same per-field schema the simulator produces, so the
API classifies real fields alongside the demo data (restart with CORNEA_REBUILD=1).

Setup (once):
    .venv/bin/pip install earthengine-api
    .venv/bin/earthengine authenticate
    export EE_PROJECT=<gcp project registered for Earth Engine>

Two ways to choose fields:
  1. County mode (no files needed): field polygons are vectorised from contiguous corn pixels in the
     USDA Cropland Data Layer inside a Census TIGER county boundary.
        python -m pipeline.ingest_gee --county "Marathon" --state WI --year 2025 --limit 150
  2. Boundary file: your own GeoJSON polygons (optional "label": silage|grain for validation).
        python -m pipeline.ingest_gee --boundaries fields.geojson --county-id marathon-wi --year 2025
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "fields_gee.json"
STATE_FIPS = {"AL": "01", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "FL": "12", "GA": "13",
              "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
              "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
              "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39", "OK": "40", "OR": "41", "PA": "42",
              "RI": "44", "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
              "WV": "54", "WI": "55", "WY": "56"}
BATCH = 20  # fields per request; keeps each query under EE's 5000-element collection limit


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def cdl_corn(ee, year: int):
    cdl = ee.ImageCollection("USDA/NASS/CDL").filterDate(f"{year}-01-01", f"{year}-12-31").first()
    cdl = ee.Image(ee.Algorithms.If(cdl, cdl, ee.ImageCollection("USDA/NASS/CDL").sort("system:time_start", False).first()))
    return cdl.select("cropland").eq(1)


def fields_from_county(ee, county: str, state: str, year: int, limit: int, prefix: str):
    counties = ee.FeatureCollection("TIGER/2018/Counties").filter(ee.Filter.eq("STATEFP", STATE_FIPS[state.upper()]))
    region = counties.filter(ee.Filter.eq("NAME", county.replace(" County", ""))).geometry()
    corn = cdl_corn(ee, year).clip(region)
    # Morphological opening removes roads/edges so adjacent fields separate; then vectorise patches.
    opened = corn.focalMin(1, "square", "pixels").focalMax(1, "square", "pixels").selfMask()
    vecs = opened.reduceToVectors(geometry=region, scale=30, geometryType="polygon", eightConnected=False,
                                  maxPixels=1e10, bestEffort=True, tileScale=4)
    vecs = vecs.map(lambda f: f.set("area_ha", f.geometry().area(10).divide(1e4)))
    vecs = vecs.filter(ee.Filter.rangeContains("area_ha", 8, 120)).randomColumn("r", 42).sort("r").limit(limit)
    info = vecs.map(lambda f: ee.Feature(f.geometry().simplify(15), {"area_ha": f.get("area_ha")})).getInfo()["features"]
    out = []
    for i, f in enumerate(info):
        g = f["geometry"]
        ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
        out.append({"fid": f"{prefix}-R{i + 1:04d}", "ring": ring, "label": None})
    return out


def fields_from_file(path: str, limit: int, prefix: str):
    gj = json.loads(Path(path).read_text())
    out = []
    for i, f in enumerate(gj["features"][:limit]):
        g = f["geometry"]
        ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
        p = f.get("properties") or {}
        out.append({"fid": str(p.get("id") or f"{prefix}-R{i + 1:04d}"), "ring": ring, "label": p.get("label")})
    return out


def main():
    import ee

    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--county", help='County name, e.g. "Marathon" (with --state)')
    src.add_argument("--boundaries", help="GeoJSON of field polygons")
    ap.add_argument("--state", help="2-letter state code for --county")
    ap.add_argument("--county-id", help="id used in the app (default: <county>-<state>)")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--min-corn", type=float, default=0.6)
    ap.add_argument("--limit", type=int, default=150)
    a = ap.parse_args()
    if a.county and not a.state:
        ap.error("--county needs --state")
    county_id = a.county_id or (f"{a.county.lower().replace(' county', '').replace(' ', '-')}-{a.state.lower()}-s2" if a.county else "boundaries-s2")
    county_name = (f"{a.county.replace(' County', '')} County (Sentinel)" if a.county else "Uploaded fields (Sentinel)")
    prefix = county_id.split("-")[0][:3].upper()

    ee.Initialize(project=os.environ.get("EE_PROJECT"))
    log(f"Earth Engine ready (project={os.environ.get('EE_PROJECT')})")

    fields = (fields_from_county(ee, a.county, a.state, a.year, a.limit, prefix) if a.county
              else fields_from_file(a.boundaries, a.limit, prefix))
    log(f"{len(fields)} candidate fields")
    if not fields:
        sys.exit("No fields found — check county/state spelling.")

    corn = cdl_corn(ee, a.year).rename("corn")
    start, end = f"{a.year}-04-01", f"{a.year}-12-01"
    all_fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Polygon([f["ring"]]), {"fid": f["fid"]}) for f in fields])
    bounds = all_fc.geometry().bounds()

    def s2_prep(img):
        scl = img.select("SCL")
        clear = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        ndvi = img.normalizedDifference(["B8", "B4"]).rename("ndvi")
        ndwi = img.normalizedDifference(["B8", "B11"]).rename("ndwi")
        return ndvi.addBands(ndwi).updateMask(clear).copyProperties(img, ["system:time_start"])

    s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(bounds).filterDate(start, end)
          .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 80)).map(s2_prep))
    s1 = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(bounds).filterDate(start, end)
          .filter(ee.Filter.eq("instrumentMode", "IW"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH")))
    # Use one orbit direction so incidence geometry is consistent through the season.
    asc = s1.filter(ee.Filter.eq("orbitProperties_pass", "ASCENDING"))
    s1 = ee.ImageCollection(ee.Algorithms.If(asc.size().gte(20), asc, s1)).select(["VV", "VH"])
    log(f"Sentinel-2 scenes: {s2.size().getInfo()}, Sentinel-1 scenes: {s1.size().getInfo()}")

    def series(coll, fc, keys, scale):
        def fn(img):
            doy = ee.Date(img.get("system:time_start")).getRelative("day", "year").add(1)
            return (img.reduceRegions(fc, ee.Reducer.mean(), scale, tileScale=2)
                    .filter(ee.Filter.notNull([keys[0]]))
                    .map(lambda f: ee.Feature(None, {"fid": f.get("fid"), "doy": doy, **{k: f.get(k) for k in keys}})))
        return coll.map(fn).flatten().getInfo()["features"]

    def bucket(rows, keys, step, lo):
        grid = list(range(lo, 335, step))
        acc: dict[str, dict] = {}
        for r in rows:
            p = r["properties"]
            d = acc.setdefault(p["fid"], {k: [[] for _ in grid] for k in keys})
            j = min(len(grid) - 1, max(0, int((p["doy"] - lo) // step)))
            for k in keys:
                if p.get(k) is not None:
                    d[k][j].append(p[k])
        return grid, {fid: {k: [round(sum(v) / len(v), 4) if v else None for v in arr] for k, arr in d.items()}
                      for fid, d in acc.items()}

    s2_rows, s1_rows, corn_share = [], [], {}
    for b in range(0, len(fields), BATCH):
        chunk = fields[b: b + BATCH]
        fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Polygon([f["ring"]]), {"fid": f["fid"]}) for f in chunk])
        for r in corn.reduceRegions(fc, ee.Reducer.mean(), 30).getInfo()["features"]:
            corn_share[r["properties"]["fid"]] = r["properties"].get("mean") or 0
        s2_rows += series(s2, fc, ["ndvi", "ndwi"], 20)
        s1_rows += series(s1, fc, ["VV", "VH"], 20)
        log(f"  fetched {min(b + BATCH, len(fields))}/{len(fields)} fields")

    s2_grid, s2_ser = bucket(s2_rows, ["ndvi", "ndwi"], 5, 91)
    s1_grid, s1_ser = bucket(s1_rows, ["VV", "VH"], 6, 93)

    out = []
    for f in fields:
        fid, ring = f["fid"], f["ring"]
        if corn_share.get(fid, 0) < a.min_corn or fid not in s1_ser:
            continue
        vv, vh = s1_ser[fid]["VV"], s1_ser[fid]["VH"]
        for arr in (vv, vh):  # fill orbit gaps (radar has no cloud gaps)
            first = next((x for x in arr if x is not None), None)
            if first is None:
                break
            last = first
            for i, x in enumerate(arr):
                arr[i] = last = x if x is not None else last
        else:
            lat = sum(p[1] for p in ring) / len(ring)
            area = abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:]))) / 2 \
                * 111_320 ** 2 * math.cos(math.radians(lat)) / 1e4
            s2f = s2_ser.get(fid, {"ndvi": [None] * len(s2_grid), "ndwi": [None] * len(s2_grid)})
            out.append({"id": fid, "county_id": county_id, "county_name": county_name, "state": (a.state or "").upper(), "polygon": ring, "area_ha": round(area, 1), "label": f["label"],
                        "s2_doy": s2_grid, "ndvi": s2f["ndvi"], "ndwi": s2f["ndwi"], "s1_doy": s1_grid, "vv": vv, "vh": vh})

    OUT.parent.mkdir(exist_ok=True)
    existing = json.loads(OUT.read_text())["fields"] if OUT.exists() else []
    merged = [f for f in existing if f["county_id"] != county_id] + out
    OUT.write_text(json.dumps({"fields": merged}))
    log(f"Wrote {len(out)} corn fields for {county_id} -> {OUT}  (restart API with CORNEA_REBUILD=1)")


if __name__ == "__main__":
    main()
