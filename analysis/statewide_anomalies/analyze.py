"""Step 2. Every number in FINDINGS.md, printed in the same order as its sections.

Usage: python3 analyze.py   (needs work/all.json from extract_all.py; first run builds work/agg.pkl)
"""
import statistics as st
from load import SM, STATE, years, names, get, n, rate, share, participation, dists

Y = years[1:]                     # 2019, 2021-2026 (file year = spring of the school year)
fmt = lambda v: "   - " if v is None else f"{v:5.1f}"
RACES = [("Black", "200", "220"), ("Asian", "202", "222"), ("Filipino", "203", "223"),
         ("Hispanic", "204", "224"), ("Pacific Isl.", "205", "225"), ("White", "206", "226"),
         ("Two+ races", "207", "227")]


def rank(label, vals, high_first=True):
    """Print SMFCSD's value, its rank (1 = highest, or lowest if high_first=False) and the median."""
    vals = {d: v for d, v in vals.items() if v is not None}
    if SM not in vals:
        print(f"{label:66s} SMFCSD missing"); return
    s = sorted(vals.values(), reverse=high_first)
    print(f"{label:66s} SMFCSD {vals[SM]:+7.1f}  rank {s.index(vals[SM]) + 1:>3}/{len(s)}  median {st.median(s):+.1f}")


def safe(f):
    try:
        return f()
    except (TypeError, ZeroDivisionError):
        return None


def head(t):
    print(f"\n{'=' * 100}\n{t}\n{'=' * 100}")


# ---------------------------------------------------------------------------------------------
head("0. Sanity: SMFCSD all-students met% (compare caaspp_ai_dashboard/data/caaspp_data.json)")
for t, lab in (("1", "ELA"), ("2", "Math")):
    print(f"  {lab}", *(f"{y}:{rate(y, SM, '1', t):.1f}" for y in years))
print(f"  Ranked districts with >=300 testers in 2026: {len(dists('2026'))}")

# ---------------------------------------------------------------------------------------------
head("A. Socioeconomically disadvantaged (SED) share - the original reclassification finding")
print("  SMFCSD SED share:", *(f"{y}:{share(y, SM, '31'):.1f}" for y in years))
for y0, y1 in (("2023", "2024"), ("2025", "2026"), ("2023", "2026")):
    ds = [d for d in dists(y0) if n(y1, d)]
    rank(f"  SED share change {y0}->{y1} (largest increase first)",
         {d: share(y1, d, "31") - share(y0, d, "31") for d in ds})

print("\n  'Both groups up, all students down', 2023->2026 (districts with SED and not-SED rates both years):")
for t, lab in (("1", "ELA"), ("2", "Math")):
    elig, hits = [], []
    for d in dists("2023"):
        r = [rate(y, d, sg, t) for y in ("2023", "2026") for sg in ("1", "31", "111")]
        if None in r:
            continue
        a0, s0, ns0, a1, s1, ns1 = r
        elig.append(d)
        if s1 > s0 and ns1 > ns0 and a1 < a0:
            hits.append((a1 - a0, d, s1 - s0, ns1 - ns0))
    hits.sort()
    print(f"  {lab}: {len(hits)} of {len(elig)} districts")
    for c, d, ds_, dn in hits:
        print(f"      {names[d][:38]:38s} all {c:+5.1f}  SED {ds_:+5.1f}  not SED {dn:+5.1f}")

# ---------------------------------------------------------------------------------------------
head("B. Who was reclassified: share of each race counted as SED, SMFCSD")
print(f"  {'':14s}", *(f"{y:>5s}" for y in Y))
for r, s, ns in RACES:
    print(f"  {r:14s}", *(fmt(safe(lambda: 100 * n(y, SM, s) / (n(y, SM, s) + n(y, SM, ns)))) for y in Y))
