"""Bonus: from any point upstream, trace the flood path down the valley on the DEM and
list the settlements along it (steepest descent, with pit-escaping for DEM noise)."""
from __future__ import annotations

import math

import numpy as np
from scipy.spatial import cKDTree

NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
FRONT_SPEED_MS = (3.0, 8.0)  # rough range for a glacial flood front; an assumption, labelled in the UI


def trace(dem, proj, lon, lat, res_m, max_km=80.0, max_escape_px=40):
    h, w = dem.shape
    x, y = proj.xy(lon, lat)
    r, c = (int(v) for v in proj.rowcol(x, y))
    if not (0 <= r < h and 0 <= c < w) or not np.isfinite(dem[r, c]):
        raise ValueError("Start point is outside the analysed area")
    d = np.where(np.isfinite(dem), dem, np.inf)
    visited = {(r, c)}
    path = [(r, c)]
    length = 0.0
    while length < max_km * 1000:
        z = d[r, c]
        best, best_slope = None, 0.0
        for dr, dc in NB:  # steepest downhill neighbour (D8)
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and (rr, cc) not in visited:
                sl = (z - d[rr, cc]) / math.hypot(dr, dc)
                if sl > best_slope:
                    best, best_slope = (rr, cc), sl
        if best is None:  # pit or flat: jump to the lowest unvisited cell nearby that is not higher
            found = None
            for rad in range(2, max_escape_px + 1):
                r0, r1, c0, c1 = max(0, r - rad), min(h, r + rad + 1), max(0, c - rad), min(w, c + rad + 1)
                win = d[r0:r1, c0:c1].copy()
                for (vr, vc) in path[-200:]:
                    if r0 <= vr < r1 and c0 <= vc < c1:
                        win[vr - r0, vc - c0] = np.inf
                k = np.unravel_index(np.argmin(win), win.shape)
                if win[k] <= z + 2.0:  # allow 2 m of DEM noise
                    found = (r0 + k[0], c0 + k[1])
                    break
            if found is None:
                break
            best = found
        if r in (0, h - 1) or c in (0, w - 1):
            break
        length += math.hypot(best[0] - r, best[1] - c) * res_m
        r, c = best
        visited.add(best)
        path.append(best)
    rows = np.array([p[0] for p in path])
    cols = np.array([p[1] for p in path])
    T = proj.grid.transform
    xs = T.c + (cols + 0.5) * T.a
    ys = T.f + (rows + 0.5) * T.e
    lons, lats = proj.lonlat(xs, ys)
    elev = dem[rows, cols]
    seg = np.hypot(np.diff(xs), np.diff(ys))
    along = np.concatenate([[0], np.cumsum(seg)])
    return dict(lon=np.asarray(lons).tolist(), lat=np.asarray(lats).tolist(), x=xs, y=ys,
                elev=[float(v) for v in elev], along_m=along, length_km=round(float(along[-1]) / 1000, 1),
                drop_m=round(float(elev[0] - elev[-1]), 0))


def settlements_along(fp, places, proj, max_dist_m=600):
    tree = cKDTree(np.c_[fp["x"], fp["y"]])
    out = []
    for p in places:
        if p["place"] not in ("village", "hamlet", "town", "city"):
            continue
        x, y = proj.xy(p["lon"], p["lat"])
        d, i = tree.query([x, y])
        if d <= max_dist_m:
            km = float(fp["along_m"][i]) / 1000
            t_fast = km * 1000 / FRONT_SPEED_MS[1] / 60
            t_slow = km * 1000 / FRONT_SPEED_MS[0] / 60
            out.append(dict(name=p["name"], place=p["place"], km_downstream=round(km, 1),
                            distance_to_path_m=round(float(d)), minutes_rough=(round(t_fast), round(t_slow)),
                            lon=p["lon"], lat=p["lat"]))
    out.sort(key=lambda r: r["km_downstream"])
    return out
