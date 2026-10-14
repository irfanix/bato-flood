"""Bonus: from any point upstream, trace the flood path down the valley on the DEM and
list the settlements along it (steepest descent, with pit-escaping for DEM noise)."""
from __future__ import annotations

import math

import numpy as np
from scipy.spatial import cKDTree

NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
FRONT_SPEED_MS = (3.0, 8.0)  # rough range for a glacial flood front; an assumption, labelled in the UI


def trace(dem, proj, lon, lat, res_m, max_km=80.0, max_cells=3_000_000):
    """Path water takes from (lon, lat): always spill to the lowest cell next to the area
    already reached (priority flood). Depressions in the DEM fill up and spill over, so the
    path cannot get stuck or loop. Stops at the edge of the analysed area."""
    import heapq

    h, w = dem.shape
    x, y = proj.xy(lon, lat)
    r, c = (int(v) for v in proj.rowcol(x, y))
    if not (0 <= r < h and 0 <= c < w) or not np.isfinite(dem[r, c]):
        raise ValueError("Start point is outside the analysed area")
    d = np.where(np.isfinite(dem), dem, np.inf)
    parent = {}
    done = np.zeros((h, w), bool)
    heap = [(float(d[r, c]), 0, r, c)]
    parent[(r, c)] = None
    end = (r, c)
    n = 0
    while heap and n < max_cells:
        z, _, rr, cc = heapq.heappop(heap)
        if done[rr, cc]:
            continue
        done[rr, cc] = True
        n += 1
        end = (rr, cc)
        if rr in (0, h - 1) or cc in (0, w - 1):
            break  # left the analysed area
        for dr, dc in NB:
            r2, c2 = rr + dr, cc + dc
            if 0 <= r2 < h and 0 <= c2 < w and not done[r2, c2] and np.isfinite(d[r2, c2]):
                if (r2, c2) not in parent:
                    parent[(r2, c2)] = (rr, cc)
                heapq.heappush(heap, (float(d[r2, c2]), n, r2, c2))  # always grow into the lowest cell next to the wet area
    path = []
    node = end
    while node is not None:
        path.append(node)
        node = parent[node]
    path.reverse()
    # cap length
    out, length = [path[0]], 0.0
    for p0, p1 in zip(path, path[1:]):
        length += math.hypot(p1[0] - p0[0], p1[1] - p0[1]) * res_m
        out.append(p1)
        if length > max_km * 1000:
            break
    path = out
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
