# Bato: flood damage and cut-off villages from space

**Satellites measure, code counts, AI only words it.**

Multimodal AI Hackathon 2026, **Track B: Mapping Flood Damage from Space**.

*Bato* (बाटो) means "road" in Nepali. When roads, bridges and phone lines are gone, Bato uses free satellite data to tell rescuers three things for **any area and date**:

1. **Where the flood hit:** new water and debris, from Sentinel-1 radar before and after the event, confirmed by Sentinel-2 when the sky is clear.
2. **What was damaged:** pre-event OpenStreetMap buildings, roads and bridges laid over the flood map.
3. **Who is cut off:** villages that lost their road link to the nearest town or hospital, and where the route is first blocked.

The **AI component** is a **situation-report copilot**. It answers rescuers' questions and writes a one-page report in **English and Nepali**. Every number comes from the maps: each number the language model writes is checked against the facts table, and if any number is not there, the AI answer is blocked.

**Bonus:** trace the flood path down the valley from any upstream point, and list the settlements along it.

> Educational prototype, not an operational tool. Made with respect for the families affected by the August 2026 Trishuli disaster; no images of victims are used.

## Quick start (no install): Google Colab
Open `notebooks/Bato_Colab.ipynb` in Google Colab and choose **Runtime > Run all**. It installs everything, runs the tests, runs the Trishuli case study from raw satellite data, and shows the map and the report.

## Run on your own computer
Requires Python 3.10 to 3.12.

```bash
git clone https://github.com/irfanix/bato-flood
cd bato-flood
pip install -r requirements.txt
python -m pytest -q tests                      # offline tests on a synthetic valley

# Case study: Bhote Koshi - Trishuli corridor, 26 Aug 2026 (+ bonus flood path)
python -m bato run --preset trishuli --out case_study_trishuli --flow 85.375 28.27

# Any area and date (what the judges do on the day)
python -m bato run --bbox LON_MIN LAT_MIN LON_MAX LAT_MAX --date YYYY-MM-DD --out runs/my_area

# Case-study check against Copernicus EMS EMSR927 (validation only)
python -m bato run --preset trishuli --out case_study_trishuli --ems EMSR927_package.zip

# Dashboard
streamlit run app.py
```

**Outputs** (in the `--out` folder):

| File | Contents |
|---|---|
| `map.html` | Interactive map |
| `situation_report.html` | One-page report, English and Nepali. Print to PDF |
| `situation_report_en.md`, `situation_report_ne.md` | The same report as text |
| `facts.json` | Every number, with its source layer |
| `settlements.csv` | Access status of each village |
| `flood_classes.tif` | GeoTIFF: classes and confidence |
| `bridges.geojson`, `blocked_roads.geojson`, `buildings_hit.geojson` | Damaged objects |
| `ems_comparison.json` / `.png` | Comparison with EMSR927 |

**Optional:** set `GROQ_API_KEY` (environment variable, or `.streamlit/secrets.toml`) to turn on the LLM copilot. Without it, the copilot uses rule-based answers, with the same numbers.

**If Planetary Computer is unavailable:** download Sentinel-1 VV GeoTIFFs (same orbit, before and after) from the Copernicus Browser (dataspace.copernicus.eu) and pass `--s1-pre pre.tif --s1-post post.tif`.

## How it works
See [docs/METHOD.md](docs/METHOD.md). It covers every threshold and every limitation, and the same text appears in the app. In short:

```
area + date
 ├─ OSM pre-event snapshot (ohsome, ≤ 27 Jul 2026): rivers, roads, bridges, buildings, villages, hospitals
 ├─ Copernicus DEM → slope + height above nearest river → where a flood can reach
 ├─ Sentinel-1 RTC, same orbit track before/after → new water (dark) + debris (bright)
 ├─ Sentinel-2 L2A before/after (if clear) → MNDWI water + NDVI loss
 └─ combine → flood map (new water / debris / river / not observable)
       ├─ × buildings, roads, bridges → damage
       ├─ road graph minus flooded segments → CUT OFF / UNCERTAIN / REACHABLE per village
       └─ facts table F1..F14 → report (EN/NE) + copilot with number guard
```

## Case study: Trishuli, 26 August 2026 (EMSR927)
*Results are filled in from `case_study_trishuli/facts.json` and `ems_comparison.json`.*

| | Bato | Copernicus EMS (EMSR927) |
|---|---|---|
| Flood + debris area (km², inside the EMS area of interest) | | |
| Overlap (km²) / precision / recall / F1 / IoU | | |

## Data rules followed
- **Inputs:**
  - Sentinel-1 and Sentinel-2. Free and open Copernicus data, accessed via Microsoft Planetary Computer.
  - Copernicus DEM GLO-30.
  - OpenStreetMap as it was **before** the event. The ohsome snapshot date is `min(event − 1 day, 2026-07-27)`.
- **Never used as inputs:** Copernicus EMS, UNOSAT or any other published damage maps, and post-event OSM edits. EMS maps are read only by `bato/compare.py`, after the analysis, to score it.
- Basemaps in the web map are for display only. They are never analysed.
- No training dataset was used, because we chose the copilot as the AI component.

## Required attribution
- Contains modified Copernicus Sentinel data 2026.
- Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.
- © OpenStreetMap contributors (ODbL). Pre-event history via the ohsome API (HeiGIT).
- Validation only: European Union, Copernicus Emergency Management Service data (EMSR927).

## AI tools used (disclosure)
- **Claude (Anthropic):** helped design the method, write the code, tests and documentation, and the Nepali report templates. The Nepali text should be reviewed by a native speaker.
- **Groq (openai/gpt-oss-120b):** the runtime LLM behind the copilot. It can only phrase answers; the number guard in `bato/report.py` blocks any number it invents.
- The team ran, checked and tested the system, and is responsible for it.

## License
Code: MIT. Derived OSM data: ODbL.