print("\n  Counts tested (ELA, grades 3-8), SED / not SED:  2023 -> 2024 -> 2026")
for r, s, ns in RACES:
    print(f"  {r:14s} SED {n('2023', SM, s):5.0f} {n('2024', SM, s):5.0f} {n('2026', SM, s):5.0f}"
          f"   not SED {n('2023', SM, ns):5.0f} {n('2024', SM, ns):5.0f} {n('2026', SM, ns):5.0f}")

ds = dists("2026")
for r, s, ns in (RACES[1], RACES[5]):
    for y0, y1 in (("2023", "2024"), ("2023", "2026")):
        v = {}
        for d in ds:
            a0, a1 = n(y0, d, s) + n(y0, d, ns), n(y1, d, s) + n(y1, d, ns)
            if a0 >= 100 and a1 >= 100:
                v[d] = 100 * n(y1, d, s) / a1 - 100 * n(y0, d, s) / a0
        rank(f"  {r} students counted as SED, change {y0}->{y1} (>=100 {r})", v)
v = {d: 100 * (n("2026", d, "224") / n("2023", d, "224") - 1) for d in ds if n("2023", d, "224") >= 100}
rank("  Hispanic not-SED students tested, % change 2023->2026 (steepest drop first)", v, high_first=False)

print("\n  SMFCSD met% by group (ELA):")
print(f"  {'':16s}", *(f"{y:>5s}" for y in Y))
for sg, lab in (("1", "All"), ("31", "SED"), ("111", "Not SED"), ("204", "Hispanic SED"), ("224", "Hispanic notSED"),
                ("202", "Asian SED"), ("222", "Asian notSED"), ("206", "White SED"), ("226", "White notSED")):
    print(f"  {lab:16s}", *(fmt(rate(y, SM, sg)) for y in Y))


def mixdecomp(d, sgs, y0, y1, t):
    """Group met% at y1 if its race mix had stayed at y0's (y1's within-race rates, y0's weights)."""
    rs = [s for s in sgs if rate(y0, d, s, t) is not None and rate(y1, d, s, t) is not None]
    n0 = sum(n(y0, d, s, t) for s in rs); n1 = sum(n(y1, d, s, t) for s in rs)
    a0 = sum(n(y0, d, s, t) * rate(y0, d, s, t) for s in rs) / n0
    a1 = sum(n(y1, d, s, t) * rate(y1, d, s, t) for s in rs) / n1
    fixed = sum(n(y0, d, s, t) * rate(y1, d, s, t) for s in rs) / n0
    return a0, a1, fixed


print("\n  Race-mix decomposition (race-weighted rates; differ slightly from the group total due to suppression):")
for t, lab in (("1", "ELA"), ("2", "Math")):
    for y0, y1 in (("2023", "2024"), ("2023", "2026")):
        a0, a1, fx = mixdecomp(SM, [r[1] for r in RACES], y0, y1, t)
        b0, b1, fb = mixdecomp(SM, [r[2] for r in RACES], y0, y1, t)
        print(f"  {lab} {y0}->{y1}: SED {a0:.1f}->{a1:.1f} ({a1 - a0:+.1f}); with {y0} race mix {fx:.1f} ({fx - a0:+.1f})"
              f"   | not SED {b0:.1f}->{b1:.1f} ({b1 - b0:+.1f}); with {y0} race mix {fb:.1f} ({fb - b0:+.1f})")
for t, lab in (("1", "ELA"), ("2", "Math")):
    v = {d: rate("2024", d, "31", t) - rate("2023", d, "31", t) for d in ds
         if n("2023", d, "31") >= 30 and rate("2023", d, "31", t) is not None and rate("2024", d, "31", t) is not None}
    rank(f"  SED {lab} met% change 2023->2024 (largest gain first)", v)

