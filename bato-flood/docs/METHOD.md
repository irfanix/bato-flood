### How Bato works

**1. Where did the flood hit?** Bato compares Sentinel-1 radar images from **before and after** the event, taken from the **same orbit track** (same viewing angle, same time of day, normally 12 days apart). Radar sees through monsoon cloud.
- Calm water reflects the radar away, so it looks dark. A pixel that turns dark (VV drops ≥ 3 dB and ends below −16 dB) is **new flood water**.
- Mud, boulders and broken ground make the surface rough, so it looks brighter. A pixel on the valley floor whose VV rises ≥ 3 dB is **debris / scoured ground**.
- The whole-scene shift (for example, wet monsoon soil) is measured on the valley floor and removed first.
- Only terrain a flood can reach is analysed: up to 60 m above the nearest river (pre-event OSM rivers + Copernicus DEM, a light version of "height above nearest drainage").
- Slopes steeper than 30° are marked **not observable** (radar shadow and layover), rather than "safe".
- When the sky is clear, Sentinel-2 confirms it: water index (MNDWI) for new water, and vegetation loss (NDVI from > 0.35 to < 0.15) for debris. Cloud and shadow pixels are skipped using the scene classification layer.
- Up to two orbit tracks are combined. Pixels seen by both radar and optical get **high confidence**.

**2. What was damaged?** OpenStreetMap from **before** the event (ohsome API snapshot) is laid over the flood map:
- **Buildings:** a building is "hit" if flood or debris lies within 20 m.
- **Roads:** every road is cut into its segments and sampled every 10 m.
- **Bridges:** a bridge is "hit" if new water or debris touches it. The river that was always there does not count.

**3. Who is cut off?** The pre-event roads become a graph, and the flooded segments are removed. For each named village, Bato searches for the shortest road route to the nearest **town or hospital**:
- **CUT OFF:** there was a route before, and there is none now, even if every unclear road is assumed open.
- **UNCERTAIN:** the route depends on road the satellites could not see.
- **REACHABLE:** a route exists through roads seen as open. The new distance is shown if there is a detour.
- For each cut-off village, Bato shows **where the old route is first blocked**.

**AI component: situation-report copilot.**
- A facts table (F1 to F14) is computed by code.
- The one-page report in English and Nepali is filled from that table.
- The copilot (LLM via Groq) can only answer from the table, and every number in its answer is checked. If it writes any number that is not in the maps, the answer is **blocked** and the rule-based answer is shown instead.

**Bonus: flood path.** Steepest-descent tracing on the Copernicus DEM from any upstream point, with small pits skipped. It lists settlements within 600 m of the path and their distance downstream.

### What Bato cannot do (limitations)
- **No early warning.** Sentinel-1 revisits only every few days (6 to 12 days per track), so Bato cannot warn of a glacier collapse minutes ahead. The map shows the situation on the image date, not now.
- **Floods in gorges drain fast.** By the time of the first image after the event, water may be gone. Bato then relies on the debris signal, which is less certain than water.
- **Steep valleys hide ground from radar.** Slopes facing away from the satellite are in shadow, which is why the "not observable" class exists. Optical images need clear sky.
- **The map is only as good as OSM.** Unmapped villages, roads or bridges are invisible to Bato. Population is an estimate (buildings × 4.37 people, Nepal Census 2021 household size) unless OSM has a population tag.
- **Damage is not severity.** "Hit" means flood or debris reached the building or road at 20 m resolution. It does not tell destroyed from slightly damaged.
- **A road can be blocked without the river touching it.** A landslide on a hillside road is not detected.
- **Thresholds were not tuned on the case-study answer.** They are fixed in `bato/config.py` and listed in the app.
- This is an educational prototype, not an operational tool.
