"""HarvestLens API."""
from __future__ import annotations

import csv
import io
import json
import math
import os
import pickle
import time
import zlib
from collections import Counter
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from pydantic import Field as PField

from . import env  # noqa: F401  (must load before copilot reads its settings)
from . import copilot
from . import features as F
from . import map_agent
from .model import HarvestClassifier, doy_to_date, explain
from .simulate import COUNTIES, COUNTY_BY_ID, County, Field, generate_county, simulate_series

CACHE = Path(__file__).resolve().parent.parent / ".cache" / "state-v1.pkl"
REAL_DATA = Path(__file__).resolve().parent.parent / "data" / "fields_gee.json"

app = FastAPI(title="HarvestLens API", version="0.1.0")
# Local origins only: /api/agent spends the NVIDIA key, so arbitrary websites must not be able to call it.
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"], allow_methods=["*"], allow_headers=["*"])

STATE: dict = {}
REAL_COUNTIES: dict[str, tuple[str, str]] = {}
METRICS = {"queries": 0, "query_ms": [], "exports": 0, "uploads": 0, "intents": Counter()}


# ----------------------------------------------------------------------------- pipeline

def classify_fields(clf: HarvestClassifier, fields: list[Field]) -> list[dict]:
    feats = [F.extract(f.s2_doy, f.ndvi, f.ndwi, f.s1_doy, f.vv, f.vh) for f in fields]
    if not feats:
        return []
    X = np.array([F.to_vector(x) for x in feats])
    p, lo, hi = clf.predict_proba(X)
    recs = []
    for f, ft, pi, l, h in zip(fields, feats, p, lo, hi):
        label = "silage" if pi >= 0.5 else "grain"
        conf = float(max(pi, 1 - pi))
        ex = explain(ft, label, float(pi), float(l), float(h), clf.reference)
        recs.append({
            "id": f.id, "county_id": f.county_id, "polygon": f.polygon, "area_ha": f.area_ha,
            "label": label, "prob_silage": round(float(pi), 4),
            "interval": [round(float(l), 4), round(float(h), 4)], "confidence": round(conf, 4),
            "harvest_doy": ft["harvest_doy"], "harvest_date": doy_to_date(ft["harvest_doy"]),
            "features": {k: (None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 4))
                         for k, v in ft.items()},
            "evidence": ex["evidence"], "flags": ex["flags"], "true_label": f.true_label,
            "series": {
                "s2_doy": f.s2_doy.tolist(),
                "ndvi": [None if np.isnan(v) else round(float(v), 4) for v in f.ndvi],
                "ndwi": [None if np.isnan(v) else round(float(v), 4) for v in f.ndwi],
                "s1_doy": f.s1_doy.tolist(),
                "vv": [round(float(v), 3) for v in f.vv], "vh": [round(float(v), 3) for v in f.vh],
            },
            "source": f.meta.get("source", "simulated"),
        })
    return recs


def load_real_fields() -> list[Field]:
    """Fields produced by pipeline/ingest_gee.py (real Sentinel-1/2 time series)."""
    if not REAL_DATA.exists():
        return []
    raw = json.loads(REAL_DATA.read_text())
    out = []
    for r in raw["fields"]:
        REAL_COUNTIES[r["county_id"]] = (r.get("county_name") or r["county_id"], r.get("state", ""))
        out.append(Field(
            id=r["id"], county_id=r["county_id"], polygon=r["polygon"], area_ha=r["area_ha"],
            true_label=r.get("label") or "unknown",
            s2_doy=np.array(r["s2_doy"], float), ndvi=np.array([np.nan if v is None else v for v in r["ndvi"]], float),
            ndwi=np.array([np.nan if v is None else v for v in r["ndwi"]], float),
            s1_doy=np.array(r["s1_doy"], float), vv=np.array(r["vv"], float), vh=np.array(r["vh"], float),
            meta={"source": "sentinel"},
        ))
    return out


