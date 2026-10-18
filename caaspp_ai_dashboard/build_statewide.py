#!/usr/bin/env python3
"""
Build data/statewide.json -- the compact statewide comparison data behind the dashboard's
"Statewide comparison" tab -- from CDE's statewide CAASPP research files in
../caaspp_files/california/ (one sb_caYYYY_1_csv_vN/ folder per year, each holding the score
file and its entities file). Those raw files are ~224MB and stay out of git; this script's
output (a few hundred KB) is what gets committed and deployed.

The statewide "_1_" files are All Students only. Every entity is scored on one measure:

  Percent meeting or exceeding standard, grades 3-8 combined. This is the average of the
  grade 3-8 rows' "met and above" percentages, weighted by each grade's students with scores.
  Grades suppressed by CDE (fewer than 11 students with scores) are left out. Using grades 3-8,
  not CDE's all-grades row, keeps unified and high-school districts comparable to SMFCSD, which
  is K-8. For SMFCSD itself this equals its published all-grades figure (checked at the end).

Entities kept: the state, all 58 counties (CDE's county roll-up rows), and every district
entity (Type ID 6). Two kinds of district-level entity aren't school districts and get a `kind`
so the dashboard lists them but leaves them out of rankings:
  - "coe": county offices of education. CDE gives every one a district code in the 10xxx range
    (10017-10587); matching on that, not the name, catches the ones whose names CDE truncates
    ("...Office of Educatio") or words differently ("Department of Education",
    "Superintendent of Schools").
  - "sbe": State Board of Education-authorized charters, which CDE lists as their own
    single-school "districts" (names start "SBE - ", sometimes "Sbe - ").
Other charter schools are school-level entities in CDE's files and aren't included.

Format eras: 2015-2019 comma-delimited, 2021+ caret-delimited, 2026 relabels the achievement
levels ("Proficient and Above"); several files are Latin-1, not UTF-8. Columns are resolved by
name per file.

Usage:
    cd caaspp_ai_dashboard
    python3 build_statewide.py
    python3 verify_statewide_data.py -v   # independent cross-check of the output
"""
import csv
import glob
import json
import os
import re
import sys

import verify_source_data as v

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_DIR = os.path.join(APP_DIR, "..", "caaspp_files", "california")
OUT_PATH = os.path.join(APP_DIR, "data", "statewide.json")

GRADES = ("3", "4", "5", "6", "7", "8")
SUBJECTS = {"1": "ela", "2": "math"}
SMFCSD = ("41", "69039")

TEST_ID_COLS = ["Test ID", "Test Id"]
TYPE_ID_COLS = ["Type ID", "Type Id"]
MET_ABOVE_COLS = ["Percentage Standard Met and Above", "Percentage Proficient and Above"]
SCORED_COLS = ["Total Students Tested with Scores", "Students with Scores"]


