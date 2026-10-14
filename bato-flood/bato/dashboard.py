"""Web map and printable report builders (used by the CLI and the Streamlit app)."""
from __future__ import annotations

import base64
import io
import os

import folium
from folium.plugins import MarkerCluster

from . import report

STATUS_COLOR = {"CUT OFF": "#dc2626", "UNCERTAIN": "#f59e0b", "REACHABLE": "#16a34a",
                "NO ROAD LINK": "#64748b", "NO ROAD": "#94a3b8"}

LEGEND = """
<div style="position: fixed; bottom: 18px; left: 18px; z-index: 9999; background: white; padding: 10px 12px;
 border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,.2); font: 12px/1.5 system-ui, sans-serif; color:#0f172a">
<b>Bato flood map</b><br>
<span style="background:#0ea5e9;width:12px;height:12px;display:inline-block"></span> new flood water<br>
<span style="background:#b45309;width:12px;height:12px;display:inline-block"></span> debris / scoured ground<br>
<span style="background:#2563eb;width:12px;height:12px;display:inline-block"></span> river (before and after)<br>
<span style="background:#94a3b8;width:12px;height:12px;display:inline-block"></span> not observable<br>
<span style="color:#dc2626">●</span> cut off &nbsp;<span style="color:#f59e0b">●</span> uncertain &nbsp;<span style="color:#16a34a">●</span> reachable<br>
<span style="color:#dc2626">━</span> road blocked &nbsp;<span style="color:#7c3aed">✚</span> hospital / town
</div>"""


def _png_uri(rgba):
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def folium_map(R, focus=None, show_buildings=True):
    from .pipeline import overlay_4326

    b = R.settings.bbox
    centre = focus or [(b[1] + b[3]) / 2, (b[0] + b[2]) / 2]
    m = folium.Map(location=centre, zoom_start=13 if focus else 11, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="Map (today's OSM, display only)").add_to(m)
    folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri World Imagery (display only)", name="Satellite basemap (display only)").add_to(m)
    rgba, bounds = overlay_4326(R)
    folium.raster_layers.ImageOverlay(_png_uri(rgba), bounds=bounds, name="Flood map (Sentinel-1/2)",
                                      opacity=0.85, interactive=False).add_to(m)
    folium.Rectangle([[b[1], b[0]], [b[3], b[2]]], color="#0f766e", weight=2, fill=False,
                     name="Analysis area").add_to(m)

    roads = folium.FeatureGroup(name="Roads blocked / uncertain")
    for e in R.edges:
        if e["state"] == "hit":
            folium.PolyLine([(p[1], p[0]) for p in e["lonlat"]], color="#dc2626", weight=5, opacity=0.9).add_to(roads)
        elif e["state"] == "unknown":
            folium.PolyLine([(p[1], p[0]) for p in e["lonlat"]], color="#94a3b8", weight=3, dash_array="4 6").add_to(roads)
    roads.add_to(m)

    br = folium.FeatureGroup(name="Bridges hit")
    for x in R.bridges:
        if x["state"] == "hit":
            folium.Marker([x["lat"], x["lon"]], tooltip=f"Bridge hit: {x['name']}",
                          icon=folium.DivIcon(html='<div style="font-size:20px;color:#dc2626;font-weight:900">✖</div>')).add_to(br)
    br.add_to(m)

    if show_buildings:
        bl = MarkerCluster(name="Buildings hit", options=dict(disableClusteringAtZoom=15))
        for x in [b for b in R.buildings if b["state"] == "hit"][:6000]:
            folium.CircleMarker([x["lat"], x["lon"]], radius=3, color="#991b1b", fill=True, weight=1).add_to(bl)
        bl.add_to(m)

    st = folium.FeatureGroup(name="Settlements (access status)")
    for s in R.settlements:
        col = STATUS_COLOR.get(s["status"], "#64748b")
        extra = ""
        if s.get("blocked_at"):
            extra = f"<br>Old route first blocked {s['blocked_at']['km_from_village']} km away"
        if s.get("km_now"):
            extra += f"<br>Route now {s['km_now']} km (before {s.get('km_before')} km)"
        html = (f"<b>{s['name']}</b> ({s['place']})<br><b style='color:{col}'>{s['status']}</b><br>"
                f"People: {s.get('people')} <small>({s.get('people_source')})</small>{extra}")
        folium.CircleMarker([s["lat"], s["lon"]], radius=7 if s["status"] == "CUT OFF" else 5, color="white",
                            weight=1.5, fill=True, fill_color=col, fill_opacity=1,
                            tooltip=f"{s['name']}: {s['status']}", popup=folium.Popup(html, max_width=260)).add_to(st)
        if s.get("blocked_at") and s["status"] == "CUT OFF":
            ba = s["blocked_at"]
            folium.CircleMarker([ba["lat"], ba["lon"]], radius=4, color="#dc2626", fill=False, weight=2,
                                tooltip=f"Blocks the route of {s['name']}").add_to(st)
    st.add_to(m)

    ds = folium.FeatureGroup(name="Hospitals and towns (destinations)")
    for d in R.destinations:
        folium.Marker([d["lat"], d["lon"]], tooltip=d["name"],
                      icon=folium.DivIcon(html='<div style="font-size:18px;color:#7c3aed;font-weight:900">✚</div>')).add_to(ds)
    ds.add_to(m)

    fp = getattr(R, "flowpath", None)
    if fp:
        folium.PolyLine(list(zip(fp["lat"], fp["lon"])), color="#7c3aed", weight=4, tooltip="Traced flood path").add_to(m)

    folium.LayerControl(collapsed=True).add_to(m)
    m.get_root().html.add_child(folium.Element(LEGEND))
    return m


PRINT_CSS = """
body{font:13px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;color:#0f172a;max-width:820px;margin:24px auto;padding:0 16px}
h1{font-size:20px;color:#0f766e;margin:0 0 6px}h2{font-size:15px;margin:14px 0 4px;color:#1e3a8a}
table{border-collapse:collapse;width:100%;font-size:12px}td,th{border:1px solid #e2e8f0;padding:3px 6px;text-align:left}
th{background:#f1f5f9}sub{color:#64748b}
@media print{.page-break{page-break-before:always}}
"""


def report_html(R, path):
    import markdown

    en = markdown.markdown(report.report_markdown(R.facts, "en"), extensions=["tables"])
    ne = markdown.markdown(report.report_markdown(R.facts, "ne"), extensions=["tables"])
    html = (f"<!doctype html><html><head><meta charset='utf-8'><title>Bato situation report</title>"
            f"<style>{PRINT_CSS}</style></head><body>{en}<div class='page-break'></div>{ne}</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
