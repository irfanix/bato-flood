"""Command line:  python -m bato run --preset trishuli
                python -m bato run --bbox 85.1 27.88 85.45 28.3 --date 2026-08-26
                python -m bato run --preset trishuli --flow 85.37 28.25 --ems EMSR927.zip
"""
from __future__ import annotations

import argparse
import json
import logging
import os

from .config import Settings, TRISHULI_BBOX, TRISHULI_DATE


def main():
    ap = argparse.ArgumentParser(prog="bato", description="Flood damage and cut-off settlements from space")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--preset", choices=["trishuli"])
    r.add_argument("--bbox", nargs=4, type=float, metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    r.add_argument("--date", help="flood date YYYY-MM-DD")
    r.add_argument("--name", default=None, help="area name for the report")
    r.add_argument("--out", default="out")
    r.add_argument("--res", type=int, default=20, help="grid resolution in metres")
    r.add_argument("--s1-pre", help="optional local Sentinel-1 VV GeoTIFF before the event")
    r.add_argument("--s1-post", help="optional local Sentinel-1 VV GeoTIFF after the event")
    r.add_argument("--flow", nargs=2, type=float, metavar=("LON", "LAT"), help="bonus: trace flood path from this point")
    r.add_argument("--ems", nargs="*", help="Copernicus EMS package(s) for the case-study CHECK only")
    r.add_argument("--paths", action="store_true", help="count foot trails as roads")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if a.preset == "trishuli":
        bbox, date, name = TRISHULI_BBOX, TRISHULI_DATE, "Bhote Koshi - Trishuli corridor, Nepal"
    else:
        if not (a.bbox and a.date):
            ap.error("give --preset or both --bbox and --date")
        bbox, date, name = tuple(a.bbox), a.date, "Custom area"
    s = Settings(bbox=bbox, event_date=a.date or date, resolution_m=a.res, include_paths=a.paths)

    from .pipeline import run, write_outputs
    R = run(s, a.name or name, a.out, a.s1_pre, a.s1_post, progress=lambda m, f: print(f"[{int(f*100):3d}%] {m}"))

    if a.flow:
        from .flowpath import trace, settlements_along
        fp = trace(R.dem, R.proj, a.flow[0], a.flow[1], s.resolution_m, s.flow_max_km)
        along = settlements_along(fp, R.osm["places"], R.proj)
        R.flowpath = fp
        with open(os.path.join(a.out, "flowpath.json"), "w", encoding="utf-8") as f:
            json.dump(dict(length_km=fp["length_km"], drop_m=fp["drop_m"], settlements=along,
                           line=list(zip(fp["lon"], fp["lat"]))), f, ensure_ascii=False, indent=1)
        print(f"Flood path {fp['length_km']} km, {len(along)} settlements along it")
        write_outputs(R)

    if a.ems:
        from .compare import compare
        res = compare(R.cls, R.grid, a.ems, R.buildings, os.path.join(a.out, "ems_comparison.png"))
        with open(os.path.join(a.out, "ems_comparison.json"), "w") as f:
            json.dump(res, f, indent=1)
        print("EMS comparison:", json.dumps({k: v for k, v in res.items() if k != "ems_layers"}))

    fs = R.facts
    print("\nKEY NUMBERS")
    for f in fs["facts"]:
        print(f"  [{f['id']}] {f['en']}: {f['value']} {f['unit']}")
    print(f"\nOutputs in {os.path.abspath(a.out)}: map.html, situation_report.html, facts.json, settlements.csv ...")


if __name__ == "__main__":
    main()
