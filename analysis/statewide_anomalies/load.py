"""Step 2 helper. Aggregates work/all.json to grades 3-8 per (district, test, student group), cached
in work/agg.pkl. Imported by analyze.py.

Conventions (same as the published pages):
  - Grades 3-8 combined; percent met-or-above is weighted by students with scores.
  - Ranked districts: >=300 grade 3-8 ELA testers, excluding county offices of education
    (district codes 10xxx), State Board charters ("SBE - ..."), and county/state roll-ups.
  - Group "share" = group's students with scores / all students with scores (ELA, grades 3-8).
    CDE suppresses groups under 11 students per grade, so small shares can read as 0.
"""
import json, os, pickle
from collections import defaultdict

WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work")
SM = "41|69039"      # San Mateo-Foster City
STATE = "00|00000"


def _build():
    X = json.load(open(os.path.join(WORK, "all.json")))
    years = sorted(X, key=int)
    names = {}
    for y in years:
        names.update(X[y]["names"])
    agg, grade = {}, {}
    for y in years:
        a = defaultdict(lambda: [0, 0, 0, 0.0, 0])   # enrolled, tested, scored, sum(p*n), n with p
        g = {}
        for k, (e, t, n, p, m) in X[y]["d"].items():
            cc, dc, test, sg, gr = k.split("|")
            d = f"{cc}|{dc}"
            if gr == "13":
                continue
            g[(d, test, sg, gr)] = (e, t, n, p, m)
            r = a[(d, test, sg)]
            r[0] += e or 0; r[1] += t or 0; r[2] += n or 0
            if p is not None and n:
                r[3] += p * n; r[4] += n
        agg[y] = dict(a); grade[y] = g
    return years, names, agg, grade


_cache = os.path.join(WORK, "agg.pkl")
if os.path.exists(_cache):
    years, names, agg, grade = pickle.load(open(_cache, "rb"))
else:
    years, names, agg, grade = _build()
    pickle.dump((years, names, agg, grade), open(_cache, "wb"))


def get(y, d, sg="1", t="1"):
    return agg[y].get((d, t, sg))


def n(y, d, sg="1", t="1"):
    r = get(y, d, sg, t)
    return r[2] if r else 0


def rate(y, d, sg="1", t="1"):
    r = get(y, d, sg, t)
    return r[3] / r[4] if r and r[4] else None


def share(y, d, sg):
    a = n(y, d)
    return 100 * n(y, d, sg) / a if a else None


def participation(y, d, sg="1", t="1"):
    r = get(y, d, sg, t)
    return 100 * r[1] / r[0] if r and r[0] else None


def dists(y, minn=300):
    return sorted(k[0] for k in agg[y]
                  if k[1] == "1" and k[2] == "1" and not k[0].endswith("|00000")
                  and not k[0].split("|")[1].startswith("10")
                  and not names.get(k[0], "").upper().startswith("SBE")
                  and agg[y][k][2] >= minn)