def build_state():
    stale = REAL_DATA.exists() and CACHE.exists() and REAL_DATA.stat().st_mtime > CACHE.stat().st_mtime
    if CACHE.exists() and not stale and not os.environ.get("HARVESTLENS_REBUILD"):
        return pickle.loads(CACHE.read_bytes())
    t = time.time()
    clf = HarvestClassifier().fit()
    fields = [f for c in COUNTIES for f in generate_county(c)]
    recs = classify_fields(clf, fields) + classify_fields(clf, load_real_fields())
    y = np.array([r["true_label"] == "silage" for r in recs if r["source"] == "simulated"])
    yhat = np.array([r["label"] == "silage" for r in recs if r["source"] == "simulated"])
    state = {
        "clf": clf, "fields": {r["id"]: r for r in recs},
        "real_counties": dict(REAL_COUNTIES),
        "demo_accuracy": round(float((y == yhat).mean()), 4) if len(y) else None,
        "build_seconds": round(time.time() - t, 1),
    }
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_bytes(pickle.dumps(state))
    return state


@app.on_event("startup")
def _startup():
    STATE.update(build_state())


# ----------------------------------------------------------------------------- helpers

def county_info(c: County) -> dict:
    pts = [p for r in STATE["fields"].values() if r["county_id"] == c.id for p in r["polygon"]]
    bbox = [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)] if pts else None
    return {"id": c.id, "name": c.name, "state": c.state, "center": c.center, "bbox": bbox}


def all_counties() -> list[dict]:
    extra = sorted({r["county_id"] for r in STATE["fields"].values()} - set(COUNTY_BY_ID))
    out = [county_info(c) for c in COUNTIES]
    real = STATE.get("real_counties", {})
    for cid in extra:
        name, st = real.get(cid, (cid.replace("-", " ").title(), ""))
        out.append(county_info(County(cid, name, st, (0, 0), 0, 0, 0, 0)))
    return out


def select(counties: list[str] | None = None, harvest_type: str | None = None,
           min_conf: float | None = None, max_conf: float | None = None, anomalies: bool = False) -> list[dict]:
    out = []
    for r in STATE["fields"].values():
        if counties and r["county_id"] not in counties:
            continue
        if harvest_type and r["label"] != harvest_type:
            continue
        if min_conf is not None and r["confidence"] < min_conf:
            continue
        if max_conf is not None and r["confidence"] > max_conf:
            continue
        if anomalies and not r["flags"]:
            continue
        out.append(r)
    return out


def light(r: dict) -> dict:
    return {k: r[k] for k in ("id", "county_id", "label", "prob_silage", "confidence", "interval",
                              "area_ha", "harvest_date", "flags", "source")}


def to_geojson(recs: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": i, "geometry": {"type": "Polygon", "coordinates": [r["polygon"]]},
         "properties": light(r)} for i, r in enumerate(recs)]}


def summarize(recs: list[dict]) -> dict:
    n = len(recs)
    sil = [r for r in recs if r["label"] == "silage"]
    area = sum(r["area_ha"] for r in recs)
    sil_area = sum(r["area_ha"] for r in sil)
    hist = np.histogram([r["confidence"] for r in recs], bins=[0.5, 0.6, 0.7, 0.8, 0.9, 1.0001])[0].tolist() if n else [0] * 5
    return {
        "fields": n, "silage_fields": len(sil), "grain_fields": n - len(sil),
        "area_ha": round(area, 1), "silage_area_ha": round(sil_area, 1),
        "silage_share_area": round(sil_area / area, 4) if area else 0,
        "mean_confidence": round(float(np.mean([r["confidence"] for r in recs])), 4) if n else None,
        "flagged": sum(1 for r in recs if r["flags"]),
        "confidence_hist": {"bins": ["50-60", "60-70", "70-80", "80-90", "90-100"], "counts": hist},
    }


def parse_counties(county: str | None) -> list[str] | None:
    return [c for c in county.split(",") if c] if county else None


