"""Find and load Sentinel-1, Sentinel-2 and Copernicus DEM data for any area and date.

Data access: Microsoft Planetary Computer STAC (free, no account; items are signed
anonymously). The same Copernicus Sentinel data as dataspace.copernicus.eu.
Alternatively, the user can pass GeoTIFFs downloaded from the Copernicus Data Space
Browser with --s1-pre / --s1-post (see README).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
import logging

import numpy as np

log = logging.getLogger("bato")

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"


# ---------------------------------------------------------------- grid helpers
def utm_epsg(bbox) -> int:
    lon = (bbox[0] + bbox[2]) / 2
    lat = (bbox[1] + bbox[3]) / 2
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def make_geobox(bbox, resolution_m: int):
    from odc.geo.geobox import GeoBox
    from odc.geo.geom import BoundingBox
    from pyproj import Transformer

    epsg = utm_epsg(bbox)
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    xs, ys = [], []
    for lon in np.linspace(bbox[0], bbox[2], 5):
        for lat in np.linspace(bbox[1], bbox[3], 5):
            x, y = tr.transform(lon, lat)
            xs.append(x)
            ys.append(y)
    bb = BoundingBox(min(xs), min(ys), max(xs), max(ys), crs=f"EPSG:{epsg}")
    return GeoBox.from_bbox(bb, resolution=resolution_m, tight=True)


@dataclass
class Grid:
    crs: str
    transform: object      # affine.Affine
    shape: tuple[int, int]

    @staticmethod
    def from_geobox(gb) -> "Grid":
        return Grid(str(gb.crs), gb.affine, (int(gb.shape[0]), int(gb.shape[1])))


# ---------------------------------------------------------------- STAC search
def _client():
    import planetary_computer
    import pystac_client

    return pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)


def _bbox_poly(bbox):
    from shapely.geometry import box

    return box(*bbox)


def _coverage(items, bbox) -> float:
    from shapely.geometry import shape
    from shapely.ops import unary_union

    aoi = _bbox_poly(bbox)
    u = unary_union([shape(i.geometry) for i in items])
    return float(u.intersection(aoi).area / aoi.area)


def find_s1_pairs(bbox, event_date: str, days_before=36, days_after=24, max_tracks=2):
    """Return up to `max_tracks` (pre_items, post_items, info) on the SAME orbit track.

    Change detection only works pixel by pixel when both images come from the same
    relative orbit and pass direction (same viewing geometry, same time of day).
    """
    ev = date.fromisoformat(event_date)
    t0 = (ev - timedelta(days=days_before)).isoformat()
    t1 = (ev + timedelta(days=days_after)).isoformat()
    items = list(_client().search(collections=["sentinel-1-rtc"], bbox=bbox,
                                  datetime=f"{t0}/{t1}").items())
    log.info("Sentinel-1 RTC scenes found: %d", len(items))
    by_track = defaultdict(lambda: defaultdict(list))
    for it in items:
        p = it.properties
        key = (p.get("sat:relative_orbit"), p.get("sat:orbit_state"))
        by_track[key][it.datetime.date()].append(it)

    cands = []
    for key, by_day in by_track.items():
        pre_days = sorted([d for d in by_day if d < ev], reverse=True)
        post_days = sorted([d for d in by_day if d > ev])
        if not pre_days or not post_days:
            continue
        pre_d, post_d = pre_days[0], post_days[0]
        pre, post = by_day[pre_d], by_day[post_d]
        cov = min(_coverage(pre, bbox), _coverage(post, bbox))
        if cov < 0.2:
            continue
        cands.append(dict(track=key, pre_date=pre_d.isoformat(), post_date=post_d.isoformat(),
                          gap_days=(post_d - pre_d).days, post_lag_days=(post_d - ev).days,
                          coverage=round(cov, 3), pre=pre, post=post))
    # Prefer good coverage (in 25% steps), then the earliest image after the event.
    cands.sort(key=lambda c: (-int(c["coverage"] * 4), c["post_lag_days"], c["gap_days"]))
    chosen = cands[:max_tracks]
    for c in chosen:
        log.info("S1 track %s %s: pre %s, post %s (coverage %.0f%%)", c["track"][0], c["track"][1],
                 c["pre_date"], c["post_date"], 100 * c["coverage"])
    return chosen


def find_s2(bbox, event_date: str, days_before=60, days_after=30, max_cloud=60.0):
    """Least-cloudy Sentinel-2 L2A day before and after the event (or None)."""
    ev = date.fromisoformat(event_date)

    def best(t0, t1, prefer_close_to):
        items = list(_client().search(collections=["sentinel-2-l2a"], bbox=bbox,
                                      datetime=f"{t0}/{t1}",
                                      query={"eo:cloud_cover": {"lt": max_cloud}}).items())
        days = defaultdict(list)
        for it in items:
            days[it.datetime.date()].append(it)
        scored = []
        for d, its in days.items():
            cov = _coverage(its, bbox)
            if cov < 0.5:
                continue
            cloud = float(np.mean([i.properties.get("eo:cloud_cover", 100) for i in its]))
            scored.append((cloud + abs((d - prefer_close_to).days) * 0.5, d, its, cloud, cov))
        if not scored:
            return None
        scored.sort(key=lambda s: s[0])
        _, d, its, cloud, cov = scored[0]
        return dict(date=d.isoformat(), items=its, cloud=round(cloud, 1), coverage=round(cov, 3))

    pre = best((ev - timedelta(days=days_before)).isoformat(), (ev - timedelta(days=1)).isoformat(), ev)
    post = best((ev + timedelta(days=1)).isoformat(), (ev + timedelta(days=days_after)).isoformat(), ev)
    return pre, post


# ---------------------------------------------------------------- loading
def _load(items, bands, geobox, resampling):
    import odc.stac

    ds = odc.stac.load(items, bands=bands, geobox=geobox, groupby="solar_day",
                       resampling=resampling, chunks={})
    ds = ds.compute()
    out = {}
    for b in bands:
        arr = ds[b].values
        arr = arr[0] if arr.ndim == 3 else arr
        out[b] = arr
    return out


def load_s1(items, geobox) -> dict:
    """Return linear gamma0 VV (and VH if present) as float32 with NaN for no data."""
    pols = set()
    for it in items:
        pols.update(k.lower() for k in it.assets.keys())
    bands = [b for b in ("vv", "vh") if b in pols]
    raw = _load(items, bands, geobox, {"*": "bilinear"})
    out = {}
    for b, a in raw.items():
        a = a.astype("float32")
        a[(a <= 0) | ~np.isfinite(a) | (a < -1000)] = np.nan
        out[b] = a
    return out


def load_s2(items, geobox) -> dict:
    """Return surface reflectance for green, red, NIR, SWIR1 and the scene class layer."""
    bands = ["B03", "B04", "B08", "B11", "SCL"]
    raw = _load(items, bands, geobox, {"*": "bilinear", "SCL": "nearest"})
    baseline = str(items[0].properties.get("s2:processing_baseline", "05.00"))
    offset = 1000.0 if baseline >= "04.00" else 0.0  # BOA_ADD_OFFSET since Jan 2022
    out = {}
    for b in ("B03", "B04", "B08", "B11"):
        a = raw[b].astype("float32")
        a[a == 0] = np.nan
        out[b] = (a - offset) / 10000.0
    out["SCL"] = raw["SCL"].astype("uint8")
    return out


def load_dem(bbox, geobox) -> np.ndarray:
    items = list(_client().search(collections=["cop-dem-glo-30"], bbox=bbox).items())
    if not items:
        raise RuntimeError("No Copernicus DEM tiles found for this area")
    dem = _load(items, ["data"], geobox, {"*": "bilinear"})["data"].astype("float32")
    dem[dem < -500] = np.nan
    return dem


def load_local_geotiff(path: str, geobox) -> np.ndarray:
    """Reproject a user-supplied GeoTIFF (e.g. from the Copernicus Browser) onto the grid."""
    import rasterio
    from rasterio.warp import reproject, Resampling

    dst = np.full(geobox.shape, np.nan, dtype="float32")
    with rasterio.open(path) as src:
        reproject(source=rasterio.band(src, 1), destination=dst, dst_transform=geobox.affine,
                  dst_crs=str(geobox.crs), resampling=Resampling.bilinear, dst_nodata=np.nan)
    dst[~np.isfinite(dst)] = np.nan
    # Copernicus Browser exports may be in dB (mostly negative values) or linear (0..1).
    if np.nanmedian(dst) < 0:
        dst[dst < -60] = np.nan
        return (10 ** (dst / 10.0)).astype("float32")
    dst[dst <= 0] = np.nan
    return dst
