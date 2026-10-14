"""Question 1: where did the flood hit?

Before/after change detection on Sentinel-1 radar (sees through cloud), confirmed by
Sentinel-2 optical when the sky is clear, limited to terrain a flood can reach.

Output class codes (uint8):
    0   observed, no flood change
    1   permanent water (river before and after)
    2   NEW water (flooded)
    3   debris / scoured ground (strong surface change on the valley floor)
    254 valley floor, but not observable (radar shadow/layover and no clear optical)
    255 no satellite data
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

NONE, PERM, WATER, DEBRIS, UNOBS, NODATA = 0, 1, 2, 3, 254, 255
CLASS_NAMES = {NONE: "no change", PERM: "river (before and after)", WATER: "new flood water",
               DEBRIS: "debris / scoured ground", UNOBS: "not observable", NODATA: "no data"}
CLASS_COLORS = {PERM: (37, 99, 235, 170), WATER: (14, 165, 233, 220), DEBRIS: (180, 83, 9, 220),
                UNOBS: (148, 163, 184, 90)}


# ------------------------------------------------------------------ terrain
def terrain(dem: np.ndarray, res_m: float, valley_window_m: float, river=None, max_river_dist_m=4000):
    """Slope (degrees) and height above the nearest river (m), a light version of HAND.

    `river` is a boolean raster of pre-event rivers (OSM waterways and/or water seen
    before the event). Each pixel is compared with the elevation of its nearest river
    pixel. Without rivers we fall back to the lowest point in a moving window.
    """
    d = np.where(np.isfinite(dem), dem, np.nanmedian(dem))
    gy, gx = np.gradient(d, res_m)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    if river is not None and river.sum() > 20:
        # river line may sit a pixel off the DEM channel: use the local minimum around it
        bed = ndimage.minimum_filter(d, size=5)
        dist, (ri, ci) = ndimage.distance_transform_edt(~river, return_indices=True)
        rel = d - bed[ri, ci]
        rel[dist * res_m > max_river_dist_m] = np.inf
    else:
        win = max(3, int(valley_window_m / res_m) | 1)
        rel = d - ndimage.minimum_filter(d, size=win)
    rel = rel.astype("float32")
    slope[~np.isfinite(dem)] = np.nan
    rel[~np.isfinite(dem)] = np.nan
    return slope.astype("float32"), rel.astype("float32")


def _to_db(a):
    with np.errstate(divide="ignore", invalid="ignore"):
        return (10 * np.log10(a)).astype("float32")


def _despeckle(db, k):
    valid = np.isfinite(db)
    filled = np.where(valid, db, np.nanmedian(db) if valid.any() else 0)
    out = ndimage.median_filter(filled, size=k)
    out[~valid] = np.nan
    return out


def _clean(mask, min_px):
    lab, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = ndimage.sum(mask, lab, index=np.arange(1, n + 1))
    keep = np.zeros(n + 1, bool)
    keep[1:] = sizes >= min_px
    return keep[lab]


# ------------------------------------------------------------------ Sentinel-1
def s1_change(pre_vv, post_vv, valley, steep, s):
    """Change detection for ONE orbit track. Returns dict of boolean layers + stats."""
    pre = _despeckle(_to_db(pre_vv), s.speckle_px)
    post = _despeckle(_to_db(post_vv), s.speckle_px)
    valid = np.isfinite(pre) & np.isfinite(post) & ~steep
    diff = post - pre
    # Normalise: remove the scene-wide shift (e.g. monsoon soil moisture) on the valley floor.
    ref = valid & valley
    shift = float(np.nanmedian(diff[ref])) if ref.sum() > 100 else 0.0
    diff = diff - shift
    water_pre = pre < s.water_db
    water_post = post < s.water_db
    new_water = valid & valley & water_post & ~water_pre & (diff <= -s.drop_db)
    perm = valid & water_pre & water_post
    debris = valid & valley & ~water_post & (diff >= s.rise_db)
    # A river that MOVED (old channel now debris) or widened is also captured by the two rules.
    return dict(valid=valid, new_water=new_water, perm=perm, debris=debris,
                diff_db=diff, shift_db=shift)


# ------------------------------------------------------------------ Sentinel-2
def _nd(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return (a - b) / (a + b)


def s2_change(pre, post, valley, s):
    ok = {4, 5, 6, 7}  # vegetation, bare soil, water, unclassified (no cloud/shadow/snow)
    vpre = np.isin(pre["SCL"], list(ok)) & np.isfinite(pre["B03"])
    vpost = np.isin(post["SCL"], list(ok)) & np.isfinite(post["B03"])
    valid = vpre & vpost
    mndwi_pre, mndwi_post = _nd(pre["B03"], pre["B11"]), _nd(post["B03"], post["B11"])
    ndvi_pre, ndvi_post = _nd(pre["B08"], pre["B04"]), _nd(post["B08"], post["B04"])
    water_pre = mndwi_pre > s.mndwi_water
    water_post = mndwi_post > s.mndwi_water
    new_water = valid & valley & water_post & ~water_pre
    perm = valid & water_pre & water_post
    debris = valid & valley & ~water_post & (ndvi_pre > s.ndvi_loss_pre) & (ndvi_post < s.ndvi_loss_post)
    return dict(valid=valid, new_water=new_water, perm=perm, debris=debris)


# ------------------------------------------------------------------ combine
def combine(s1_list, s2, dem, slope, rel, s):
    shape = dem.shape
    valley = np.isfinite(rel) & (rel <= s.max_rel_elev_m)
    any_valid = np.zeros(shape, bool)
    votes_w = np.zeros(shape, np.uint8)
    votes_d = np.zeros(shape, np.uint8)
    perm = np.zeros(shape, bool)
    s1_valid = np.zeros(shape, bool)
    for r in s1_list:
        any_valid |= r["valid"]
        s1_valid |= r["valid"]
        votes_w += r["new_water"]
        votes_d += r["debris"]
        perm |= r["perm"]
    s1_w = votes_w > 0
    s1_d = votes_d > 0
    s2_w = s2_d = np.zeros(shape, bool)
    s2_valid = np.zeros(shape, bool)
    if s2 is not None:
        s2_valid = s2["valid"]
        any_valid |= s2_valid
        s2_w, s2_d = s2["new_water"], s2["debris"]
        perm |= s2["perm"]

    water = _clean(s1_w | s2_w, s.min_blob_px)
    debris = _clean((s1_d | s2_d) & ~water, s.min_blob_px)

    cls = np.full(shape, NODATA, np.uint8)
    observed_area = np.isfinite(dem)
    cls[observed_area] = NONE
    cls[observed_area & valley & ~any_valid] = UNOBS
    cls[perm & ~water] = PERM
    cls[debris] = DEBRIS
    cls[water] = WATER
    if not s1_list and s2 is None:
        cls[:] = NODATA

    # Confidence: 2 = both sensors saw it (or one saw it and the other could not look), 1 = one only
    conf = np.zeros(shape, np.uint8)
    hit = water | debris
    both = ((s1_w | s1_d) & (s2_w | s2_d))
    single_but_other_blind = ((s1_w | s1_d) & ~s2_valid) | ((s2_w | s2_d) & ~s1_valid)
    conf[hit] = 1
    conf[hit & (both)] = 2
    conf[hit & single_but_other_blind & ~both] = 1
    return cls, conf


def area_stats(cls, conf, res_m):
    px_km2 = (res_m * res_m) / 1e6
    def a(m):
        return round(float(m.sum() * px_km2), 2)
    return {
        "new_water_km2": a(cls == WATER),
        "debris_km2": a(cls == DEBRIS),
        "flood_total_km2": a((cls == WATER) | (cls == DEBRIS)),
        "high_confidence_km2": a(((cls == WATER) | (cls == DEBRIS)) & (conf == 2)),
        "river_km2": a(cls == PERM),
        "not_observable_km2": a(cls == UNOBS),
        "no_data_km2": a(cls == NODATA),
    }


def to_rgba(cls):
    rgba = np.zeros(cls.shape + (4,), np.uint8)
    for k, c in CLASS_COLORS.items():
        rgba[cls == k] = c
    return rgba