# ----------------------------------------------------------------------------- routes

@app.get("/api/health")
def health():
    return {"ok": True, "fields": len(STATE.get("fields", {})), "llm": copilot.llm_available()}


@app.get("/api/meta")
def meta():
    return {
        "counties": all_counties(),
        "model": {**STATE["clf"].validation, "demo_accuracy": STATE["demo_accuracy"],
                  "features": F.FEATURE_NAMES, "ensemble_size": len(STATE["clf"].models)},
        "llm": {"enabled": copilot.llm_available(), "model": copilot.NIM_MODEL},
        "data_source": "mixed" if REAL_DATA.exists() else "simulated",
    }


@app.get("/api/fields")
def fields(county: str | None = None, type: str | None = None, min_conf: float | None = None,
           max_conf: float | None = None, anomalies: bool = False):
    return to_geojson(select(parse_counties(county), type, min_conf, max_conf, anomalies))


@app.get("/api/fields/{fid}")
def field_detail(fid: str):
    r = STATE["fields"].get(fid.upper())
    if not r:
        raise HTTPException(404, f"Field {fid} not found")
    lons = [p[0] for p in r["polygon"]]
    lats = [p[1] for p in r["polygon"]]
    pad = 0.002
    bbox = f"{min(lons)-pad},{min(lats)-pad},{max(lons)+pad},{max(lats)+pad}"
    thumb = ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
             f"?bbox={bbox}&bboxSR=4326&imageSR=3857&size=320,320&format=jpg&f=image")
    return {**r, "thumbnail": thumb}


@app.get("/api/summary")
def summary(county: str | None = None):
    ids = parse_counties(county) or [c["id"] for c in all_counties()]
    per = {}
    for c in all_counties():
        if c["id"] in ids:
            per[c["id"]] = {"name": c["name"], "state": c["state"], **summarize(select([c["id"]]))}
    return {"overall": summarize(select(ids)), "counties": per}


@app.get("/api/export")
def export(format: str = Query("csv", pattern="^(csv|geojson)$"), county: str | None = None,
           type: str | None = None, min_conf: float | None = None, max_conf: float | None = None,
           anomalies: bool = False):
    METRICS["exports"] += 1
    recs = select(parse_counties(county), type, min_conf, max_conf, anomalies)
    if format == "geojson":
        fc = to_geojson(recs)
        for feat, r in zip(fc["features"], recs):
            feat["properties"]["features"] = r["features"]
        return Response(json.dumps(fc), media_type="application/geo+json",
                        headers={"Content-Disposition": "attachment; filename=harvestlens_fields.geojson"})
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["field_id", "county_id", "label", "prob_silage", "ci_low", "ci_high", "confidence", "area_ha",
            "harvest_date", "flags", "centroid_lon", "centroid_lat", "source"] + F.FEATURE_NAMES
    w.writerow(cols)
    for r in recs:
        ring = r["polygon"][:-1]
        w.writerow([r["id"], r["county_id"], r["label"], r["prob_silage"], *r["interval"], r["confidence"],
                    r["area_ha"], r["harvest_date"], "|".join(r["flags"]),
                    round(sum(p[0] for p in ring) / len(ring), 6), round(sum(p[1] for p in ring) / len(ring), 6),
                    r["source"]] + [r["features"][k] for k in F.FEATURE_NAMES])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=harvestlens_fields.csv"})


class CopilotIn(BaseModel):
    query: str


