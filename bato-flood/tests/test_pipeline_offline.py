"""Full pipeline run with the network calls replaced by a synthetic scene."""
import json
import os

import numpy as np

from bato import pipeline, satellite, osm
from bato.config import Settings

BBOX = (85.00, 28.00, 85.08, 28.06)


def fake_world(monkeypatch):
    gb = satellite.make_geobox(BBOX, 20)
    H, W = gb.shape
    rows, cols = np.mgrid[0:H, 0:W]
    mid = H // 2
    dem = (1500 - cols * 2.0 + np.maximum(0, np.abs(rows - mid) - 15) * 8.0).astype("float32")
    rng = np.random.default_rng(1)
    pre = (0.08 * rng.uniform(0.8, 1.2, (H, W))).astype("float32")
    pre[mid - 3:mid + 4, :] = 0.005
    post = pre.copy()
    post[mid - 10:mid + 10, W // 2:] = 0.005
    T = gb.affine
    tr = __import__("pyproj").Transformer.from_crs(str(gb.crs), 4326, always_xy=True)

    def P(c, r):
        return list(tr.transform(T.c + (c + .5) * T.a, T.f + (r + .5) * T.e))

    c3 = int(W * 0.75)
    data = dict(
        rivers=[[P(0, mid), P(W - 1, mid)]],
        roads=[dict(id="way/1", coords=[P(2, mid - 25), P(c3, mid - 25), P(W - 3, mid - 25)], highway="primary", name="Highway", bridge=False),
               dict(id="way/2", coords=[P(c3, mid - 25), P(c3, mid - 12)], highway="tertiary", name="", bridge=False),
               dict(id="way/3", coords=[P(c3, mid - 12), P(c3, mid + 12)], highway="tertiary", name="Test bridge", bridge=True),
               dict(id="way/4", coords=[P(c3, mid + 12), P(c3, mid + 40)], highway="tertiary", name="", bridge=False)],
        buildings=[dict(lon=P(c3 + i % 4, mid + 40 + i // 4)[0], lat=P(c3 + i % 4, mid + 40 + i // 4)[1], kind="house", id=str(i)) for i in range(12)],
        places=[dict(lon=P(c3, mid + 40)[0], lat=P(c3, mid + 40)[1], name="Kalika", name_ne="कालिका", place="village", population=None, id="n1"),
                dict(lon=P(2, mid - 25)[0], lat=P(2, mid - 25)[1], name="Bazar", name_ne="बजार", place="town", population=3000, id="n2")],
        health=[], snapshot="2026-07-27", bbox_buffered=BBOX)

    monkeypatch.setattr(osm, "fetch", lambda *a, **k: data)
    monkeypatch.setattr(satellite, "load_dem", lambda bbox, g: dem)
    monkeypatch.setattr(satellite, "find_s1_pairs", lambda *a, **k: [dict(
        track=(85, "ascending"), pre_date="2026-08-21", post_date="2026-09-02", gap_days=12, post_lag_days=7,
        coverage=1.0, pre=["pre"], post=["post"])])
    monkeypatch.setattr(satellite, "load_s1", lambda items, g: {"vv": pre if items == ["pre"] else post})
    monkeypatch.setattr(satellite, "find_s2", lambda *a, **k: (None, None))


def test_full_run(monkeypatch, tmp_path):
    fake_world(monkeypatch)
    R = pipeline.run(Settings(bbox=BBOX, event_date="2026-08-26"), "Test", str(tmp_path))
    for f in ["map.html", "situation_report.html", "facts.json", "settlements.csv", "flood_classes.tif",
              "situation_report_en.md", "situation_report_ne.md", "bridges.geojson", "flood_overlay.png"]:
        assert os.path.getsize(tmp_path / f) > 100, f
    facts = json.load(open(tmp_path / "facts.json", encoding="utf-8"))
    by = {s["name"]: s for s in facts["settlements"]}
    assert by["Kalika"]["status"] == "CUT OFF"
    assert facts["impact_stats"]["bridges_hit"] == 1
    assert R.facts["flood_stats"]["new_water_km2"] > 0
