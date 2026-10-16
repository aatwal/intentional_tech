#!/usr/bin/env python3
"""
Rebuild DATA (data/caaspp_data.json + the embedded `const DATA = ...` line in
templates/index.html) from the raw CDE CAASPP research files in ../caaspp_files/.

Before this script existed DATA was a one-off extraction with no checked-in generator; the
record-selection and rounding rules below were reverse-engineered from that extraction and
reproduce it exactly for 2014-15 through 2024-25. To keep it that way, the script compares every
year already present in the existing data/caaspp_data.json against the rebuild and refuses to
write anything if a single prior-year value, record, school or subgroup changed (pass --force to
override, e.g. after CDE publishes a genuine correction to an old file).

Rules:
  - A (school, grade, subject, subgroup) record is included if any year has a non-suppressed
    "met and above" percentage. Years with no row or a suppressed row are null.
  - Percentages are round(float, 1); counts are ints; '*' / '' are null.
  - claims: four series (Area 1-4 "above" percentage; Math's Area 4 is 0.0 in the source). A
    record whose claim values are null in every year gets claims = null -- except All Students
    records, which always keep the four (possibly all-null) series.

Format eras (see also verify_source_data.py): 2015-2019 comma-delimited, 2021-2025 caret-
delimited, and 2026 caret-delimited with CDE's renamed achievement levels -- Advanced /
Proficient / Developing / Minimal and "Above Proficient" claim areas -- which are treated as
the same four levels as Standard Exceeded / Met / Nearly Met / Not Met (a label change only).

School names come from the newest source file that has one. Subgroup names and categories are
not in the research files (they come from CDE's student group reference file), so they're carried
forward from the existing JSON; a subgroup id with no known name stops the build.

Usage:
    cd caaspp_ai_dashboard
    python3 build_data.py [--dry-run] [--force]
then run `python3 verify_source_data.py -v`.
"""
import glob
import json
import os
import re
import sys

import verify_source_data as v

TEMPLATE_PREFIX = "const DATA = "
DISTRICT_CODE = "0000000"
DISTRICT_NAME = "District Total (all schools)"
SUBJECTS = ("1", "2")  # ELA, Math
ALL_STUDENTS = "1"
GRADE_ORDER = ["13", "3", "4", "5", "6", "7", "8"]
GRADE_LABELS = {"13": "All Grades", **{g: f"Grade {g}" for g in GRADE_ORDER[1:]}}
SERIES = ["met_above", "exceeded", "met", "nearly", "not_met", "tested"]

# DATA field -> candidate source columns, "Standard" era first, then 2026's "Proficient" labels.
COLUMNS = {
    "met_above": ["Percentage Standard Met and Above", "Percentage Proficient and Above"],
    "exceeded": ["Percentage Standard Exceeded", "Percentage Advanced"],
    "met": ["Percentage Standard Met", "Percentage Proficient"],
    "nearly": ["Percentage Standard Nearly Met", "Percentage Developing"],
    "not_met": ["Percentage Standard Not Met", "Percentage Minimal"],
    "tested": v.TESTED_COLS,
}
CLAIM_COLUMNS = [
    [f"Area {i} Percentage Above Standard", f"Area {i} Percentage Above Proficient"]
    for i in range(1, v.CLAIM_AREAS + 1)
]


def load_years():
    """[(year_label, {key: row})] sorted by year."""
    years = []
    for path in sorted(glob.glob(v.SOURCE_GLOB)):
        m = re.search(r"sb_ca(\d{4})_", os.path.basename(path))
        if not m:
            sys.exit(f"Can't parse a test year from {path}")
        years.append((v.year_label_for(int(m.group(1))), v.load_source_rows(path)))
    return years


def value(row, name, candidates):
    raw = v.field(row, candidates)
    return v.parse_int(raw) if name == "tested" else v.parse_pct(raw)


def build(old):
    years = load_years()
    labels = [label for label, _ in years]
    n = len(labels)

    keys = set()
    for _, rows in years:
        for key, row in rows.items():
            if key[2] in SUBJECTS and key[1] in GRADE_LABELS and value(row, "met_above", COLUMNS["met_above"]) is not None:
                keys.add(key)

    data = {}
    for key in keys:
        rec = {name: [None] * n for name in SERIES}
        claims = [[None] * n for _ in CLAIM_COLUMNS]
        for i, (_, rows) in enumerate(years):
            row = rows.get(key)
            if row is None:
                continue
            for name in SERIES:
                rec[name][i] = value(row, name, COLUMNS[name])
            for c, cols in enumerate(CLAIM_COLUMNS):
                claims[c][i] = v.parse_pct(v.field(row, cols))
        has_claims = key[3] == ALL_STUDENTS or any(x is not None for s in claims for x in s)
        rec["claims"] = claims if has_claims else None
        data["|".join(key)] = rec

    # Subgroups: names/categories only exist in the old JSON (see docstring).
    known = {s["id"]: s for s in old["subgroups"]}
    used = {k.split("|")[3] for k in data}
    unknown = sorted(used - set(known), key=int)
    if unknown:
        sys.exit(f"Subgroup id(s) {unknown} have no name/category; add them to subgroups first.")
    subgroups = [s for s in old["subgroups"] if s["id"] in used]

    # Schools: newest non-empty source name, else the old JSON's name, else "School <code>".
    old_names = {s["code"]: s["name"] for s in old["schools"]}
    names = {}
    for _, rows in years:
        for row in rows.values():
            if row.get("School Name"):
                names[row["School Code"]] = row["School Name"]
    by_school = {}
    for k in data:
        school, grade, _, subgroup = k.split("|")
        g, s = by_school.setdefault(school, (set(), set()))
        g.add(grade)
        s.add(subgroup)
    sub_order = [s["id"] for s in subgroups]
    schools = []
    for code, (g, s) in by_school.items():
        name = DISTRICT_NAME if code == DISTRICT_CODE else names.get(code) or old_names.get(code) or f"School {code}"
        schools.append({
            "code": code,
            "name": name,
            "available_grades": [x for x in GRADE_ORDER if x in g],
            "available_subgroups": [x for x in sub_order if x in s],
        })
    schools.sort(key=lambda s: (s["code"] != DISTRICT_CODE, s["name"]))

    return {
        "years": labels,
        "grades": [{"code": g, "label": GRADE_LABELS[g]} for g in GRADE_ORDER],
        "schools": schools,
        "subgroups": subgroups,
        "data": dict(sorted(data.items())),
    }


