#!/usr/bin/env python3
"""
Cross-validate data/statewide.json (the "Statewide comparison" tab's data, built by
build_statewide.py) against CDE's raw statewide research files in ../caaspp_files/california/
and against this dashboard's own SMFCSD data in data/caaspp_data.json.

Deliberately independent of build_statewide.py: it re-reads and re-parses the raw files with
its own code, so a bug in the build can't also hide itself here. Run it after every
`python3 build_statewide.py`. The raw statewide files are gitignored (~224MB), so this only
runs where they've been downloaded.

Checks (each PASS/FAIL; exit status is non-zero if any FAIL):
  1. Years: statewide.json covers exactly the years in caaspp_data.json, one raw folder each.
  2. SMFCSD grade-level rows in the statewide files (grades 3-8 and all grades, ELA and Math,
     every year) equal the Dashboard tab's All Students values exactly.
  3. SMFCSD's grades 3-8 combined figure (what the Statewide tab shows) matches the Dashboard
     tab's All Grades figure: within 0.1 pts from 2016-17 on, within 1.0 pts for 2014-15 and
     2015-16 (CDE published whole-number percentages those years, so combining grades loses
     precision). SMFCSD is K-8, so the two should agree.
  4. Every state, county, and district value and students-with-scores count in statewide.json
     equals an independent recompute from the raw files.
  5. For K-8 districts with no suppressed grade, the grades 3-8 calculation matches CDE's own
     all-grades figure within 0.1 pts for at least 90% of district-years (2016-17 on). This
     checks the weighting method itself, not just that it ran consistently. Unified districts
     whose grade 11 rows are suppressed still count those students in CDE's all-grades figure,
     so a small share of differences is expected.
  6. Ranking exclusions: every county office of education (district code 10xxx) is flagged
     kind "coe", every State Board charter ("SBE - ", any capitalization) is flagged "sbe", and
     no unflagged entity's name looks like either.

Usage:
    cd caaspp_ai_dashboard
    python3 verify_statewide_data.py [-v]
"""
import collections
import csv
import glob
import json
import os
import re
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_DIR = os.path.join(APP_DIR, "..", "caaspp_files", "california")
STATEWIDE_PATH = os.path.join(APP_DIR, "data", "statewide.json")
DISTRICT_PATH = os.path.join(APP_DIR, "data", "caaspp_data.json")

SUBJECTS = {"1": "ela", "2": "math"}
GRADES = ("3", "4", "5", "6", "7", "8")
WHOLE_NUMBER_YEARS = ("2014–15", "2015–16")

MET_ABOVE = ("Percentage Standard Met and Above", "Percentage Proficient and Above")
SCORED = ("Total Students Tested with Scores", "Students with Scores")
TEST_ID = ("Test ID", "Test Id")


