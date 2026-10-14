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


UA = {"User-Agent": "bato-flood/0.1 (hackathon prototype; github.com/irfanix/bato-flood)"}
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
SOURCES_USED = set()


def _cache(key_obj):
    os.makedirs(CACHE, exist_ok=True)
    key = hashlib.md5(json.dumps(key_obj).encode()).hexdigest()
    return os.path.join(CACHE, f"osm_{key}.json")


def _ohsome(endpoint, bbox, snapshot, flt, props="tags"):
    data = {"bboxes": ",".join(f"{v:.5f}" for v in bbox), "time": snapshot,
            "filter": flt, "properties": props}
    last = None
    for attempt in range(2):
        try:
            r = requests.post(f"{OHSOME}/{endpoint}", data=data, headers=UA, timeout=300)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            log.warning("ohsome %s failed (%s), retry %d", endpoint, e, attempt + 1)
            time.sleep(2)
    raise RuntimeError(f"ohsome failed: {last}")


def _overpass(stmt, bbox, snapshot, mode):
    """Same pre-event snapshot through Overpass 'attic' data ([date:...]): OSM as it was then."""
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]
    bb = f"{s:.5f},{w:.5f},{n:.5f},{e:.5f}"
    q = (f'[out:json][timeout:600][maxsize:1073741824][date:"{snapshot}T00:00:00Z"];'
         f'({stmt.replace("BBOX", bb)});out tags {mode};')
    last = None
    for url in OVERPASS:
        for attempt in range(2):
            try:
                r = requests.post(url, data={"data": q}, headers=UA, timeout=900)
                if r.status_code in (429, 504):
                    raise RuntimeError(f"busy ({r.status_code})")
                r.raise_for_status()
                js = r.json()
                if js.get("remark") and "error" in js["remark"].lower():
                    raise RuntimeError(js["remark"][:200])
                return _to_geojson(js, mode), url
            except Exception as ex:
                last = ex
                log.warning("Overpass %s failed (%s)", url.split("/")[2], ex)
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Overpass failed: {last}")


def _to_geojson(js, mode):
    feats = []
    for el in js.get("elements", []):
        props = dict(el.get("tags", {}))
        props["@osmId"] = f"{el['type']}/{el['id']}"
        if mode == "geom":
            g = el.get("geometry")
            if not g:
                continue
            geom = {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in g if p]}
        else:
            if el["type"] == "node":
                lon, lat = el.get("lon"), el.get("lat")
            else:
                c = el.get("center") or {}
                lon, lat = c.get("lon"), c.get("lat")
            if lon is None:
                continue
            geom = {"type": "Point", "coordinates": [lon, lat]}
        feats.append({"type": "Feature", "properties": props, "geometry": geom})
    return {"type": "FeatureCollection", "features": feats}


def _post(endpoint, bbox, snapshot, flt, overpass_stmt=None, props="tags"):
    """Pre-event OSM: ohsome first (as the brief suggests), Overpass attic data as fallback."""
    path = _cache([endpoint, bbox, snapshot, flt])
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    try:
        js = _ohsome(endpoint, bbox, snapshot, flt, props)
        SOURCES_USED.add("ohsome API")
    except Exception as e:
        if not overpass_stmt:
            raise
        log.warning("ohsome unavailable (%s); using Overpass attic snapshot instead", e)
        mode = "geom" if endpoint.endswith("geometry") else "center"
        js, url = _overpass(overpass_stmt, bbox, snapshot, mode)
        SOURCES_USED.add(f"Overpass API attic data ({url.split('/')[2]})")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(js, f)
    return js


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
    rx = "|".join(k.strip() for k in kinds.split(","))
    roads = _post("elements/geometry", big, snapshot, f"type:way and highway in ({kinds})",
                  f'way["highway"~"^({rx})$"](BBOX);')
    buildings = _post("elements/centroid", bbox, snapshot, "building=* and geometry:polygon",
                      'way["building"](BBOX);')
    places = _post("elements/centroid", big, snapshot,
                   "place in (city, town, village, hamlet) and name=*",
                   'nwr["place"~"^(city|town|village|hamlet)$"]["name"](BBOX);')
    rivers = _post("elements/geometry", big, snapshot, "type:way and waterway in (river, canal)",
                   'way["waterway"~"^(river|canal)$"](BBOX);')
    health = _post("elements/centroid", big, snapshot,
                   "amenity in (hospital, clinic, doctors) or healthcare in (hospital, clinic, centre)",
                   'nwr["amenity"~"^(hospital|clinic|doctors)$"](BBOX);nwr["healthcare"~"^(hospital|clinic|centre)$"](BBOX);')

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
    return dict(source=sorted(SOURCES_USED) or ['cache'], rivers=out_rivers, roads=out_roads, buildings=out_bld, places=out_places, health=out_health,
                snapshot=snapshot, bbox_buffered=big)
