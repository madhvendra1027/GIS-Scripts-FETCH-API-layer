# Hazard Zone Classification & Relocation Prioritization Platform

*(Smart India Hackathon 2026 — Problem Statement 26191)*

Identifies Red/Yellow/Green hazard zones across four hazard types — **flood**, **landslide**,
**coastal erosion**, and **cloudburst** — for locations in India, prioritizes vulnerable
habitations for relocation, and assesses the carrying capacity of candidate relocation sites.

## Repository structure

This repo has two parts that work together:

```
.
├── gis_fetcher/          # Installable package: fetches raw GIS/weather/hydrology data
│   ├── gis_fetcher/      #   from free public APIs (Open-Meteo, USGS, Overpass, etc.)
│   │   ├── core/         #   base classes, config loading, caching, registry
│   │   └── providers/    #   one module per data source
│   ├── config/providers.yaml
│   ├── examples/quickstart.py
│   └── tests/
│
├── hazard_platform/      # The pipeline, scoring engine, backend, and dashboard
│   ├── data_pipeline/    #   normalizes + cleans raw features into hazard readings
│   ├── ml_service/       #   weighted-overlay scoring per hazard (+ AHP weighting)
│   ├── backend/          #   zone classification, prioritization, FastAPI app
│   ├── frontend/         #   dashboard.html — interactive Leaflet map
│   ├── pipeline_runner.py
│   ├── zones.py
│   └── tests/
│
└── docs/                 # Decision log, weight justification, changelog
```

Each of `gis_fetcher/` and `hazard_platform/` also has its own `README.md` with
package-specific detail — this file is the entry point that ties them together.

## Prerequisites

- Python 3.10+
- pip
- Internet access (most data sources are free public APIs — see Configuration below)

## Installation

```bash
# 1. Clone
git clone https://github.com/madhvendra1027/GIS-Scripts-FETCH-API-layer.git
cd GIS-Scripts-FETCH-API-layer

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS/Linux

# 3. Install gis_fetcher as an editable package (so hazard_platform can import it)
pip install -e gis_fetcher/

# 4. Install hazard_platform's dependencies
pip install -r hazard_platform/requirements.txt

# 5. (Optional) install gis_fetcher's dev/test dependencies
pip install -r gis_fetcher/requirements-dev.txt
```

## Configuration

Copy the example env file and fill in only what you plan to use:

```bash
copy hazard_platform\.env.example hazard_platform\.env   # Windows
cp hazard_platform/.env.example hazard_platform/.env     # macOS/Linux
```

This project's design principle (see `docs/PROJECT_CHAT_LOG.md`) is **no fabricated data** —
every field either comes from a real source or is left honestly as `None`. In practice that
means most providers need **no API key at all**. The two that do:

| Variable | Needed for | If not set |
|---|---|---|
| `GOOGLE_FLOOD_API_KEY` | Google Flood Forecasting API — precise river gauge level + flood-status severity | A free GloFAS-based proxy (`fetch_flood_status_proxy.py`) is used automatically instead |
| A Google Earth Engine service-account key path | Any GEE-sourced static dataset — see `hazard_platform/STATIC_DATASETS.md` | That field stays `None` |

Provider-level tuning (retries, rate limits, default regions) lives in
`gis_fetcher/config/providers.yaml` with sane defaults already set — nothing there needs to
change to get started.

## Running it

**Run the tests first**, as a sanity check that installation worked:

```bash
pytest gis_fetcher/tests
cd hazard_platform && pytest
```

**Ingest and score a zone:**

```bash
cd hazard_platform
python pipeline_runner.py
```

See `example_run.py` for a minimal scripted example, and `zones.py` for how zones are defined.

**Start the backend API:**

```bash
cd hazard_platform
uvicorn backend.api:app --reload --port 8000
```

> If this errors, check the top of `backend/api.py` to confirm the FastAPI instance's variable
> name — adjust the command above to match.

**Open the dashboard:** with the backend running, open `hazard_platform/frontend/dashboard.html`
directly in a browser. Click anywhere on the map to fetch and classify that point live.

## Further reading

- `gis_fetcher/README.md` — the fetcher package, its providers, and CLI usage
- `hazard_platform/README.md` — the pipeline, scoring, and backend in detail
- `hazard_platform/STATIC_DATASETS.md` — static/CSV datasets used where no live API exists
- `docs/WEIGHT_JUSTIFICATION.md` — literature justification for the hazard-scoring weights
- `docs/PROJECT_CHAT_LOG.md` — running decision log
- `docs/CHANGELOG.md` — change history

## Project status

All four hazard scorers use documented, inspectable weighted-overlay formulas — not trained ML
models — since none of the four hazards yet has defensible zone-level historical outcome labels
to train on. Weights are being migrated to a formally AHP-derived, Consistency-Ratio-gated
system; see `docs/WEIGHT_JUSTIFICATION.md` for exactly what's automated and what's still a
documented placeholder.