# ---------------------------------------------------------------------------------------------
head("C. Homeless students (automatically counted as SED)")
print("  SMFCSD homeless share:", *(f"{y}:{fmt(share(y, SM, '52'))}" for y in Y))
print("  SMFCSD homeless tested:", *(f"{y}:{n(y, SM, '52'):.0f}" for y in Y))
hv = {d: share("2026", d, "52") for d in ds}
rank("  Homeless share 2026", hv)
rank("  Homeless share change 2023->2026", {d: share("2026", d, "52") - share("2023", d, "52") for d in ds if n("2023", d)})
low = [d for d in ds if share("2026", d, "31") < 50]
rank(f"  Homeless share 2026, among the {len(low)} districts with SED < 50%", {d: hv[d] for d in low})
for d in sorted(low, key=lambda d: -hv[d])[:10]:
    print(f"      {names[d][:38]:38s} homeless {hv[d]:4.1f}  (2023 {fmt(share('2023', d, '52'))})  SED {share('2026', d, '31'):4.1f}")
print("  SMFCSD homeless met% ELA:", *(f"{y}:{fmt(rate(y, SM, '52'))}" for y in Y))

# ---------------------------------------------------------------------------------------------
head("D/E. Scores for other student groups, 2023->2026 (districts with >=100 tested in the group)")
for sg, lab in (("128", "Disability"), ("6", "Fluent/English only"), ("160", "English learner"), ("52", "Homeless")):
    print(f"  SMFCSD {lab} met%:", *(f"{y}:{fmt(rate(y, SM, sg))}" for y in Y), "(ELA)")
    print(f"  SMFCSD {lab} met%:", *(f"{y}:{fmt(rate(y, SM, sg, '2'))}" for y in Y), "(Math)")
    for t, tl in (("1", "ELA"), ("2", "Math")):
        lv = {d: rate("2026", d, sg, t) for d in ds if n("2026", d, sg, t) >= 100}
        ch = {d: lv[d] - rate("2023", d, sg, t) for d in lv
              if lv[d] is not None and n("2023", d, sg, t) >= 100 and rate("2023", d, sg, t) is not None}
        rank(f"    {lab} {tl} met% 2026", lv)
        rank(f"    {lab} {tl} change 2023->2026", ch)
print("  SMFCSD disability share:", *(f"{y}:{fmt(share(y, SM, '128'))}" for y in Y))

# ---------------------------------------------------------------------------------------------
head("F. Checked and normal (or not unusual)")
print("  SMFCSD ELA participation (tested/enrolled), all / SED / not SED:")
for y in years:
    print(f"    {y}", *(fmt(participation(y, SM, s)) for s in ("1", "31", "111")))
rank("  ELA participation 2026 (lowest first)", {d: participation("2026", d) for d in ds}, high_first=False)
rank("  ELA participation change 2023->2026 (largest drop first)",
     {d: safe(lambda: participation("2026", d) - participation("2023", d)) for d in ds}, high_first=False)
rank("  Grade 3-8 enrollment % change 2019->2026 (largest drop first)",
     {d: safe(lambda: 100 * (get("2026", d)[0] / get("2019", d)[0] - 1)) for d in ds}, high_first=False)
for sg, lab in (("78", "Hispanic"), ("76", "Asian"), ("80", "White"), ("160", "English learner"), ("170", "Ever-EL")):
    print(f"  SMFCSD {lab} share:", *(f"{y}:{fmt(share(y, SM, sg))}" for y in Y))
print("  SMFCSD parent education 'declined to state' share:", *(f"{y}:{fmt(share(y, SM, '121'))}" for y in Y))
d24 = [d for d in dists("2024") if n("2023", d) >= 300]
v = {d: share("2024", d, "121") - share("2023", d, "121") for d in d24}
rank("  Parent-ed 'declined to state' change 2023->2024", v)
print(f"    districts with a jump of >= 15 points: {sum(x >= 15 for x in v.values())}")

print("\n  San Mateo County districts, SED share (2019 2021 2022 2023 2024 2025 2026):")
for d in [d for d in dists("2026", 100) if d.startswith("41|")]:
    print(f"    {names[d][:36]:36s}", *(fmt(share(y, d, "31")) for y in Y))
print(f"    {'STATE':36s}", *(fmt(share(y, STATE, '31')) for y in Y))