@app.post("/api/copilot")
def copilot_route(body: CopilotIn):
    t = time.time()
    counties = all_counties()
    plan, planner = copilot.plan_query(body.query, counties)
    METRICS["queries"] += 1
    METRICS["intents"][plan["intent"]] += 1
    names = {c["id"]: f'{c["name"]}, {c["state"]}'.strip(", ") for c in counties}
    resp = {"plan": plan, "planner": planner, "action": None}

    if plan.get("clarification"):
        resp.update(answer=plan["clarification"], narrator="rules", needs_clarification=True)
    elif plan["intent"] == "explain" and plan.get("field_id"):
        r = STATE["fields"].get(plan["field_id"])
        if not r:
            resp.update(answer=f"I couldn't find field {plan['field_id']}.", narrator="rules")
        else:
            facts = {"field": r["id"], "county": names.get(r["county_id"]), "label": r["label"],
                     "confidence": r["confidence"], "prob_silage_90pct_interval": r["interval"],
                     "evidence": r["evidence"], "flags": r["flags"]}
            ev = " ".join(e["detail"] for e in r["evidence"][:3])
            fb = (f"{r['id']} is classified {r['label']} with {r['confidence']:.0%} confidence "
                  f"(P(silage) 90% interval {r['interval'][0]:.2f}–{r['interval'][1]:.2f}). {ev}"
                  + (f" Flags: {', '.join(r['flags'])}." if r["flags"] else ""))
            ans, narrator = copilot.narrate(body.query, facts, fb)
            resp.update(answer=ans, narrator=narrator, action={"type": "select_field", "field_id": r["id"]})
    else:
        cids = plan["counties"] or None
        recs = select(cids, plan["harvest_type"], plan["min_confidence"], plan["max_confidence"], plan["anomalies_only"])
        scope = ", ".join(names[c] for c in cids) if cids else "all counties"
        filt = {"county": ",".join(cids or []), "type": plan["harvest_type"], "min_conf": plan["min_confidence"],
                "max_conf": plan["max_confidence"], "anomalies": plan["anomalies_only"]}
        s = summarize(recs)
        facts = {"scope": scope, "filters": filt, "summary": s}
        if plan["intent"] == "compare" or (plan["intent"] == "summary" and (not cids or len(cids) > 1)):
            per = {names[c["id"]]: summarize(select([c["id"]], None, plan["min_confidence"], plan["max_confidence"],
                                                    plan["anomalies_only"])) for c in counties if not cids or c["id"] in cids}
            facts["per_county"] = {k: {kk: v[kk] for kk in ("fields", "silage_fields", "silage_share_area", "mean_confidence", "flagged")}
                                   for k, v in per.items()}
            ranked = sorted(facts["per_county"].items(), key=lambda kv: -kv[1]["silage_share_area"])
            fb = "Silage share of corn area: " + "; ".join(
                f"{k} {v['silage_share_area']:.0%} ({v['silage_fields']}/{v['fields']} fields)" for k, v in ranked) + "."
            resp["action"] = {"type": "compare", "filters": filt}
        else:
            what = f"{plan['harvest_type']} fields" if plan["harvest_type"] else "corn fields"
            if plan["anomalies_only"]:
                what = f"flagged {what}"
            conf = f" at ≥{plan['min_confidence']:.0%} confidence" if plan["min_confidence"] else ""
            fb = (f"Found {s['fields']} {what}{conf} in {scope} covering {s['area_ha']:,.0f} ha"
                  + (f"; silage is {s['silage_share_area']:.0%} of that area" if not plan["harvest_type"] else "")
                  + (f". Mean confidence {s['mean_confidence']:.0%}" if s["mean_confidence"] else "")
                  + (f"; {s['flagged']} fields carry review flags." if s["flagged"] else "."))
            if plan["anomalies_only"] and recs:
                top = Counter(f for r in recs for f in r["flags"]).most_common(3)
                fb += " Most common issues: " + ", ".join(f"{k.replace('_', ' ')} ({v})" for k, v in top) + "."
                facts["flag_counts"] = dict(top)
            resp["action"] = {"type": "export" if plan["intent"] == "export" else "filter", "filters": filt,
                              "export_format": plan.get("export_format")}
        ans, narrator = copilot.narrate(body.query, facts, fb)
        resp.update(answer=ans, narrator=narrator, summary=s)
    ms = (time.time() - t) * 1000
    METRICS["query_ms"].append(ms)
    resp["latency_ms"] = round(ms, 1)
    return resp


