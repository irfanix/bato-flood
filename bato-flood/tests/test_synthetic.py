"""Offline tests on a synthetic valley: no network needed.

Scene: a valley running west-east, river in the middle, a glacial flood widens the
river and dumps debris in the east. A main road follows the valley; village "Upper"
is reached only through a bridge that the flood hits; village "Lower" is downstream
of the hospital and stays reachable.
"""
import numpy as np
from affine import Affine

from bato import flood, impact, cutoff, report, flowpath
from bato.config import Settings
from bato.satellite import Grid

H, W, RES = 300, 400, 20.0
X0, Y0 = 300000.0, 3110000.0


def scene():
    s = Settings(bbox=(0, 0, 0, 0), event_date="2026-08-26", speckle_px=3, min_blob_px=4)
    grid = Grid("EPSG:32645", Affine(RES, 0, X0, 0, -RES, Y0), (H, W))
    rows, cols = np.mgrid[0:H, 0:W]
    # valley floor rows 135-165, river at row 150, dropping 100 m/km eastwards (steep Himalayan river)
    dem = (1500 - cols * 2.0 + np.maximum(0, np.abs(rows - 150) - 15) * 8.0).astype("float32")
    river = np.zeros((H, W), bool)
    river[150, :] = True
    slope, rel = flood.terrain(dem, RES, 1500, river=river)
    valley = rel <= s.max_rel_elev_m
    steep = slope > s.max_slope_deg
    rng = np.random.default_rng(0)
    pre = np.full((H, W), 0.08, "float32") * rng.uniform(0.8, 1.2, (H, W))
    pre[146:154, :] = 0.005  # river
    post = pre.copy()
    post[140:160, 200:400] = 0.005          # river widened in the east (new water)
    post[160:166, 250:330] = 0.4            # debris: strong backscatter rise
    s1 = flood.s1_change(pre, post, valley, steep, s)
    cls, conf = flood.combine([s1], None, dem, slope, rel, s)
    return s, grid, dem, cls, conf


def xy_to_lonlat(proj, col, row):
    x = X0 + (col + 0.5) * RES
    y = Y0 - (row + 0.5) * RES
    return [float(v) for v in proj.lonlat(x, y)]


def test_flood_classes():
    s, grid, dem, cls, conf = scene()
    assert (cls[150, 100] == flood.PERM)
    assert (cls[142, 300] == flood.WATER)
    assert (cls[163, 290] == flood.DEBRIS)
    assert (cls[60, 300] == flood.NONE)        # hillside, unchanged
    st = flood.area_stats(cls, conf, RES)
    assert 0.5 < st["new_water_km2"] < 1.2
    assert st["debris_km2"] > 0.1


