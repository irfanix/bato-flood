"""Bato dashboard (Streamlit).  Run:  streamlit run app.py"""
from __future__ import annotations

import glob
import json
import os
import urllib.parse

import streamlit as st
import streamlit.components.v1 as components

from bato import report
from bato.config import Settings, TRISHULI_BBOX, TRISHULI_DATE, ATTRIBUTION

st.set_page_config(page_title="Bato: flood damage from space", page_icon="🛰️", layout="wide")

CSS = """
<style>
.block-container{padding-top:1.2rem}
.kpi{background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:10px 14px}
.kpi b{font-size:26px;color:#0f172a}.kpi span{display:block;color:#475569;font-size:13px}
.kpi.red b{color:#dc2626}.kpi.amber b{color:#d97706}
.status{border-radius:14px;padding:18px 20px;color:white;font-size:18px}
.badge{display:inline-block;border-radius:999px;padding:2px 10px;font-size:12px;background:#e0f2fe;color:#075985}
.badge.block{background:#fee2e2;color:#991b1b}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)

T = {
    "en": dict(title="Bato: flood damage and cut-off villages from space",
               tag="Satellites measure, code counts, AI only words it.",
               find="Find my village", cut="Cut off", unc="Uncertain", reach="Reachable"),
    "ne": dict(title="बाटो: अन्तरिक्षबाट बाढी क्षति र सम्पर्कविहीन गाउँ",
               tag="उपग्रहले नाप्छ, कोडले गन्छ, एआईले मात्र भन्छ।",
               find="मेरो गाउँ खोज्नुहोस्", cut="सम्पर्कविहीन", unc="अनिश्चित", reach="पहुँच छ"),
}
STATUS_COL = {"CUT OFF": "#dc2626", "UNCERTAIN": "#d97706", "REACHABLE": "#16a34a", "NO ROAD LINK": "#64748b", "NO ROAD": "#94a3b8"}

def _secret(k):
    try:
        return st.secrets.get(k, "") or os.environ.get(k, "")
    except Exception:
        return os.environ.get(k, "")


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### 🛰️ Bato")
    lang = st.radio("Language / भाषा", ["en", "ne"], format_func=lambda x: "English" if x == "en" else "नेपाली", horizontal=True)
    role = st.selectbox("I am a ...", ["Rescue coordinator", "Local official", "Family member", "Analyst / judge"])
    st.divider()
    cases = sorted(d for d in glob.glob("case_study*") if os.path.exists(os.path.join(d, "facts.json")))
    mode = st.radio("Data", (["Saved case study"] if cases else []) + ["Run a new analysis"])
    out_dir = None
    if mode == "Saved case study":
        out_dir = st.selectbox("Case study", cases)
    else:
        preset = st.checkbox("Trishuli, Aug 2026 (case study)", value=True)
        if preset:
            bbox, date = TRISHULI_BBOX, TRISHULI_DATE
        else:
            c1, c2 = st.columns(2)
            lon0 = c1.number_input("West lon", value=85.10, format="%.4f")
            lat0 = c2.number_input("South lat", value=27.88, format="%.4f")
            lon1 = c1.number_input("East lon", value=85.45, format="%.4f")
            lat1 = c2.number_input("North lat", value=28.30, format="%.4f")
            bbox = (lon0, lat0, lon1, lat1)
            date = st.date_input("Flood date").isoformat()
        name = st.text_input("Area name", "Bhote Koshi - Trishuli corridor, Nepal" if preset else "Custom area")
        res = st.select_slider("Grid (m)", [10, 20, 30, 40], value=20 if preset else 30)
        if st.button("▶ Run analysis from raw satellite data", type="primary"):
            from bato.pipeline import run
            bar = st.progress(0.0, "Starting")
            target = f"runs/{date}_{abs(hash(bbox)) % 10**6}"
            try:
                run(Settings(bbox=tuple(bbox), event_date=date, resolution_m=res), name, target,
                    progress=lambda m, f: bar.progress(min(1.0, f), m))
                st.session_state["out_dir"] = target
            except Exception as e:
                st.error(f"Run failed: {e}")
        out_dir = st.session_state.get("out_dir")
    st.divider()
    key = st.text_input("Groq API key (optional, for the AI copilot)", type="password",
                        value=_secret("GROQ_API_KEY"))

t = T[lang]
st.markdown(f"## {t['title']}")
st.caption(f"**{t['tag']}** Educational prototype, not an operational tool. "
           "No images of victims are used.")

if not out_dir:
    st.info("Choose the saved case study or run a new analysis in the sidebar.")
    st.stop()


@st.cache_resource(show_spinner=False)
def load(d):
    from bato.pipeline import load_dir
    return load_dir(d)


R = load(out_dir)
facts = R.facts
F = {f["id"]: f for f in facts["facts"]}
sett = facts["settlements"]
m = facts["meta"]


def kpis():
    cols = st.columns(5)
    items = [("F9", "red"), ("F10", "red"), ("F8", "amber"), ("F5", ""), ("F3", "")]
    for c, (fid, cl) in zip(cols, items):
        f = F[fid]
        c.markdown(f"<div class='kpi {cl}'><b>{f['value']}</b> {f['unit']}<span>{f[lang]}</span></div>", unsafe_allow_html=True)
    s1 = "; ".join(f"{p['pre_date']} → {p['post_date']} ({p['direction']})" for p in m.get("s1_pairs", []))
    st.caption(f"Event {m['event_date']} · Sentinel-1: {s1 or 'none'} · Sentinel-2: "
               f"{(m.get('s2_pre') or '-') + ' → ' + (m.get('s2_post') or '-')} · OSM snapshot {m['osm_snapshot']}")


def tab_map():
    kpis()
    path = os.path.join(out_dir, "map.html")
    components.html(open(path, encoding="utf-8").read(), height=620)


def tab_cutoff():
    st.markdown("Each village is checked for a road route to the nearest **town or hospital**, "
                "before and after the flood, on the pre-event OpenStreetMap road network.")
    import pandas as pd
    df = pd.DataFrame([{**s, "blocked_km_from_village": (s.get("blocked_at") or {}).get("km_from_village")} for s in sett])
    if df.empty:
        st.warning("No named settlements in this area.")
        return
    show = st.multiselect("Status", list(STATUS_COL), default=["CUT OFF", "UNCERTAIN"])
    cols = ["status", "name", "name_ne", "place", "people", "people_source", "destination", "km_before", "km_now",
            "blocked_km_from_village", "lat", "lon"]
    st.dataframe(df[df.status.isin(show)][[c for c in cols if c in df]], use_container_width=True, hide_index=True)
    st.download_button("⬇ settlements.csv", open(os.path.join(out_dir, "settlements.csv"), "rb"), "settlements.csv")


def tab_family():
    st.markdown(f"### {t['find']}")
    names = sorted({s["name"] for s in sett if s.get("name")})
    pick = st.selectbox("Village / गाउँ", [""] + names)
    if not pick:
        return
    s = next(x for x in sett if x["name"] == pick)
    ans = report.rule_answer(pick, facts, lang)
    st.markdown(f"<div class='status' style='background:{STATUS_COL.get(s['status'], '#64748b')}'>"
                f"<b>{s.get('name_ne') if lang == 'ne' and s.get('name_ne') else s['name']}</b>: {s['status']}</div>",
                unsafe_allow_html=True)
    st.write(ans)
    st.caption(f"Based on satellite images of {', '.join(p['post_date'] for p in m.get('s1_pairs', [])) or '-'}. "
               "The situation may have changed since. Contact local authorities for official information.")
    msg = f"Bato ({m['event_date']} flood): {ans}"
    st.link_button("Share on WhatsApp", "https://wa.me/?text=" + urllib.parse.quote(msg))


def tab_report():
    l2 = st.radio("Report language", ["en", "ne"], index=0 if lang == "en" else 1, horizontal=True,
                  format_func=lambda x: "English" if x == "en" else "नेपाली")
    md = report.report_markdown(facts, l2)
    st.download_button("⬇ One-page report (HTML, print to PDF)", open(os.path.join(out_dir, "situation_report.html"), "rb"),
                       "bato_situation_report.html")
    st.markdown(md, unsafe_allow_html=True)


def tab_ask():
    st.markdown("Ask in English or Nepali. Every number in the answer is checked against the maps; "
                "if the AI writes a number that is not in the facts, its answer is **blocked** and the rule-based answer is shown.")
    ex = ["Who is cut off?", "Which bridges are hit?", "How many buildings were hit?", "कुन बस्ती सम्पर्कविहीन छन्?"]
    c = st.columns(len(ex))
    q = None
    for i, e in enumerate(ex):
        if c[i].button(e):
            q = e
    q = st.chat_input("Ask Bato ...") or q
    hist = st.session_state.setdefault("chat", [])
    if q:
        ql = "ne" if any("ऀ" <= ch <= "ॿ" for ch in q) else lang
        res = report.llm_answer(q, facts, ql, api_key=key or None)
        hist.append((q, res))
    for q0, r0 in reversed(hist[-8:]):
        with st.chat_message("user"):
            st.write(q0)
        with st.chat_message("assistant"):
            st.write(r0["answer"])
            cls = "block" if r0.get("blocked") else ""
            st.markdown(f"<span class='badge {cls}'>{r0['source']}</span>", unsafe_allow_html=True)
            if r0.get("blocked"):
                with st.expander("Why was the AI answer blocked?"):
                    st.write(f"It contained numbers not found in the maps: {r0['blocked']}")
                    st.code(r0.get("rejected", ""))
    with st.expander("Facts table the copilot is allowed to use"):
        st.table([{"id": f["id"], "fact": f[lang], "value": f["value"], "unit": f["unit"], "from layer": f["layer"]} for f in facts["facts"]])


def tab_flow():
    st.markdown("**Bonus:** pick any point upstream. Bato follows the steepest way down the Copernicus DEM "
                "and lists the settlements along the path.")
    if R.dem is None:
        st.info("Elevation not saved for this run.")
        return
    b = m["bbox"]
    c1, c2 = st.columns(2)
    lon = c1.number_input("Start longitude", value=float(b[2] - 0.08), format="%.5f")
    lat = c2.number_input("Start latitude", value=float(b[3] - 0.05), format="%.5f")
    if st.button("Trace flood path"):
        from bato.flowpath import trace, settlements_along, FRONT_SPEED_MS
        import folium
        try:
            fp = trace(R.dem, R.proj, lon, lat, abs(R.grid.transform.a))
        except ValueError as e:
            st.error(str(e))
            return
        along = settlements_along(fp, R.places, R.proj)
        st.success(f"Path {fp['length_km']} km, drop {fp['drop_m']:.0f} m, {len(along)} settlements within 600 m.")
        mp = folium.Map(location=[lat, lon], zoom_start=11)
        folium.PolyLine(list(zip(fp["lat"], fp["lon"])), color="#7c3aed", weight=4).add_to(mp)
        folium.Marker([lat, lon], tooltip="start").add_to(mp)
        for s in along:
            folium.CircleMarker([s["lat"], s["lon"]], radius=6, color="#dc2626", fill=True,
                                tooltip=f"{s['name']}: {s['km_downstream']} km").add_to(mp)
        components.html(mp._repr_html_(), height=420)
        st.dataframe([{k: v for k, v in s.items() if k not in ("lon", "lat")} for s in along], hide_index=True)
        st.caption(f"'minutes_rough' assumes a flood front speed of {FRONT_SPEED_MS[0]} to {FRONT_SPEED_MS[1]} m/s. "
                   "It is an illustration, not a warning: real speeds depend on volume and channel shape.")


def tab_check():
    st.markdown("Case-study check against the official **Copernicus EMS** map (activation EMSR927). "
                "EMS data is used **only here, after the run, to score Bato**, never as an input.")
    fig = os.path.join(out_dir, "ems_comparison.png")
    js = os.path.join(out_dir, "ems_comparison.json")
    up = st.file_uploader("EMS vector package (.zip)", type=["zip"])
    if up:
        from bato.compare import compare
        p = os.path.join(out_dir, "ems_upload.zip")
        open(p, "wb").write(up.getvalue())
        res = compare(R.cls, R.grid, [p], None, fig)
        json.dump(res, open(js, "w"), indent=1)
    if os.path.exists(js):
        res = json.load(open(js))
        c = st.columns(5)
        for col, k in zip(c, ["precision", "recall", "f1", "iou", "overlap_km2"]):
            col.metric(k, res[k])
        st.write(f"Reference flooded area {res['reference_km2']} km², Bato {res['ours_km2']} km².")
        if os.path.exists(fig):
            st.image(fig, caption="Green: both agree · Orange: only Bato · Blue: only EMS", use_container_width=True)


def tab_method():
    st.markdown(open("docs/METHOD.md", encoding="utf-8").read() if os.path.exists("docs/METHOD.md") else "")
    st.markdown("**Attribution**\n\n" + "\n".join(f"- {a}" for a in ATTRIBUTION))
    with st.expander("Settings used (every threshold)"):
        st.json(m.get("settings", {}))
    with st.expander("Processing times (s)"):
        st.json(facts.get("timings_s", {}))


ORDER = {
    "Rescue coordinator": [("🚨 Who is cut off", tab_cutoff), ("🗺️ Map", tab_map), ("💬 Ask Bato", tab_ask),
                           ("📄 Situation report", tab_report), ("🌊 Flood path", tab_flow), ("✅ Check vs EMS", tab_check), ("ℹ️ Method & limits", tab_method)],
    "Local official": [("🗺️ Map", tab_map), ("📄 Situation report", tab_report), ("🚨 Who is cut off", tab_cutoff),
                       ("💬 Ask Bato", tab_ask), ("ℹ️ Method & limits", tab_method)],
    "Family member": [("🏠 " + t["find"], tab_family), ("🗺️ Map", tab_map), ("💬 Ask Bato", tab_ask)],
    "Analyst / judge": [("🗺️ Map", tab_map), ("✅ Check vs EMS", tab_check), ("🚨 Who is cut off", tab_cutoff),
                        ("💬 Ask Bato", tab_ask), ("📄 Situation report", tab_report), ("🌊 Flood path", tab_flow), ("ℹ️ Method & limits", tab_method)],
}
tabs = ORDER[role]
for (label, fn), tb in zip(tabs, st.tabs([x[0] for x in tabs])):
    with tb:
        fn()
