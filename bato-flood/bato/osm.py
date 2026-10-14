"""Pre-event OpenStreetMap via the ohsome API (history snapshot, never post-event edits)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time

import requests

log = logging.getLogger("bato")
OHSOME = "https://api.ohsome.org/v1"
CACHE = os.environ.get("BATO_CACHE", os.path.join(os.path.dirname(__file__), "..", "data", "cache"))

VEHICLE = ("motorway, trunk, primary, secondary, tertiary, unclassified, residential, living_street, "
           "service, road, motorway_link, trunk_link, primary_link, secondary_link, tertiary_link")


def _post(endpoint, bbox, snapshot, flt, props="tags"):
    os.makedirs(CACHE, exist_ok=True)
    key = hashlib.md5(json.dumps([endpoint, bbox, snapshot, flt]).encode()).hexdigest()
    path = os.path.join(CACHE, f"ohsome_{key}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    data = {"bboxes": ",".join(f"{v:.5f}" for v in bbox), "time": snapshot,
            "filter": flt, "properties": props}
    for attempt in range(4):
        try:
            r = requests.post(f"{OHSOME}/{endpoint}", data=data, timeout=300)
            r.raise_for_status()
            js = r.json()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(js, f)
            return js
        except Exception as e:  # network hiccup: retry with back-off
            log.warning("ohsome %s failed (%s), retry %d", endpoint, e, attempt + 1)
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("ohsome API not reachable")


def latest_snapshot() -> str | None:
    try:
        r = requests.get(f"{OHSOME}/metadata", timeout=30).json()
        return r["extractRegion"]["temporalExtent"]["toTimestamp"][:10]
    except Exception:
        return None


def expand(bbox, km):
    d = km / 111.0
    return (bbox[0] - d, bbox[1] - d, bbox[2] + d, bbox[3] + d)


def fetch(bbox, snapshot: str, buffer_km: float, include_tracks=True, include_paths=False) -> dict:
    """Return dict of plain-python features: roads, buildings, places, health, plus metadata."""
    big = expand(bbox, buffer_km)
    kinds = VEHICLE
    if include_tracks:
        kinds += ", track"
    if include_paths:
        kinds += ", path, footway, bridleway, steps"
    roads = _post("elements/geometry", big, snapshot, f"type:way and highway in ({kinds})")
    buildings = _post("elements/centroid", bbox, snapshot, "building=* and geometry:polygon")
    places = _post("elements/centroid", big, snapshot,
                   "place in (city, town, village, hamlet) and name=*")
    rivers = _post("elements/geometry", big, snapshot, "type:way and waterway in (river, canal)")
    health = _post("elements/centroid", big, snapshot,
                   "amenity in (hospital, clinic, doctors) or healthcare in (hospital, clinic, centre)")

    def tags(f):
        return f.get("properties", {}) or {}

    out_roads = []
    for f in roads.get("features", []):
        g = f["geometry"]
        lines = [g["coordinates"]] if g["type"] == "LineString" else (
            g["coordinates"] if g["type"] == "MultiLineString" else [])
        t = tags(f)
        for coords in lines:
            if len(coords) >= 2:
                out_roads.append(dict(id=t.get("@osmId", ""), coords=coords,
                                      highway=t.get("highway", ""), name=t.get("name", ""),
                                      bridge=t.get("bridge", "no") not in ("no", ""),
                                      ))
    out_bld = [dict(lon=f["geometry"]["coordinates"][0], lat=f["geometry"]["coordinates"][1],
                    kind=tags(f).get("building", "yes"), id=tags(f).get("@osmId", ""))
               for f in buildings.get("features", [])]
    out_places = []
    for f in places.get("features", []):
        t = tags(f)
        pop = t.get("population")
        try:
            pop = int(str(pop).replace(",", "")) if pop else None
        except ValueError:
            pop = None
        out_places.append(dict(lon=f["geometry"]["coordinates"][0], lat=f["geometry"]["coordinates"][1],
                               name=t.get("name:en") or t.get("name"), name_ne=t.get("name:ne") or t.get("name"),
                               place=t.get("place"), population=pop, id=t.get("@osmId", "")))
    out_health = []
    for f in health.get("features", []):
        t = tags(f)
        kind = t.get("amenity") or t.get("healthcare")
        out_health.append(dict(lon=f["geometry"]["coordinates"][0], lat=f["geometry"]["coordinates"][1],
                               name=t.get("name:en") or t.get("name") or kind, kind=kind,
                               id=t.get("@osmId", "")))
    out_rivers = []
    for f in rivers.get("features", []):
        g = f["geometry"]
        if g["type"] == "LineString":
            out_rivers.append(g["coordinates"])
        elif g["type"] == "MultiLineString":
            out_rivers += g["coordinates"]
    log.info("OSM %s: %d road pieces, %d buildings, %d places, %d health sites", snapshot,
             len(out_roads), len(out_bld), len(out_places), len(out_health))
    return dict(rivers=out_rivers, roads=out_roads, buildings=out_bld, places=out_places, health=out_health,
                snapshot=snapshot, bbox_buffered=big)