def compare_prior_years(old, new):
    """Every year in `old` must be reproduced exactly in `new`. Returns a list of problems."""
    problems = []
    idx = [new["years"].index(y) if y in new["years"] else None for y in old["years"]]
    if None in idx:
        problems.append(f"years missing from rebuild: {[y for y, i in zip(old['years'], idx) if i is None]}")
        return problems
    if old["grades"] != new["grades"]:
        problems.append("grades list changed")
    for s in old["subgroups"]:
        if s not in new["subgroups"]:
            problems.append(f"subgroup {s} missing or changed")
    new_schools = {s["code"]: s for s in new["schools"]}
    for s in old["schools"]:
        ns = new_schools.get(s["code"])
        if ns is None or ns["name"] != s["name"]:
            problems.append(f"school {s['code']} {s['name']!r} missing or renamed")
        elif not set(s["available_grades"]) <= set(ns["available_grades"]) or \
                not set(s["available_subgroups"]) <= set(ns["available_subgroups"]):
            problems.append(f"school {s['code']} lost grades/subgroups")

    def project(rec):
        out = {name: [rec[name][i] for i in idx] for name in SERIES}
        out["claims"] = None if rec["claims"] is None else [[c[i] for i in idx] for c in rec["claims"]]
        return out

    empty = {name: [None] * len(idx) for name in SERIES}
    for k, rec in old["data"].items():
        if k not in new["data"]:
            problems.append(f"record {k} missing from rebuild")
            continue
        got = project(new["data"][k])
        if rec["claims"] is None and got["claims"] is not None and \
                all(x is None for c in got["claims"] for x in c):
            got["claims"] = None  # claims only present because of new-year values
        if got != rec:
            problems.append(f"record {k} changed in a prior year")
    # A record that's new because a new year has its first non-suppressed score may still carry
    # prior-year "tested" counts (CDE publishes counts for suppressed rows; existing records
    # already do this), but never a prior-year score, band or claim value.
    for k in new["data"].keys() - old["data"].keys():
        got = project(new["data"][k])
        got.pop("claims")
        got["tested"] = empty["tested"]
        claims = new["data"][k]["claims"] or []
        if got != empty or any(c[i] is not None for c in claims for i in idx):
            problems.append(f"new record {k} has prior-year values")
    return problems


def main():
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    with open(v.DATA_PATH, encoding="utf-8") as f:
        old = json.load(f)
    new = build(old)

    problems = compare_prior_years(old, new)
    added = [y for y in new["years"] if y not in old["years"]]
    print(f"Years: {', '.join(new['years'])} (new: {', '.join(added) or 'none'})")
    print(f"Records: {len(old['data'])} -> {len(new['data'])}; "
          f"schools: {len(old['schools'])} -> {len(new['schools'])}; "
          f"subgroups: {len(old['subgroups'])} -> {len(new['subgroups'])}")
    if problems:
        print(f"\n{len(problems)} prior-year difference(s) vs the existing JSON:", file=sys.stderr)
        for p in problems[:50]:
            print(" -", p, file=sys.stderr)
        if not force:
            sys.exit("Refusing to write. Re-run with --force if these changes are intended.")
    else:
        print(f"All {len(old['years'])} prior years reproduce the existing JSON exactly.")
    if dry_run:
        return

    with open(v.DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False, separators=(",", ":"))

    with open(v.TEMPLATE_PATH, encoding="utf-8") as f:
        lines = f.readlines()
    hits = [i for i, line in enumerate(lines) if line.startswith(TEMPLATE_PREFIX)]
    if len(hits) != 1:
        sys.exit(f"Expected exactly one '{TEMPLATE_PREFIX}' line in {v.TEMPLATE_PATH}, found {len(hits)}")
    lines[hits[0]] = TEMPLATE_PREFIX + json.dumps(new, ensure_ascii=False) + ";\n"
    with open(v.TEMPLATE_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"Wrote {v.DATA_PATH} and {v.TEMPLATE_PATH}")


if __name__ == "__main__":
    main()
