"""End-to-end run: area + date -> flood map -> damage -> cut-off -> facts -> report -> dashboard."""
from __future__ import annotations

import csv
import json
import logging
import os
import time

import numpy as np

from . import flood, impact, cutoff, report, osm, satellite
from .config import Settings, ATTRIBUTION, HOUSEHOLD_SIZE

log = logging.getLogger("bato")


class Run:
    """Holds every layer of one analysis so the dashboard and copilot can use them."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def run(s: Settings, area_name="Custom area", out_dir="out", s1_pre=None, s1_post=None,
        progress=lambda msg, frac: None) -> Run:
    os.makedirs(out_dir, exist_ok=True)
    timings = {}
    t = time.time()

    def step(name, frac):
        nonlocal t
        now = time.time()
        timings[name] = round(now - t, 1)
        t = now
        progress(name, frac)
        log.info("%s (%.1fs)", name, timings[name])

    gb = satellite.make_geobox(s.bbox, s.resolution_m)
    grid = satellite.Grid.from_geobox(gb)
    # ---- OSM first (pre-event snapshot): rivers define "height above river" for the flood mask
    snap = s.snapshot()
    progress(f"OpenStreetMap snapshot {snap} (pre-event)", 0.03)
    data = osm.fetch(s.bbox, snap, s.osm_buffer_km, s.include_tracks, s.include_paths)
    step("OpenStreetMap pre-event snapshot", 0.08)
    progress("Loading elevation (Copernicus DEM)", 0.09)
    dem = satellite.load_dem(s.bbox, gb)
    river = rasterize_lines(data["rivers"], grid)
    slope, rel = flood.terrain(dem, s.resolution_m, s.valley_window_m, river=river)
    valley = np.isfinite(rel) & (rel <= s.max_rel_elev_m)
    steep = ~np.isfinite(slope) | (slope > s.max_slope_deg)
    step("Elevation and terrain masks", 0.15)

    # ---- Sentinel-1 radar
    s1_results, s1_meta = [], []
    if s1_pre and s1_post:
        pre = satellite.load_local_geotiff(s1_pre, gb)
        post = satellite.load_local_geotiff(s1_post, gb)
        s1_results.append(flood.s1_change(pre, post, valley, steep, s))
        s1_meta.append(dict(pre_date="local file", post_date="local file", direction="user GeoTIFF", coverage=None))
    else:
        progress("Searching Sentinel-1 radar pairs (same orbit track)", 0.15)
        for pair in satellite.find_s1_pairs(s.bbox, s.event_date, s.s1_days_before, s.s1_days_after):
            pre = satellite.load_s1(pair["pre"], gb)
            post = satellite.load_s1(pair["post"], gb)
            r = flood.s1_change(pre["vv"], post["vv"], valley, steep, s)
            s1_results.append(r)
            s1_meta.append(dict(pre_date=pair["pre_date"], post_date=pair["post_date"],
                                direction=f"orbit {pair['track'][0]} {pair['track'][1]}",
                                coverage=pair["coverage"], gap_days=pair["gap_days"],
                                scene_shift_db=round(r["shift_db"], 2)))
    step("Sentinel-1 change detection", 0.40)

    # ---- Sentinel-2 optical (when the sky is clear)
    s2_res, s2_pre_d, s2_post_d = None, None, None
    try:
        pre2, post2 = satellite.find_s2(s.bbox, s.event_date, s.s2_days_before, s.s2_days_after, s.s2_max_cloud)
        if pre2 and post2:
            a = satellite.load_s2(pre2["items"], gb)
            b = satellite.load_s2(post2["items"], gb)
            s2_res = flood.s2_change(a, b, valley, s)
            s2_pre_d, s2_post_d = pre2["date"], post2["date"]
    except Exception as e:  # optical is optional
        log.warning("Sentinel-2 skipped: %s", e)
    step("Sentinel-2 optical check", 0.55)

    cls, conf = flood.combine(s1_results, s2_res, dem, slope, rel, s)
    fstats = flood.area_stats(cls, conf, s.resolution_m)
    step("Flood map combined", 0.6)

    proj = impact.Projector(grid)
    blds = impact.buildings(cls, proj, data["buildings"], s.hit_buffer_px)
    edges = impact.roads(cls, proj, data["roads"], s.hit_buffer_px, s.road_sample_m)
    istats, bridges = impact.summarise(blds, edges, data["roads"])
    step("Damage overlay", 0.82)

    sett, cstats, dests = cutoff.analyse(edges, data["places"], data["health"], proj, s.bbox, s,
                                         buildings=blds, household=HOUSEHOLD_SIZE)
    step("Cut-off analysis", 0.9)

    meta = dict(area_name=area_name, event_date=s.event_date, bbox=list(s.bbox), osm_snapshot=snap,
                s1_pairs=s1_meta, s2_pre=s2_pre_d, s2_post=s2_post_d, settings=s.to_dict(),
                attribution=ATTRIBUTION)
    facts = report.build_facts(meta, fstats, istats, bridges, sett, cstats)
    facts["flood_stats"], facts["impact_stats"], facts["cutoff_stats"] = fstats, istats, cstats
    facts["timings_s"] = timings

    R = Run(settings=s, grid=grid, geobox=gb, dem=dem, slope=slope, rel=rel, cls=cls, conf=conf,
            proj=proj, osm=data, buildings=blds, edges=edges, bridges=bridges, settlements=sett,
            destinations=dests, facts=facts, out_dir=out_dir, s1_meta=s1_meta)
    write_outputs(R)
    step("Outputs written", 1.0)
    return R


def rasterize_lines(lines, grid):
    from rasterio.features import rasterize
    from shapely.geometry import LineString, mapping
    from pyproj import Transformer

    if not lines:
        return np.zeros(grid.shape, bool)
    tr = Transformer.from_crs(4326, grid.crs, always_xy=True)
    shapes = []
    for c in lines:
        c = np.asarray(c)
        x, y = tr.transform(c[:, 0], c[:, 1])
        shapes.append((mapping(LineString(np.c_[x, y])), 1))
    return rasterize(shapes, out_shape=grid.shape, transform=grid.transform, fill=0,
                     all_touched=True, dtype="uint8").astype(bool)


# ------------------------------------------------------------------ outputs
def overlay_4326(R):
    """Warp the class raster to lon/lat and return (rgba, bounds) for web maps."""
    import rasterio
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    from rasterio.transform import array_bounds

    h, w = R.cls.shape
    left, bottom, right, top = array_bounds(h, w, R.grid.transform)
    tr, ww, hh = calculate_default_transform(R.grid.crs, "EPSG:4326", w, h, left, bottom, right, top)
    dst = np.full((hh, ww), flood.NODATA, np.uint8)
    reproject(R.cls, dst, src_transform=R.grid.transform, src_crs=R.grid.crs, dst_transform=tr,
              dst_crs="EPSG:4326", resampling=Resampling.nearest, src_nodata=None)
    west, south, east, north = array_bounds(hh, ww, tr)
    return flood.to_rgba(dst), [[south, west], [north, east]]


def write_outputs(R):
    import rasterio
    from PIL import Image

    o = R.out_dir
    with rasterio.open(os.path.join(o, "flood_classes.tif"), "w", driver="GTiff", height=R.cls.shape[0],
                       width=R.cls.shape[1], count=2, dtype="uint8", crs=R.grid.crs,
                       transform=R.grid.transform, compress="deflate", nodata=255) as dst:
        dst.write(R.cls, 1)
        dst.write(R.conf, 2)
        dst.set_band_description(1, "class: 0 none,1 river,2 new water,3 debris,254 not observable,255 no data")
        dst.set_band_description(2, "confidence: 1 one sensor, 2 radar+optical")
    with rasterio.open(os.path.join(o, "dem.tif"), "w", driver="GTiff", height=R.dem.shape[0],
                       width=R.dem.shape[1], count=1, dtype="float32", crs=R.grid.crs,
                       transform=R.grid.transform, compress="deflate", predictor=3) as dst:
        dst.write(R.dem.astype("float32"), 1)
    with open(os.path.join(o, "places.json"), "w", encoding="utf-8") as f:
        json.dump(R.osm["places"], f, ensure_ascii=False)
    rgba, bounds = overlay_4326(R)
    Image.fromarray(rgba, "RGBA").save(os.path.join(o, "flood_overlay.png"))
    R.overlay_bounds = bounds
    with open(os.path.join(o, "facts.json"), "w", encoding="utf-8") as f:
        json.dump(dict(R.facts, overlay_bounds=bounds), f, ensure_ascii=False, indent=1, default=str)
    for lang in ("en", "ne"):
        with open(os.path.join(o, f"situation_report_{lang}.md"), "w", encoding="utf-8") as f:
            f.write(report.report_markdown(R.facts, lang))
    with open(os.path.join(o, "settlements.csv"), "w", newline="", encoding="utf-8") as f:
        cols = ["status", "name", "name_ne", "place", "people", "people_source", "destination", "km_before",
                "km_now", "blocked_at", "lat", "lon", "osm_id"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for s in R.settlements:
            w.writerow(s)
    gj = lambda feats: {"type": "FeatureCollection", "features": feats}
    with open(os.path.join(o, "bridges.geojson"), "w") as f:
        json.dump(gj([{"type": "Feature", "properties": {k: b[k] for k in ("name", "state", "highway")},
                       "geometry": {"type": "Point", "coordinates": [b["lon"], b["lat"]]}} for b in R.bridges]), f)
    with open(os.path.join(o, "blocked_roads.geojson"), "w") as f:
        json.dump(gj([{"type": "Feature", "properties": {"state": e["state"]},
                       "geometry": {"type": "LineString", "coordinates": e["lonlat"]}}
                      for e in R.edges if e["state"] in ("hit", "unknown")]), f)
    with open(os.path.join(o, "buildings_hit.geojson"), "w") as f:
        json.dump(gj([{"type": "Feature", "properties": {"kind": b["kind"]},
                       "geometry": {"type": "Point", "coordinates": [b["lon"], b["lat"]]}}
                      for b in R.buildings if b["state"] == "hit"]), f)
    from . import dashboard
    dashboard.folium_map(R).save(os.path.join(o, "map.html"))
    dashboard.report_html(R, os.path.join(o, "situation_report.html"))


def load_dir(out_dir):
    """Re-open a finished run from its output folder (used by the dashboard)."""
    import rasterio

    from .impact import Projector
    from .satellite import Grid

    with open(os.path.join(out_dir, "facts.json"), encoding="utf-8") as f:
        facts = json.load(f)
    with rasterio.open(os.path.join(out_dir, "flood_classes.tif")) as src:
        cls = src.read(1)
        grid = Grid(str(src.crs), src.transform, cls.shape)
    dem = None
    if os.path.exists(os.path.join(out_dir, "dem.tif")):
        with rasterio.open(os.path.join(out_dir, "dem.tif")) as src:
            dem = src.read(1)
    places = []
    if os.path.exists(os.path.join(out_dir, "places.json")):
        with open(os.path.join(out_dir, "places.json"), encoding="utf-8") as f:
            places = json.load(f)
    return Run(facts=facts, cls=cls, grid=grid, dem=dem, places=places, proj=Projector(grid), out_dir=out_dir)