def read_rows(path):
    raw = open(path, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    delimiter = "," if text.startswith('"') else "^"
    return list(csv.DictReader(text.splitlines(), delimiter=delimiter))


def year_files():
    out = []
    for folder in sorted(glob.glob(os.path.join(SOURCE_DIR, "sb_ca*_1_csv_v*"))):
        m = re.search(r"sb_ca(\d{4})_", os.path.basename(folder))
        scores = glob.glob(os.path.join(folder, "sb_ca*_1_csv_v*.txt"))
        entities = glob.glob(os.path.join(folder, "sb_ca*entities_csv.txt"))
        if not (m and len(scores) == 1 and len(entities) == 1):
            sys.exit(f"Unexpected contents in {folder}")
        out.append((int(m.group(1)), scores[0], entities[0]))
    if not out:
        sys.exit(f"No statewide folders found in {SOURCE_DIR}")
    return out


def district_kind(district_code, name):
    """None for a school district; "coe" / "sbe" for entities left out of rankings (see docstring)."""
    if re.fullmatch(r"10\d{3}", district_code):
        return "coe"
    if name.upper().startswith("SBE - "):  # CDE also spells it "Sbe - " in some years
        return "sbe"
    return None


def num(raw):
    raw = (raw or "").strip()
    return None if raw in ("", "*") else float(raw)


def main():
    files = year_files()
    labels = [v.year_label_for(y) for y, _, _ in files]
    n = len(labels)

    # entity key -> {"names", "county", "ela": [...], "math": [...], "ela_n": [...], ...}
    state = None
    counties, districts = {}, {}

    def series():
        return {f: [None] * n for s in SUBJECTS.values() for f in (s, s + "_n")}

    for i, (year, score_path, entity_path) in enumerate(files):
        entities = read_rows(entity_path)
        county_names = {}
        for e in entities:
            t = v.field(e, TYPE_ID_COLS)
            if t == "5":
                county_names[e["County Code"]] = e["County Name"].strip()
        for e in entities:
            if v.field(e, TYPE_ID_COLS) != "6":
                continue
            key = (e["County Code"], e["District Code"])
            d = districts.setdefault(key, {"names": {}, **series()})
            d["names"][year] = e["District Name"].strip()
            d["county"] = e["County Code"]
        for code, name in county_names.items():
            counties.setdefault(code, {"names": {}, **series()})["names"][year] = name

        # Weighted grade 3-8 sums per (level key, subject)
        sums = {}
        for r in read_rows(score_path):
            if r["Grade"] not in GRADES or r["School Code"] != "0000000":
                continue
            subj = SUBJECTS.get(v.field(r, TEST_ID_COLS))
            pct, scored = num(v.field(r, MET_ABOVE_COLS)), num(v.field(r, SCORED_COLS))
            if subj is None or pct is None or not scored:
                continue
            cc, dc = r["County Code"], r["District Code"]
            level = "state" if cc == "00" else ("county", cc) if dc == "00000" else ("district", cc, dc)
            acc = sums.setdefault((level, subj), [0.0, 0])
            acc[0] += pct * scored
            acc[1] += int(scored)

        for (level, subj), (weighted, scored) in sums.items():
            if level == "state":
                if state is None:
                    state = series()
                target = state
            elif level[0] == "county":
                target = counties.get(level[1])
            else:
                target = districts.get(level[1:])
            if target is None:
                continue  # score row without a matching entity of the expected type
            target[subj][i] = round(weighted / scored, 1)
            target[subj + "_n"][i] = scored
        print(f"{labels[i]}: {len(county_names)} counties, "
              f"{sum(1 for d in districts.values() if d['ela'][i] is not None or d['math'][i] is not None)} districts with scores")

    def latest_name(names):
        return names[max(names)]

    def pack(d):
        return {k: d[k] for k in ("ela", "math", "ela_n", "math_n")}

    out_counties = [
        {"code": code, "name": latest_name(c["names"]), **pack(c)}
        for code, c in sorted(counties.items(), key=lambda kv: latest_name(kv[1]["names"]))
    ]
    out_districts = []
    for (cc, dc), d in districts.items():
        if all(x is None for s in SUBJECTS.values() for x in d[s]):
            continue
        name = latest_name(d["names"])
        entry = {"id": f"{cc}|{dc}", "name": name, "county": cc}
        kind = district_kind(dc, name)
        if kind:
            entry["kind"] = kind
        out_districts.append({**entry, **pack(d)})
    out_districts.sort(key=lambda d: (d["name"], d["id"]))

    out = {
        "years": labels,
        "measure": "Percent meeting or exceeding standard, grades 3-8 combined (All Students)",
        "smfcsd": "|".join(SMFCSD),
        "state": pack(state),
        "counties": out_counties,
        "districts": out_districts,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\nWrote {OUT_PATH}: {len(out_counties)} counties, {len(out_districts)} districts "
          f"({sum(d.get('kind') == 'coe' for d in out_districts)} county offices, "
          f"{sum(d.get('kind') == 'sbe' for d in out_districts)} State Board charters; not ranked), "
          f"{os.path.getsize(OUT_PATH) / 1024:.0f} KB")

    # Sanity check: SMFCSD is K-8, so its grade 3-8 figure should match the district dashboard's
    # published all-grades value.
    with open(v.DATA_PATH, encoding="utf-8") as f:
        district_data = json.load(f)
    smfcsd = next(d for d in out_districts if d["id"] == out["smfcsd"])
    worst = 0.0
    for t, s in SUBJECTS.items():
        published = district_data["data"][f"0000000|13|{t}|1"]["met_above"]
        for label, mine in zip(labels, smfcsd[s]):
            pub = published[district_data["years"].index(label)]
            if mine is None or pub is None:
                if mine != pub:
                    sys.exit(f"SMFCSD {s} {label}: statewide={mine} district dashboard={pub}")
                continue
            worst = max(worst, abs(mine - pub))
    print(f"SMFCSD check: grade 3-8 figures within {worst:.1f} pts of the district dashboard's published values")


if __name__ == "__main__":
    main()