class AgentTurn(BaseModel):
    role: str = PField(pattern="^(user|assistant)$")
    content: str = PField(max_length=4000)


class MapState(BaseModel):
    county: list[str] = []
    type: str | None = None
    min_conf: float | None = None
    max_conf: float | None = None
    anomalies: bool = False
    selected_field: str | None = None


class AgentIn(BaseModel):
    query: str = PField(min_length=1, max_length=1000)
    history: list[AgentTurn] = PField(default=[], max_length=12)
    map_state: MapState = MapState()


@app.post("/api/agent")
def agent_route(body: AgentIn):
    """Stream one Nemotron map-agent run as server-sent events."""
    if not copilot.llm_available():
        raise HTTPException(503, "NVIDIA_API_KEY is not set on the server")
    METRICS["agent_runs"] = METRICS.get("agent_runs", 0) + 1
    tools = map_agent.MapTools(STATE["fields"], all_counties(), select, summarize)
    events = map_agent.run(body.query, [t.model_dump() for t in body.history], body.map_state.model_dump(), tools)
    return StreamingResponse((f"data: {json.dumps(e, default=str)}\n\n" for e in events), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """Ad-hoc analysis on user-supplied boundaries (GeoJSON Polygon/MultiPolygon features).

    Without Earth Engine credentials the time series are simulated (deterministically from the
    geometry) so the workflow can be demonstrated; results are tagged source=simulated-upload.
    """
    try:
        gj = json.loads(await file.read())
    except json.JSONDecodeError:
        raise HTTPException(400, "File is not valid GeoJSON")
    feats = gj.get("features") or ([gj] if gj.get("type") == "Feature" else [])
    fields_new = []
    for i, ft in enumerate(feats[:500]):
        g = ft.get("geometry") or {}
        if g.get("type") == "Polygon":
            ring = g["coordinates"][0]
        elif g.get("type") == "MultiPolygon":
            ring = g["coordinates"][0][0]
        else:
            continue
        ring = [[float(p[0]), float(p[1])] for p in ring]
        lon = sum(p[0] for p in ring) / len(ring)
        lat = sum(p[1] for p in ring) / len(ring)
        seed = zlib.crc32(json.dumps(ring).encode())
        rng = np.random.default_rng(seed)
        near = min(COUNTIES, key=lambda c: (c.center[0] - lon) ** 2 + (c.center[1] - lat) ** 2)
        label = "silage" if rng.random() < near.silage_share else "grain"
        ndvi, ndwi, vv, vh, _ = simulate_series(rng, label, near)
        from .simulate import S1_DOYS, S2_DOYS
        area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))) / 2 * (111_320 ** 2) * math.cos(math.radians(lat)) / 10_000
        fields_new.append(Field(id=f"UPL-{i + 1:04d}", county_id="uploaded", polygon=ring, area_ha=round(area, 1),
                                true_label=label, s2_doy=S2_DOYS, ndvi=ndvi, ndwi=ndwi, s1_doy=S1_DOYS, vv=vv, vh=vh,
                                meta={"source": "simulated-upload"}))
    if not fields_new:
        raise HTTPException(400, "No Polygon features found")
    for k in [k for k, r in STATE["fields"].items() if r["county_id"] == "uploaded"]:
        del STATE["fields"][k]
    for r in classify_fields(STATE["clf"], fields_new):
        STATE["fields"][r["id"]] = r
    METRICS["uploads"] += 1
    return {"county_id": "uploaded", "fields": len(fields_new), "summary": summarize(select(["uploaded"]))}


@app.get("/api/metrics")
def metrics():
    q = METRICS["query_ms"]
    return {"queries": METRICS["queries"], "exports": METRICS["exports"], "uploads": METRICS["uploads"],
            "median_query_ms": round(float(np.median(q)), 1) if q else None, "intents": dict(METRICS["intents"]),
            "confidence_hist": summarize(list(STATE["fields"].values()))["confidence_hist"]}