def read_rows(path):
    raw = open(path, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return list(csv.DictReader(text.splitlines(), delimiter="," if text.startswith('"') else "^"))


def col(row, names):
    for n in names:
        if n in row:
            return row[n]
    raise KeyError(f"none of {names} in row")


def num(raw):
    raw = (raw or "").strip()
    return None if raw in ("", "*") else float(raw)


def year_label(test_year):
    return f"{test_year - 1}–{str(test_year)[-2:]}"


def combine(rows, key):
    """{(key, subject): (weighted grade 3-8 met-and-above, students with scores)}."""
    acc = collections.defaultdict(lambda: [0.0, 0])
    for r in rows:
        if r["Grade"] not in GRADES:
            continue
        subj = SUBJECTS.get(col(r, TEST_ID))
        pct, scored = num(col(r, MET_ABOVE)), num(col(r, SCORED))
        if subj and pct is not None and scored:
            a = acc[(key(r), subj)]
            a[0] += pct * scored
            a[1] += int(scored)
    return {k: (round(w / n, 1), n) for k, (w, n) in acc.items()}


def main():
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    if not os.path.isdir(SOURCE_DIR):
        print(f"No statewide source files at {SOURCE_DIR} (they're gitignored; download CDE's "
              "statewide All Students research files there first).", file=sys.stderr)
        sys.exit(2)

    sw = json.load(open(STATEWIDE_PATH, encoding="utf-8"))
    dd = json.load(open(DISTRICT_PATH, encoding="utf-8"))
    years = sw["years"]
    results = []

    def report(name, ok, detail, problems=()):
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        for p in list(problems)[: (len(problems) if verbose else 10)]:
            print(f"    - {p}")
        if not verbose and len(problems) > 10:
            print(f"    ... and {len(problems) - 10} more (run with -v for all)")

    # Raw district/county/state-level rows, per year index
    folders = sorted(glob.glob(os.path.join(SOURCE_DIR, "sb_ca*_1_csv_v*")))
    folder_years = [year_label(int(re.search(r"sb_ca(\d{4})_", os.path.basename(f)).group(1))) for f in folders]

    # 1. Years line up
    ok = folder_years == years == dd["years"]
    report("Years", ok, f"statewide.json {len(years)}, caaspp_data.json {len(dd['years'])}, raw folders {len(folder_years)}",
           [] if ok else [f"statewide.json={years}", f"caaspp_data.json={dd['years']}", f"raw folders={folder_years}"])
    if not ok:
        sys.exit(1)

    raw = {}
    for i, folder in enumerate(folders):
        scores = glob.glob(os.path.join(folder, "sb_ca*_1_csv_v*.txt"))[0]
        raw[i] = [r for r in read_rows(scores) if r["School Code"] == "0000000"]
        if verbose:
            print(f"    read {os.path.basename(scores)} ({len(raw[i]):,} district/county/state rows)")

    smfcsd_cc, smfcsd_dc = sw["smfcsd"].split("|")
    smfcsd = next(d for d in sw["districts"] if d["id"] == sw["smfcsd"])

    # 2. SMFCSD grade-level rows vs the Dashboard tab
    problems, n = [], 0
    for i, rows in raw.items():
        for r in rows:
            if (r["County Code"], r["District Code"]) != (smfcsd_cc, smfcsd_dc) or r["Grade"] not in GRADES + ("13",):
                continue
            t = col(r, TEST_ID)
            key = f"0000000|{r['Grade']}|{t}|1"
            dash = dd["data"][key]["met_above"][i]
            v = num(col(r, MET_ABOVE))
            v = None if v is None else round(v, 1)
            n += 1
            if v != dash:
                problems.append(f"{years[i]} {key}: statewide file {v}, Dashboard tab {dash}")
    report("SMFCSD grade-level values vs Dashboard tab", not problems and n > 0, f"{n} values, {len(problems)} mismatches", problems)

    # 3. SMFCSD combined (Statewide tab) vs All Grades (Dashboard tab)
    problems, worst = [], 0.0
    for t, s in SUBJECTS.items():
        dash = dd["data"][f"0000000|13|{t}|1"]["met_above"]
        for i, y in enumerate(years):
            a, b = smfcsd[s][i], dash[i]
            if a is None or b is None:
                if a != b:
                    problems.append(f"{y} {s}: Statewide tab {a}, Dashboard tab {b}")
                continue
            diff = abs(a - b)
            worst = max(worst, diff)
            if diff > (1.0 if y in WHOLE_NUMBER_YEARS else 0.1) + 1e-9:
                problems.append(f"{y} {s}: Statewide tab {a}, Dashboard tab {b} (diff {diff:.1f})")
    report("SMFCSD Statewide-tab figure vs Dashboard-tab All Grades", not problems,
           f"largest difference {worst:.1f} pts", problems)

    # 4. Independent recompute of every value
    problems, n = [], 0
    for i, rows in raw.items():
        state = combine([r for r in rows if r["County Code"] == "00"], lambda r: "state")
        counties = combine([r for r in rows if r["County Code"] != "00" and r["District Code"] == "00000"],
                           lambda r: r["County Code"])
        districts = combine([r for r in rows if r["District Code"] != "00000"],
                            lambda r: f"{r['County Code']}|{r['District Code']}")
        for s in SUBJECTS.values():
            checks = [("California", (sw["state"][s][i], sw["state"][s + "_n"][i]), state.get(("state", s), (None, None)))]
            checks += [(c["name"], (c[s][i], c[s + "_n"][i]), counties.get((c["code"], s), (None, None))) for c in sw["counties"]]
            checks += [(d["name"], (d[s][i], d[s + "_n"][i]), districts.get((d["id"], s), (None, None))) for d in sw["districts"]]
            for name, got, expected in checks:
                n += 1
                if got != expected:
                    problems.append(f"{years[i]} {s} {name}: statewide.json {got}, recomputed {expected}")
    report("Independent recompute of state/county/district values", not problems, f"{n:,} checked, {len(problems)} mismatches", problems)

    # 5. Weighting method vs CDE's own all-grades figure, K-8 districts
    diffs = []
    for i, rows in raw.items():
        if years[i] in WHOLE_NUMBER_YEARS:
            continue
        has_grade_11 = {(r["County Code"], r["District Code"]) for r in rows
                        if r["Grade"] == "11" and num(col(r, MET_ABOVE)) is not None}
        published, suppressed = {}, set()
        for r in rows:
            subj = SUBJECTS.get(col(r, TEST_ID))
            if not subj or r["District Code"] == "00000":
                continue
            k = (f"{r['County Code']}|{r['District Code']}", subj)
            if r["Grade"] == "13":
                published[k] = num(col(r, MET_ABOVE))
            elif r["Grade"] in GRADES and num(col(r, MET_ABOVE)) is None:
                suppressed.add(k)
        for d in sw["districts"]:
            if tuple(d["id"].split("|")) in has_grade_11:
                continue
            for s in SUBJECTS.values():
                p = published.get((d["id"], s))
                if d[s][i] is None or p is None or (d["id"], s) in suppressed:
                    continue
                diffs.append((abs(d[s][i] - round(p, 1)), years[i], d["name"], s, d[s][i], p))
    within = sum(1 for x in diffs if x[0] <= 0.1 + 1e-9) / len(diffs) if diffs else 0
    exact = sum(1 for x in diffs if x[0] == 0) / len(diffs) if diffs else 0
    largest = [f"{y} {name} {s}: grades 3-8 {mine}, CDE all grades {pub}"
               for _, y, name, s, mine, pub in sorted(diffs, reverse=True)[:5]]
    report("Grades 3-8 method vs CDE all-grades (K-8 districts, 2016-17 on)", within >= 0.90,
           f"{len(diffs):,} comparisons, {exact:.1%} exact, {within:.1%} within 0.1 (need 90%)",
           largest if verbose or within < 0.90 else [])

    # 6. Ranking exclusions
    problems = []
    for d in sw["districts"]:
        dc, kind, name = d["id"].split("|")[1], d.get("kind"), d["name"]
        if re.fullmatch(r"10\d{3}", dc) and kind != "coe":
            problems.append(f"{name} ({d['id']}): district code {dc} is a county office but kind={kind}")
        if name.upper().startswith("SBE - ") and kind != "sbe":
            problems.append(f"{name} ({d['id']}): State Board charter but kind={kind}")
        if not kind and re.search(r"office of educ|department of educ|superintendent of school", name, re.I):
            problems.append(f"{name} ({d['id']}): looks like a county office but isn't flagged")
    kinds = collections.Counter(d.get("kind") or "district" for d in sw["districts"])
    report("Ranking exclusions (county offices, State Board charters)", not problems,
           f"{kinds['district']} ranked-eligible districts, {kinds['coe']} county offices, {kinds['sbe']} State Board charters",
           problems)

    # Context, not a check: SMFCSD's latest standing as the tab computes it
    yi = len(years) - 1
    for s in SUBJECTS.values():
        pool = [d for d in sw["districts"] if not d.get("kind") and d[s][yi] is not None]
        v = smfcsd[s][yi]
        if v is not None and len(pool) > 1:
            rank = 1 + sum(d[s][yi] > v for d in pool)
            below = sum(d[s][yi] < v for d in pool)
            print(f"       SMFCSD {s.upper()} {years[yi]}: {v}%, rank #{rank} of {len(pool)}, higher than {below / (len(pool) - 1):.0%}")

    failed = results.count(False)
    print(f"\n{len(results) - failed} of {len(results)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
