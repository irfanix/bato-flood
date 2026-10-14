"""Question 3: who is cut off?

Build the pre-event road graph, remove edges the flood hit, and check whether each
settlement can still reach the nearest town or hospital.

Status per settlement:
  CUT OFF       had a road link before, has none now (even if every unclear road is open)
  UNCERTAIN     link depends on road sections the satellites could not see
  REACHABLE     still linked using only roads observed as open (detour shown)
  NO ROAD LINK  not linked to a town/hospital even before the event (in OSM)
  NO ROAD       no mapped road within the snap distance
"""
from __future__ import annotations

import networkx as nx
import numpy as np
from scipy.spatial import cKDTree

from .impact import HIT, UNKNOWN

SRC = "__DESTINATIONS__"
CUT, UNCERTAIN, REACH, NOLINK, NOROAD = "CUT OFF", "UNCERTAIN", "REACHABLE", "NO ROAD LINK", "NO ROAD"


def build_graph(edges):
    G = nx.Graph()
    for i, e in enumerate(edges):
        a, b = e["a"], e["b"]
        if a == b:
            continue
        if G.has_edge(a, b):  # keep the worse state if two ways share a segment
            if e["state"] == HIT:
                G[a][b]["state"] = HIT
            continue
        G.add_edge(a, b, w=e["length"], state=e["state"], idx=i)
    return G


def _snap(tree, nodes, x, y, max_m):
    d, i = tree.query([x, y])
    return (nodes[i], float(d)) if d <= max_m else (None, float(d))


def _search(G, dest_nodes, allow):
    """Dijkstra from all destinations at once, through edges whose state is allowed."""
    H = G
    for n in dest_nodes:
        H.add_edge(SRC, n, w=0.0, state="virtual", idx=-1)
    view = nx.subgraph_view(H, filter_edge=lambda u, v: H[u][v]["state"] in allow or H[u][v]["state"] == "virtual")
    pred, dist = nx.dijkstra_predecessor_and_distance(view, SRC, weight="w")
    return pred, dist


def _trace(pred, node):
    path = [node]
    while path[-1] != SRC:
        p = pred.get(path[-1])
        if not p:
            return None
        path.append(p[0])
    return path


