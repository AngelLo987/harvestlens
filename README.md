# HarvestLens

Field-level **silage vs. grain** corn classification from Sentinel-2 optical and Sentinel-1 radar time series, with an AI copilot and an interactive map dashboard. Built for the USDA *Hack the Harvest* challenge.

## Quick start

```bash
./dev.sh            # first run installs deps; API on :8000, homepage on http://localhost:3000, map app on /app
```

Or run each piece on its own:

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000      # first boot trains the model (~45 s), then it is cached in .cache/
cd frontend && npm install && npm run dev       # http://localhost:3000
```

To turn on **NVIDIA Nemotron**, copy `backend/.env.example` to `backend/.env`, set `NVIDIA_API_KEY` (the file is git-ignored), and restart the API. `NEMOTRON_MODEL` optionally overrides the default, `nvidia/nemotron-3.5-super-vl-120b-a12b`. Without a key the map agent shows as offline and the side-panel copilot uses a deterministic rule-based planner.

### Nemotron map agent

The panel on the map runs a streaming tool-calling loop (`backend/app/map_agent.py`, `POST /api/agent`, server-sent events). Nemotron reads data through `find_fields`, `compare_counties` and `get_field`, and operates the map through `set_map_filters`, `fly_to`, `highlight_fields`, `open_field_panel` and `clear_map`. Tool arguments are validated on the server and invalid calls go back to the model to correct, with a cap of 8 steps per run. The panel shows reasoning, each tool call and result, tokens, time to first token and total time.

To test the loop offline, run `uvicorn tests.mock_nim:app --port 8799` and point the API at it with `NVIDIA_BASE_URL=http://127.0.0.1:8799/v1`, plus a `NEMOTRON_MODEL` containing "mock" so it can't be mistaken for the real model.

## How it works

| Stage | What it does | Code |
|---|---|---|
| Ingestion | Per-field S2 NDVI/NDWI (SCL cloud mask, 5-day bins) and S1 VV/VH (dB, 6-day bins), corn mask from the USDA CDL | `backend/pipeline/ingest_gee.py` |
| Features | 13 phenology features: harvest date (optical drop, with SAR fallback under cloud), NDVI/NDWI at cut, senescence slope, post-harvest VH residue, optical coverage… | `backend/app/features.py` |
| Model | 12-member bagged `HistGradientBoostingClassifier` (handles missing data natively) → P(silage) plus a 90% interval | `backend/app/model.py` |
| Explanations | Evidence per signal plus review flags: low confidence, ensemble disagreement, cloud gaps, conflicting signals, optical/SAR timing mismatch | `backend/app/model.py` |
| Copilot | NL → JSON plan (filter / compare / summary / explain / export / anomalies), then a grounded narrative over computed facts | `backend/app/copilot.py` |
| Dashboard | MapLibre map with Esri imagery, filters, drill-down charts, county dashboard, CSV/GeoJSON export, boundary upload | `frontend/` |

**The agronomic signal:** silage is chopped whole-plant at ~65% moisture in late August to mid September while the canopy is still green. That shows up as an abrupt NDVI/NDWI drop from a high level and bare-soil radar afterwards. Grain corn dries down in the field and is combined in October–November, so it shows a gradual senescence first and stover-elevated VH backscatter after harvest.

## API

`GET /api/meta` · `GET /api/fields?county=&type=&min_conf=&max_conf=&anomalies=` (GeoJSON) · `GET /api/fields/{id}` · `GET /api/summary` · `GET /api/export?format=csv|geojson&…` · `POST /api/copilot {query}` · `POST /api/upload` (GeoJSON file) · `GET /api/metrics` (query count, latency, exports, confidence distribution)

## ⚠️ Data honesty

The bundled demo uses **simulated** Sentinel-1/2 time series for ~1,800 synthetic fields placed in four real counties (Yolo CA, Marathon WI, Story IA, Lancaster PA). The field polygons don't line up with real fields on the imagery, and the reported 96.5% accuracy is measured against simulated labels. Treat it as a pipeline check, not evidence for real-world accuracy.

To run on real imagery:

```bash
cd backend && .venv/bin/pip install earthengine-api && earthengine authenticate
EE_PROJECT=<gcp-project> .venv/bin/python -m pipeline.ingest_gee --boundaries fields.geojson --county-id marathon-wi --year 2025
HARVESTLENS_REBUILD=1 .venv/bin/uvicorn app.main:app --port 8000
```

Add a `label` property (silage/grain) to the boundary features to get real validation numbers. Real ground truth (e.g. NASS survey or FSA-reported silage acreage) is the most important next step for the >85% accuracy goal. Uploaded boundaries in the UI also get simulated series unless they were ingested through Earth Engine.
