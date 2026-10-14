"""Case study check against Copernicus EMS reference maps (e.g. EMSR927).

The EMS maps are used ONLY here, after the run, to score the result. They are never
an input to the flood, damage or cut-off analysis (rule of the challenge).
"""
from __future__ import annotations

import glob
import os
import tempfile
import zipfile

import numpy as np

from .flood import WATER, DEBRIS, NODATA


def _collect(paths):
    files = []
    for p in paths:
        if p.lower().endswith(".zip"):
            d = tempfile.mkdtemp(prefix="ems_")
            with zipfile.ZipFile(p) as z:
                z.extractall(d)
            for inner in glob.glob(os.path.join(d, "**", "*.zip"), recursive=True):
                with zipfile.ZipFile(inner) as z:
                    z.extractall(os.path.dirname(inner))
            files += glob.glob(os.path.join(d, "**", "*.*"), recursive=True)
        elif os.path.isdir(p):
            files += glob.glob(os.path.join(p, "**", "*.*"), recursive=True)
        else:
            files.append(p)
    return [f for f in files if f.lower().endswith((".shp", ".geojson", ".json", ".gpkg", ".kml"))]


def _norm(x):
    return x.lower().replace("_", "").replace("-", "").replace(" ", "")


def _layers(files):
    """Yield (name, path, layer) for every vector layer, including layers inside GeoPackages."""
    for f in files:
        if f.lower().endswith(".gpkg"):
            try:
                import pyogrio
                for lyr in pyogrio.list_layers(f)[:, 0]:
                    yield str(lyr), f, str(lyr)
            except Exception:
                import fiona
                for lyr in fiona.listlayers(f):
                    yield lyr, f, lyr
        else:
            yield os.path.basename(f), f, None


def _read(files, key):
    import geopandas as gpd
    import pandas as pd

    parts = []
    for name, f, lyr in _layers(files):
        if _norm(key) in _norm(name):
            g = gpd.read_file(f, layer=lyr) if lyr else gpd.read_file(f)
            if len(g):
                parts.append(g)
    if not parts:
        return None
    crs = parts[0].crs or "EPSG:4326"
    return gpd.GeoDataFrame(pd.concat([p.to_crs(crs) if p.crs else p.set_crs(crs) for p in parts], ignore_index=True), crs=crs)


def layer_names(paths):
    return [n for n, _, _ in _layers(_collect(paths))]


def compare(cls, grid, ems_paths, blds=None, out_png=None):
    from rasterio.features import rasterize

    files = _collect(ems_paths)
    ev = _read(files, "observedEvent")
    if ev is None:
        raise ValueError("No 'observedEvent' layer found. Layers seen: " + ", ".join(layer_names(ems_paths))[:800])
    aoi = _read(files, "areaOfInterest")
    ev = ev.to_crs(grid.crs)
    ref = rasterize(((g, 1) for g in ev.geometry if g is not None and not g.is_empty),
                    out_shape=grid.shape, transform=grid.transform, fill=0, dtype="uint8").astype(bool)
    if aoi is not None:
        aoi = aoi.to_crs(grid.crs)
        inside = rasterize(((g, 1) for g in aoi.geometry), out_shape=grid.shape, transform=grid.transform,
                           fill=0, dtype="uint8").astype(bool)
    else:
        inside = np.ones(grid.shape, bool)
    valid = inside & (cls != NODATA)
    ours = (cls == WATER) | (cls == DEBRIS)
    tp = int((ours & ref & valid).sum())
    fp = int((ours & ~ref & valid).sum())
    fn = int((~ours & ref & valid).sum())
    px = abs(grid.transform.a * grid.transform.e) / 1e6
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    iou = tp / (tp + fp + fn) if tp + fp + fn else 0.0
    res = dict(reference_km2=round((ref & valid).sum() * px, 2), ours_km2=round((ours & valid).sum() * px, 2),
               overlap_km2=round(tp * px, 2), precision=round(prec, 3), recall=round(rec, 3),
               f1=round(f1, 3), iou=round(iou, 3), ems_layers=[n for n, _, _ in _layers(files)][:40])

    # Building-level check if the package contains damage-graded built-up points/areas.
    bu = _read(files, "builtUp")
    if bu is not None and blds is not None:
        col = next((c for c in bu.columns if "damage" in c.lower()), None)
        if col:
            dmg = bu[~bu[col].astype(str).str.lower().isin(["no visible damage", "not applicable", "not analysed", "none", "nan"])]
            res["ems_damaged_buildings"] = int(len(dmg))
            res["ems_damage_column"] = col
        if aoi is not None:
            from shapely.geometry import Point
            from shapely.ops import unary_union
            poly = unary_union(list(aoi.geometry))
            res["our_buildings_hit_in_ems_aoi"] = int(sum(1 for b in blds if b["state"] == "hit" and poly.contains(Point(b["x"], b["y"]))))

    if out_png:
        from PIL import Image
        img = np.full(grid.shape + (3,), 255, np.uint8)
        img[~inside] = (241, 245, 249)
        img[ours & ref & valid] = (22, 163, 74)     # agree
        img[ours & ~ref & valid] = (234, 88, 12)    # only Bato
        img[~ours & ref & valid] = (37, 99, 235)    # only EMS
        Image.fromarray(img).save(out_png)
        res["figure"] = os.path.basename(out_png)
    return res