def analyse(edges, places, health, proj, aoi_bbox, s, buildings=None, household=4.37):
    G = build_graph(edges)
    if G.number_of_nodes() == 0:
        return [], dict(settlements=0), []
    nodes = list(G.nodes)
    tree = cKDTree(np.array(nodes))

    # Destinations: towns/cities and hospitals (health posts are reported, not destinations).
    dests = []
    for p in places:
        if p["place"] in ("town", "city"):
            dests.append(dict(name=p["name"], kind=p["place"], lon=p["lon"], lat=p["lat"]))
    for h in health:
        if h["kind"] == "hospital":
            dests.append(dict(name=h["name"], kind="hospital", lon=h["lon"], lat=h["lat"]))
    dest_node_name = {}
    for d in dests:
        x, y = proj.xy(d["lon"], d["lat"])
        n, dd = _snap(tree, nodes, x, y, s.snap_max_m)
        if n is not None:
            dest_node_name.setdefault(n, f"{d['name']} ({d['kind']})")
    dest_nodes = list(dest_node_name)

    open_states = {"open", "outside"}
    pred_pre, dist_pre = _search(G, dest_nodes, open_states | {HIT, UNKNOWN})
    pred_opt, dist_opt = _search(G, dest_nodes, open_states | {UNKNOWN})
    pred_pes, dist_pes = _search(G, dest_nodes, open_states)

    # Each building counts for its NEAREST settlement only (within 1 km), so neighbouring
    # villages do not share and double-count the same houses.
    owner_count = {}
    cand = [p for p in places if p["place"] in ("village", "hamlet", "town", "city")]
    if buildings and cand:
        pxy = np.array([proj.xy(p["lon"], p["lat"]) for p in cand])
        d_, i_ = cKDTree(pxy).query(np.array([[b["x"], b["y"]] for b in buildings]))
        for dist_, idx_ in zip(d_, i_):
            if dist_ <= 1000:
                owner_count[cand[idx_]["id"]] = owner_count.get(cand[idx_]["id"], 0) + 1

    lon0, lat0, lon1, lat1 = aoi_bbox
    out = []
    for p in places:
        if p["place"] not in ("village", "hamlet", "town", "city"):
            continue
        if not (lon0 <= p["lon"] <= lon1 and lat0 <= p["lat"] <= lat1):
            continue
        x, y = proj.xy(p["lon"], p["lat"])
        n, snapd = _snap(tree, nodes, x, y, s.snap_max_m)
        rec = dict(name=p["name"], name_ne=p.get("name_ne"), place=p["place"], lon=p["lon"], lat=p["lat"],
                   osm_id=p["id"], snap_m=round(snapd))
        nb = owner_count.get(p["id"], 0)
        rec["buildings_nearby"] = nb
        if p.get("population"):
            rec["people"], rec["people_source"] = p["population"], "OSM population tag"
        else:
            rec["people"], rec["people_source"] = int(round(nb * household)), "estimate: buildings x 4.37"
        if n is None:
            rec["status"] = NOROAD
        elif n not in dist_pre:
            rec["status"] = NOLINK
        else:
            path = _trace(pred_pre, n)
            dest = dest_node_name.get(path[-2]) if path and len(path) > 1 else None
            rec["destination"] = dest
            rec["km_before"] = round(dist_pre[n] / 1000, 1)
            if n in dist_pes:
                rec["status"] = REACH
                rec["km_now"] = round(dist_pes[n] / 1000, 1)
                p2 = _trace(pred_pes, n)
                rec["destination_now"] = dest_node_name.get(p2[-2]) if p2 and len(p2) > 1 else None
            elif n in dist_opt:
                rec["status"] = UNCERTAIN
                rec["km_now"] = round(dist_opt[n] / 1000, 1)
            else:
                rec["status"] = CUT
            # Where is the first blocked section on the old route?
            if rec["status"] != REACH and path:
                for u, v in zip(path[:-2], path[1:-1]):
                    if G[u][v]["state"] in (HIT, UNKNOWN) and (rec["status"] == UNCERTAIN or G[u][v]["state"] == HIT):
                        lon, lat = proj.lonlat((u[0] + v[0]) / 2, (u[1] + v[1]) / 2)
                        along = 0.0
                        for a, b in zip(path, path[1:]):
                            if (a, b) == (u, v):
                                break
                            along += G[a][b]["w"]
                        rec["blocked_at"] = dict(lon=round(lon, 5), lat=round(lat, 5), km_from_village=round(along / 1000, 1),
                                                 state=G[u][v]["state"])
                        break
        out.append(rec)

    order = {CUT: 0, UNCERTAIN: 1, REACH: 2, NOLINK: 3, NOROAD: 4}
    out.sort(key=lambda r: (order[r["status"]], -r.get("people", 0)))
    stats = dict(
        settlements=len(out),
        cut_off=sum(r["status"] == CUT for r in out),
        uncertain=sum(r["status"] == UNCERTAIN for r in out),
        reachable=sum(r["status"] == REACH for r in out),
        no_link_before=sum(r["status"] in (NOLINK, NOROAD) for r in out),
        people_cut_off=sum(r.get("people", 0) for r in out if r["status"] == CUT),
        people_uncertain=sum(r.get("people", 0) for r in out if r["status"] == UNCERTAIN),
        destinations=len(dest_nodes),
        longer_route=sum(1 for r in out if r["status"] == REACH and r.get("km_now", 0) > 1.5 * r.get("km_before", 1e9)),
    )
    dest_list = [dict(name=v, lon=proj.lonlat(*k)[0], lat=proj.lonlat(*k)[1]) for k, v in dest_node_name.items()]
    G.remove_node(SRC) if SRC in G else None
    return out, stats, dest_list