def build_world():
    s, grid, dem, cls, conf = scene()
    proj = impact.Projector(grid)
    P = lambda c, r: xy_to_lonlat(proj, c, r)
    roads = [
        dict(id="way/1", coords=[P(10, 120), P(100, 120), P(150, 120), P(300, 120), P(390, 120)], highway="primary", name="Valley road", bridge=False),
        dict(id="way/2", coords=[P(300, 120), P(300, 139)], highway="tertiary", name="", bridge=False),
        dict(id="way/3", coords=[P(300, 139), P(300, 168)], highway="tertiary", name="Upper bridge", bridge=True),
        dict(id="way/4", coords=[P(300, 168), P(300, 200)], highway="tertiary", name="", bridge=False),
        dict(id="way/5", coords=[P(100, 120), P(100, 90)], highway="track", name="", bridge=False),
    ]
    places = [
        dict(lon=P(300, 200)[0], lat=P(300, 200)[1], name="Upper", name_ne="माथिल्लो", place="village", population=None, id="node/1"),
        dict(lon=P(100, 90)[0], lat=P(100, 90)[1], name="Lower", name_ne="तल्लो", place="village", population=250, id="node/2"),
        dict(lon=P(380, 280)[0], lat=P(380, 280)[1], name="Faraway", name_ne=None, place="hamlet", population=None, id="node/3"),
    ]
    health = [dict(lon=P(10, 120)[0], lat=P(10, 120)[1], name="District Hospital", kind="hospital", id="node/9")]
    blds = [dict(lon=P(300 + i % 5, 200 + i // 5)[0], lat=P(300 + i % 5, 200 + i // 5)[1], kind="house", id=str(i)) for i in range(20)]
    blds += [dict(lon=P(290, 142)[0], lat=P(290, 142)[1], kind="house", id="flooded")]
    bbox = (min(p["lon"] for p in places) - 0.05, min(p["lat"] for p in places) - 0.05,
            max(p["lon"] for p in places) + 0.05, max(p["lat"] for p in places) + 0.05)
    blds = impact.buildings(cls, proj, blds, 1)
    edges = impact.roads(cls, proj, roads, 1, 10)
    istats, bridges = impact.summarise(blds, edges, roads)
    sett, cstats, dests = cutoff.analyse(edges, places, health, proj, bbox, s, buildings=blds)
    return locals()


def test_damage_and_cutoff():
    w = build_world()
    istats, sett, bridges = w["istats"], w["sett"], w["bridges"]
    assert istats["buildings_hit"] == 1
    assert istats["bridges_hit"] == 1 and bridges[0]["name"] == "Upper bridge"
    by = {s["name"]: s for s in sett}
    assert by["Upper"]["status"] == cutoff.CUT
    assert by["Upper"]["people"] == round(20 * 4.37)
    assert by["Upper"]["blocked_at"]["km_from_village"] < 1.0
    assert by["Lower"]["status"] == cutoff.REACH and by["Lower"]["people"] == 250
    assert by["Faraway"]["status"] == cutoff.NOROAD
    assert w["cstats"]["cut_off"] == 1


def test_report_and_guard():
    w = build_world()
    meta = dict(area_name="Test valley", event_date="2026-08-26", osm_snapshot="2026-07-27",
                s1_pairs=[dict(pre_date="2026-08-20", post_date="2026-09-01", direction="orbit 85 ascending")],
                s2_pre=None, s2_post=None)
    fstats = flood.area_stats(w["cls"], w["conf"], RES)
    facts = report.build_facts(meta, fstats, w["istats"], w["bridges"], w["sett"], w["cstats"])
    en = report.report_markdown(facts, "en")
    ne = report.report_markdown(facts, "ne")
    assert "Upper" in en and "माथिल्लो" in ne
    # Every number in the deterministic answers passes the guard
    for q in ["Who is cut off?", "Which bridges are down?", "Tell me about Upper", "status of Lower", "how many buildings?"]:
        for lang in ("en", "ne"):
            ans = report.rule_answer(q, facts, lang)
            ok, bad = report.guard(ans, facts, q)
            assert ok, (q, lang, ans, bad)
    # An invented number is caught, also in Devanagari digits
    assert not report.guard("About 5000 people are stranded [F10].", facts)[0]
    assert not report.guard("करिब ५००० जना [F10]", facts)[0]
    assert report.guard(f"{facts['facts'][8]['value']} settlements are cut off [F9].", facts)[0]


def test_flowpath():
    s, grid, dem, cls, conf = scene()
    proj = impact.Projector(grid)
    lon, lat = xy_to_lonlat(proj, 20, 100)  # on the hillside, west
    fp = flowpath.trace(dem, proj, lon, lat, RES, max_km=50)
    assert fp["drop_m"] > 300
    rows = [int(proj.rowcol(x, y)[0]) for x, y in zip(fp["x"][-20:], fp["y"][-20:])]
    assert all(135 <= r <= 165 for r in rows)  # ends up following the valley floor
    places = [dict(lon=xy_to_lonlat(proj, 250, 152)[0], lat=xy_to_lonlat(proj, 250, 152)[1], name="Riverside", place="village")]
    along = flowpath.settlements_along(fp, places, proj)
    assert along and along[0]["name"] == "Riverside"
