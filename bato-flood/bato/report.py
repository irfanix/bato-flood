"""Facts table, one-page situation report (English + Nepali) and the guarded copilot.

Principle: the satellites measure, the code counts, the language model only words it.
Every number shown to a rescuer comes from the facts table. The copilot's answer is
checked number by number; if it contains any number that is not in the facts, the
answer is blocked and the deterministic answer is shown instead.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from .config import ATTRIBUTION

DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


# ------------------------------------------------------------------ facts
def build_facts(meta, flood_stats, impact_stats, bridges, settlements, cut_stats):
    F = []

    def add(fid, label_en, label_ne, value, unit="", layer=""):
        F.append(dict(id=fid, en=label_en, ne=label_ne, value=value, unit=unit, layer=layer))

    add("F1", "New flood water", "नयाँ बाढीको पानी", flood_stats["new_water_km2"], "km²", "flood map, class 2")
    add("F2", "Debris / scoured ground", "मलबा / बगाएको जमिन", flood_stats["debris_km2"], "km²", "flood map, class 3")
    add("F3", "Total flood-affected area", "जम्मा प्रभावित क्षेत्र", flood_stats["flood_total_km2"], "km²", "flood map, classes 2+3")
    add("F4", "Of which seen by both radar and optical", "रडार र अप्टिकल दुवैले देखेको", flood_stats["high_confidence_km2"], "km²", "confidence layer")
    add("F5", "Buildings hit", "क्षति पुगेका भवन", impact_stats["buildings_hit"], "", "OSM buildings x flood map")
    add("F6", "Buildings that could not be checked", "जाँच गर्न नसकिएका भवन", impact_stats["buildings_unknown"], "", "OSM buildings x not-observable mask")
    add("F7", "Road length blocked", "अवरुद्ध सडक", impact_stats["road_km_blocked"], "km", "OSM roads x flood map")
    add("F8", "Bridges hit", "क्षति पुगेका पुल", impact_stats["bridges_hit"], "", "OSM bridges x flood map")
    add("F9", "Settlements cut off", "सम्पर्कविहीन बस्ती", cut_stats["cut_off"], "", "road network analysis")
    add("F10", "People in cut-off settlements (estimate)", "सम्पर्कविहीन बस्तीका अनुमानित मानिस", cut_stats["people_cut_off"], "", "OSM population or buildings x 4.37")
    add("F11", "Settlements with uncertain access", "पहुँच अनिश्चित बस्ती", cut_stats["uncertain"], "", "road network analysis")
    add("F12", "Settlements still reachable", "पहुँच भएका बस्ती", cut_stats["reachable"], "", "road network analysis")
    add("F13", "Settlements analysed", "विश्लेषण गरिएका बस्ती", cut_stats["settlements"], "", "OSM places")
    add("F14", "Area not observable (radar shadow, cloud)", "देख्न नसकिएको क्षेत्र", flood_stats["not_observable_km2"], "km²", "observability mask")
    return dict(
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        meta=meta,
        facts=F,
        bridges_hit=[b for b in bridges if b["state"] == "hit"],
        settlements=[{k: v for k, v in s.items() if k in (
            "name", "name_ne", "place", "status", "people", "people_source", "destination", "km_before",
            "km_now", "blocked_at", "lon", "lat")} for s in settlements],
    )


def fact(facts, fid):
    for f in facts["facts"]:
        if f["id"] == fid:
            return f["value"]
    raise KeyError(fid)


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.1f}" if abs(v) < 100 else f"{v:.0f}"
    return str(v)


# ------------------------------------------------------------------ report
def report_markdown(facts, lang="en", top=10):
    m = facts["meta"]
    cut = [s for s in facts["settlements"] if s["status"] == "CUT OFF"][:top]
    unc = [s for s in facts["settlements"] if s["status"] == "UNCERTAIN"][:5]
    br = facts["bridges_hit"][:8]
    s1 = ", ".join(f"{t['pre_date']} → {t['post_date']} ({t['direction']})" for t in m.get("s1_pairs", [])) or "none"
    s2 = f"{m['s2_pre']} → {m['s2_post']}" if m.get("s2_post") else ("none" if lang == "en" else "छैन")
    L = []
    if lang == "en":
        L += [f"# Situation report: flood impact, {m['area_name']}",
              f"**Event date:** {m['event_date']}  ·  **Generated:** {facts['generated']}  ·  **Prototype, not an operational product**",
              f"Satellite images used: Sentinel-1 radar {s1}; Sentinel-2 optical {s2}. Roads and buildings: OpenStreetMap as of {m['osm_snapshot']}.",
              "", "## Key numbers", "| | Value | Source |", "|---|---|---|"]
        for f in facts["facts"]:
            L.append(f"| {f['en']} [{f['id']}] | **{_fmt(f['value'])}** {f['unit']} | {f['layer']} |")
        L += ["", f"## Cut-off settlements (top {len(cut)})"]
        if cut:
            L += ["| Settlement | People (est.) | First blocked point on old route |", "|---|---|---|"]
            for s in cut:
                b = s.get("blocked_at") or {}
                where = f"{b.get('km_from_village', '?')} km from village ({b.get('lat')}, {b.get('lon')})" if b else "n/a"
                L.append(f"| {s['name']} | {s['people']} | {where} |")
        else:
            L.append("No settlement lost its road link in this analysis.")
        if unc:
            L += ["", "**Access uncertain** (road passes areas the satellites could not see): " + ", ".join(s["name"] for s in unc)]
        L += ["", "## Bridges hit", ", ".join(f"{b['name']} ({b['lat']:.4f}, {b['lon']:.4f})" for b in br) or "None detected."]
        L += ["", "## Suggested priorities (for coordinators to judge)",
              f"- Cut-off settlements need air or foot access: start with the largest above (estimated {_fmt(fact(facts, 'F10'))} people in total).",
              f"- Verify the {_fmt(fact(facts, 'F8'))} bridges hit on the ground or with drone imagery before routing vehicles.",
              f"- {_fmt(fact(facts, 'F11'))} settlements have uncertain access: check them first by phone or radio if possible.",
              "", "## Limits",
              "Satellites pass every few days, so this map shows the situation on the image date, not now. "
              "Radar cannot see steep slopes facing away from it, and clouds block optical images. Buildings and people "
              "come from OpenStreetMap, which misses some villages. Numbers are estimates for planning, not a damage assessment.",
              "", "<sub>" + " ".join(ATTRIBUTION) + "</sub>"]
    else:
        L += [f"# स्थिति प्रतिवेदन: बाढी प्रभाव, {m['area_name']}",
              f"**घटना मिति:** {m['event_date']}  ·  **तयार गरिएको:** {facts['generated']}  ·  **प्रोटोटाइप, आधिकारिक उत्पादन होइन**",
              f"प्रयोग गरिएका उपग्रह तस्बिर: Sentinel-1 रडार {s1}; Sentinel-2 अप्टिकल {s2}। सडक र भवन: OpenStreetMap ({m['osm_snapshot']})।",
              "", "## मुख्य तथ्याङ्क", "| | मान | स्रोत |", "|---|---|---|"]
        unit_ne = {"km²": "वर्ग किमी", "km": "किमी", "": ""}
        for f in facts["facts"]:
            L.append(f"| {f['ne']} [{f['id']}] | **{_fmt(f['value'])}** {unit_ne.get(f['unit'], f['unit'])} | {f['layer']} |")
        L += ["", "## सम्पर्कविहीन बस्ती"]
        if cut:
            L += ["| बस्ती | अनुमानित मानिस | पुरानो बाटोमा पहिलो अवरोध |", "|---|---|---|"]
            for s in cut:
                b = s.get("blocked_at") or {}
                where = f"बस्तीबाट {b.get('km_from_village', '?')} किमी ({b.get('lat')}, {b.get('lon')})" if b else "-"
                L.append(f"| {s.get('name_ne') or s['name']} | {s['people']} | {where} |")
        else:
            L.append("यस विश्लेषणमा कुनै बस्तीको सडक सम्पर्क टुटेको देखिएन।")
        if unc:
            L += ["", "**पहुँच अनिश्चित:** " + ", ".join((s.get("name_ne") or s["name"]) for s in unc)]
        L += ["", "## क्षति पुगेका पुल", ", ".join(f"{b['name']} ({b['lat']:.4f}, {b['lon']:.4f})" for b in br) or "भेटिएन।"]
        L += ["", "## सुझाव गरिएका प्राथमिकता (समन्वयकर्ताको निर्णयका लागि)",
              f"- सम्पर्कविहीन बस्तीमा हवाई वा पैदल पहुँच चाहिन्छ (जम्मा अनुमानित {_fmt(fact(facts, 'F10'))} जना)।",
              f"- गाडी पठाउनु अघि क्षति पुगेका {_fmt(fact(facts, 'F8'))} पुल जमिनमै वा ड्रोनबाट जाँच गर्नुहोस्।",
              f"- पहुँच अनिश्चित {_fmt(fact(facts, 'F11'))} बस्तीलाई सम्भव भए फोन वा रेडियोबाट पहिले सम्पर्क गर्नुहोस्।",
              "", "## सीमितता",
              "उपग्रह केही दिनमा एक पटक मात्र आउँछ, त्यसैले यो नक्सा तस्बिर लिएको मितिको अवस्था हो। "
              "रडारले भिरालो छायाँ क्षेत्र र अप्टिकलले बादलमुनि देख्दैन। भवन र मानिसको सङ्ख्या OpenStreetMap मा आधारित अनुमान हो।",
              "", "<sub>Nepali text: template written with AI assistance; please have a native speaker review.</sub>",
              "", "<sub>" + " ".join(ATTRIBUTION) + "</sub>"]
    return "\n".join(L)


# ------------------------------------------------------------------ number guard
NUM = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*")


def _numbers(text: str) -> set[str]:
    text = text.translate(DEV_DIGITS)
    text = re.sub(r"\[F\d+\]", " ", text)                      # fact citations
    text = re.sub(r"(?m)^\s*(?:[-*]|\d+[.)])\s+", " ", text)   # list markers
    out = set()
    for n in NUM.findall(text):
        n = n.replace(",", "")
        try:
            v = float(n)
        except ValueError:
            continue
        out.add(f"{v:g}")
    return out


def allowed_numbers(facts, question=""):
    blob = json.dumps(facts, ensure_ascii=False) + " " + question
    nums = _numbers(blob)
    # Also allow values rounded to 0 or 1 decimal, as people naturally write them.
    for f in facts["facts"]:
        if isinstance(f["value"], (int, float)):
            for nd in (0, 1):
                nums.add(f"{round(float(f['value']), nd):g}")
    return nums


def guard(answer: str, facts, question=""):
    bad = sorted(_numbers(answer) - allowed_numbers(facts, question))
    return (len(bad) == 0), bad


# ------------------------------------------------------------------ copilot
def _match_settlement(q, facts):
    ql = q.lower()
    best = None
    for s in facts["settlements"]:
        for nm in {s.get("name"), s.get("name_ne")}:
            if nm and len(nm) >= 3 and nm.lower() in ql:
                if best is None or len(nm) > len(best[1]):
                    best = (s, nm)
    return best[0] if best else None


def rule_answer(q, facts, lang="en"):
    """Deterministic answers (always available, also the fallback)."""
    ql = q.lower()
    s = _match_settlement(q, facts)
    ne = lang == "ne"
    if s:
        st = s["status"]
        name = (s.get("name_ne") or s["name"]) if ne else s["name"]
        if st == "CUT OFF":
            b = s.get("blocked_at") or {}
            return (f"{name}: सम्पर्कविहीन। अनुमानित {s['people']} जना। पुरानो बाटो बस्तीबाट {b.get('km_from_village', '?')} किमीमा अवरुद्ध [F9]।" if ne else
                    f"{name} is CUT OFF from the road network. About {s['people']} people ({s['people_source']}). "
                    f"The old route to {s.get('destination')} is first blocked {b.get('km_from_village', '?')} km from the village "
                    f"at ({b.get('lat')}, {b.get('lon')}) [F9].")
        if st == "UNCERTAIN":
            return (f"{name}: पहुँच अनिश्चित, बाटो उपग्रहले देख्न नसकेको क्षेत्र भएर जान्छ [F11]।" if ne else
                    f"{name}: access UNCERTAIN. Its road passes sections the satellites could not see; check by phone or radio [F11].")
        if st == "REACHABLE":
            return (f"{name}: सडक पहुँच छ, {s.get('destination_now') or s.get('destination')} सम्म {s.get('km_now')} किमी (पहिले {s.get('km_before')} किमी) [F12]।" if ne else
                    f"{name} is still REACHABLE by road: {s.get('km_now')} km to the nearest town or hospital "
                    f"(before the flood: {s.get('km_before')} km) [F12].")
        return (f"{name}: घटना अघि पनि नक्सामा सडक सम्पर्क थिएन।" if ne else
                f"{name} had no mapped road link to a town or hospital even before the flood (status: {st}).")
    if any(k in ql for k in ("cut off", "cut-off", "isolated", "stranded", "सम्पर्क", "who")):
        cut = [x for x in facts["settlements"] if x["status"] == "CUT OFF"][:8]
        names = ", ".join(f"{(x.get('name_ne') or x['name']) if ne else x['name']} ({x['people']})" for x in cut) or "-"
        return (f"सम्पर्कविहीन बस्ती: {fact(facts, 'F9')} [F9], अनुमानित {fact(facts, 'F10')} जना [F10]। सबैभन्दा ठूला: {names}।" if ne else
                f"{fact(facts, 'F9')} settlements are cut off [F9], with about {fact(facts, 'F10')} people [F10]. Largest: {names}. "
                f"{fact(facts, 'F11')} more have uncertain access [F11].")
    if any(k in ql for k in ("bridge", "पुल")):
        names = ", ".join(b["name"] for b in facts["bridges_hit"][:8]) or "-"
        return (f"क्षति पुगेका पुल: {fact(facts, 'F8')} [F8]: {names}।" if ne else
                f"{fact(facts, 'F8')} bridges are hit [F8]: {names}.")
    if any(k in ql for k in ("building", "house", "home", "भवन", "घर")):
        return (f"क्षति पुगेका भवन: {fact(facts, 'F5')} [F5]; जाँच गर्न नसकिएका: {fact(facts, 'F6')} [F6]।" if ne else
                f"{fact(facts, 'F5')} buildings are in flooded or debris areas [F5]; {fact(facts, 'F6')} could not be checked [F6].")
    if any(k in ql for k in ("road", "सडक", "highway")):
        return (f"अवरुद्ध सडक: {fact(facts, 'F7')} किमी [F7]।" if ne else f"{fact(facts, 'F7')} km of road is blocked [F7].")
    return (f"बाढी प्रभावित क्षेत्र {fact(facts, 'F3')} वर्ग किमी [F3], क्षति पुगेका भवन {fact(facts, 'F5')} [F5], सम्पर्कविहीन बस्ती {fact(facts, 'F9')} [F9]।" if ne else
            f"Flood-affected area {fact(facts, 'F3')} km² [F3], {fact(facts, 'F5')} buildings hit [F5], "
            f"{fact(facts, 'F7')} km of road blocked [F7], {fact(facts, 'F9')} settlements cut off [F9].")


SYSTEM = """You are Bato, a copilot for disaster rescue coordinators.
Answer ONLY from the JSON facts given. Rules:
- Every number you write must appear exactly in the facts. Never estimate, add, or convert numbers.
- Cite the fact id in square brackets after each number, e.g. "12 settlements are cut off [F9]".
- If the facts do not contain the answer, say so plainly.
- Answer in {lang}. At most 120 words. No numbered lists.
- Be calm and respectful; this is a real disaster with affected families."""


def llm_answer(q, facts, lang="en", api_key=None, model="openai/gpt-oss-120b"):
    """Ask the LLM, then check every number. Returns dict(answer, source, blocked)."""
    import requests

    key = api_key or os.environ.get("GROQ_API_KEY")
    base = rule_answer(q, facts, lang)
    if not key:
        return dict(answer=base, source="rules", blocked=[])
    slim = dict(facts=facts["facts"], meta={k: facts["meta"][k] for k in ("area_name", "event_date", "osm_snapshot")},
                bridges_hit=[b["name"] for b in facts["bridges_hit"][:30]],
                settlements=[{k: s.get(k) for k in ("name", "name_ne", "status", "people", "km_before", "km_now", "blocked_at")}
                             for s in facts["settlements"][:80]])
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=60,
                          headers={"Authorization": f"Bearer {key}"},
                          json=dict(model=model, temperature=0.1, messages=[
                              {"role": "system", "content": SYSTEM.format(lang="Nepali" if lang == "ne" else "English")},
                              {"role": "user", "content": f"FACTS:\n{json.dumps(slim, ensure_ascii=False)}\n\nQUESTION: {q}"}]))
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return dict(answer=base, source=f"rules (AI unavailable: {type(e).__name__})", blocked=[])
    ok, bad = guard(text, facts, q)
    if not ok:
        return dict(answer=base, source="rules (AI answer blocked: numbers not in the maps)", blocked=bad, rejected=text)
    return dict(answer=text, source="AI, every number verified against the maps", blocked=[])
