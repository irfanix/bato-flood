"""Question 2: what was damaged? Overlay pre-event OSM buildings, roads and bridges on
the flood map and decide, for each object, hit / not hit / cannot tell."""
from __future__ import annotations

import math

import numpy as np
from pyproj import Transformer

from .flood import WATER, DEBRIS, UNOBS, NODATA

HIT = "hit"
OK = "open"
UNKNOWN = "unknown"
OUTSIDE = "outside"   # outside the satellite analysis area (assumed open, flagged)


class Projector:
    def __init__(self, grid):
        self.grid = grid
        self.fwd = Transformer.from_crs(4326, grid.crs, always_xy=True)
        self.inv = Transformer.from_crs(grid.crs, 4326, always_xy=True)
        self.inv_aff = ~grid.transform

    def xy(self, lon, lat):
        return self.fwd.transform(lon, lat)

    def lonlat(self, x, y):
        return self.inv.transform(x, y)

    def rowcol(self, x, y):
        c, r = self.inv_aff * (np.asarray(x), np.asarray(y))
        return np.floor(r).astype(int), np.floor(c).astype(int)


def classify_points(cls, rows, cols, buf):
    """Vectorised: state for each pixel position using a (2*buf+1)^2 window."""
    h, w = cls.shape
    rows = np.asarray(rows)
    cols = np.asarray(cols)
    inside = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w)
    hit = np.zeros(rows.shape, bool)
    for dr in range(-buf, buf + 1):
        for dc in range(-buf, buf + 1):
            rr = np.clip(rows + dr, 0, h - 1)
            cc = np.clip(cols + dc, 0, w - 1)
            v = cls[rr, cc]
            hit |= inside & ((v == WATER) | (v == DEBRIS))
    rc = np.clip(rows, 0, h - 1)
    ccc = np.clip(cols, 0, w - 1)
    centre = cls[rc, ccc]
    unknown = inside & ~hit & ((centre == UNOBS) | (centre == NODATA))
    state = np.full(rows.shape, OK, dtype=object)
    state[unknown] = UNKNOWN
    state[hit] = HIT
    state[~inside] = OUTSIDE
    return state


def buildings(cls, proj: Projector, blds, buf):
    if not blds:
        return []
    lon = np.array([b["lon"] for b in blds])
    lat = np.array([b["lat"] for b in blds])
    x, y = proj.xy(lon, lat)
    r, c = proj.rowcol(x, y)
    st = classify_points(cls, r, c, buf)
    for b, s, xx, yy in zip(blds, st, x, y):
        b["state"] = s
        b["x"], b["y"] = float(xx), float(yy)
    return blds


def roads(cls, proj: Projector, road_list, buf, step_m):
    """Split every road into vertex-to-vertex edges and give each edge a state."""
    edges = []
    for ri, rd in enumerate(road_list):
        coords = np.asarray(rd["coords"], dtype=float)
        x, y = proj.xy(coords[:, 0], coords[:, 1])
        for i in range(len(x) - 1):
            x0, y0, x1, y1 = x[i], y[i], x[i + 1], y[i + 1]
            L = math.hypot(x1 - x0, y1 - y0)
            n = max(2, int(math.ceil(L / step_m)) + 1)
            t = np.linspace(0, 1, n)
            px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            r, c = proj.rowcol(px, py)
            st = classify_points(cls, r, c, buf)
            if (st == HIT).any():
                s = HIT
            elif (st == UNKNOWN).any():
                s = UNKNOWN
            elif (st == OUTSIDE).all():
                s = OUTSIDE
            else:
                s = OK
            hit_len = L * float((st == HIT).mean())
            edges.append(dict(road=ri, a=(round(x0, 1), round(y0, 1)), b=(round(x1, 1), round(y1, 1)),
                              length=L, state=s, hit_len=hit_len, bridge=rd["bridge"],
                              lonlat=[(float(coords[i, 0]), float(coords[i, 1])),
                                      (float(coords[i + 1, 0]), float(coords[i + 1, 1]))]))
    return edges


def summarise(blds, edges, road_list):
    from collections import Counter

    bc = Counter(b["state"] for b in blds)
    hit_km = sum(e["hit_len"] for e in edges) / 1000
    blocked_km = sum(e["length"] for e in edges if e["state"] == HIT) / 1000
    total_km = sum(e["length"] for e in edges if e["state"] != OUTSIDE) / 1000
    # Bridges: one OSM bridge way can have several edges; hit if any edge hit.
    bridge_state = {}
    for e in edges:
        if not e["bridge"]:
            continue
        cur = bridge_state.get(e["road"], OK)
        order = {OK: 0, OUTSIDE: 0, UNKNOWN: 1, HIT: 2}
        if order[e["state"]] > order[cur]:
            cur = e["state"]
        bridge_state[e["road"]] = cur
    bridges = []
    for ri, s in bridge_state.items():
        rd = road_list[ri]
        mid = rd["coords"][len(rd["coords"]) // 2]
        bridges.append(dict(name=rd["name"] or f"unnamed {rd['highway']} bridge", state=s,
                            lon=mid[0], lat=mid[1], highway=rd["highway"], osm_id=rd["id"]))
    return dict(
        buildings_total=len(blds), buildings_hit=bc.get(HIT, 0),
        buildings_unknown=bc.get(UNKNOWN, 0),
        road_km_analysed=round(total_km, 1), road_km_flooded=round(hit_km, 1),
        road_km_blocked=round(blocked_km, 1),
        bridges_total=sum(1 for b in bridges if b["state"] != OUTSIDE),
        bridges_hit=sum(1 for b in bridges if b["state"] == HIT),
        bridges_unknown=sum(1 for b in bridges if b["state"] == UNKNOWN),
    ), bridges
