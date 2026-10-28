"""Step 1. Pull district-level rows out of CDE's comprehensive statewide CAASPP research files.

Keeps every district-level row (School Code 0000000) for grades 3-8 and 13 (all grades), every
student group, ELA (Test ID 1) and Math (Test ID 2), for 2018 onward.

Out (work/all.json, ~230MB, gitignored):
    {year: {"d": {"cc|dc|test|sg|grade": [enrolled, tested, scored, pct_met_above, mean_scale_score]},
            "names": {"cc|dc": name}}}

Column names differ by era (2018-19 / 2021-23 / 2024+), so they're resolved by name per file.

Usage (~20s, 6 processes):
    python3 extract_all.py [path/to/caaspp_files/california/comprehensive]
"""
import csv, glob, json, os, re, sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "caaspp_files", "california", "comprehensive")
WORK = os.path.join(HERE, "work")
GR = {"3", "4", "5", "6", "7", "8", "13"}


def col(h, *names):
    for n in names:
        if n in h:
            return h.index(n)
    raise KeyError(names)


def num(s):
    s = s.strip().strip('"')
    if s in ("", "*"):
        return None
    return float(s)


def one(folder):
    y = int(re.search(r"sb_ca(\d{4})", folder).group(1))
    path = glob.glob(os.path.join(folder, "sb_ca*_all_csv_v*.txt"))[0]
    raw = open(path, "rb")
    first = raw.readline().decode("latin-1")
    delim = "," if first.startswith('"') else "^"
    h = next(csv.reader([first.strip()], delimiter=delim))
    ix = dict(cc=col(h, "County Code"), dc=col(h, "District Code"), sc=col(h, "School Code"),
              sg=col(h, "Subgroup ID", "Student Group ID"), gr=col(h, "Grade"), t=col(h, "Test Id", "Test ID"),
              e=col(h, "Total Students Enrolled", "Students Enrolled", "CAASPP Reported Enrollment"),
              tt=col(h, "Total Students Tested", "Students Tested"),
              n=col(h, "Total Students Tested with Scores", "Students with Scores", "Total Tested with Scores"),
              p=col(h, "Percentage Standard Met and Above", "Percentage Proficient and Above"),
              m=col(h, "Mean Scale Score"))
    tag = '"0000000"' if delim == "," else "0000000"
    out = {}
    for bline in raw:
        line = bline.decode("latin-1")
        if tag not in line:
            continue
        r = next(csv.reader([line.rstrip("\r\n")], delimiter=delim))
        if r[ix["sc"]] != "0000000" or r[ix["gr"]] not in GR:
            continue
        t = r[ix["t"]].strip('"')
        if t not in ("1", "2"):
            continue
        k = f'{r[ix["cc"]]}|{r[ix["dc"]]}|{t}|{r[ix["sg"]]}|{r[ix["gr"]]}'
        out[k] = [num(r[ix[c]]) for c in ("e", "tt", "n", "p", "m")]
    names = {}
    ent = glob.glob(os.path.join(folder, "*entities*"))[0]
    eb = open(ent, "rb").read()
    try:
        et = eb.decode("utf-8")
    except UnicodeDecodeError:
        et = eb.decode("latin-1")
    for e in csv.DictReader(et.splitlines(), delimiter="," if et.startswith('"') else "^"):
        if e.get("School Code") == "0000000":
            names[f'{e["County Code"]}|{e["District Code"]}'] = \
                (e.get("District Name") or "").strip() or (e.get("County Name") or "").strip()
    print(y, [h[ix[c]] for c in ("e", "tt", "n")], len(out), file=sys.stderr)
    return y, out, names


if __name__ == "__main__":
    folders = [f for f in sorted(glob.glob(os.path.join(SRC, "sb_ca*_all_csv_v*")))
               if int(re.search(r"sb_ca(\d{4})", f).group(1)) >= 2018]
    if not folders:
        sys.exit(f"No sb_caYYYY_all_csv_v* folders in {SRC}")
    with Pool(6) as pool:
        res = pool.map(one, folders)
    os.makedirs(WORK, exist_ok=True)
    json.dump({str(y): {"d": o, "names": nm} for y, o, nm in res}, open(os.path.join(WORK, "all.json"), "w"))
    for f in ("agg.pkl",):  # stale cache from an older extraction
        p = os.path.join(WORK, f)
        if os.path.exists(p):
            os.remove(p)
