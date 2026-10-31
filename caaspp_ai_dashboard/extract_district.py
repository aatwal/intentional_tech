#!/usr/bin/env python3
"""
Cut one district's rows out of CDE's statewide comprehensive CAASPP research files
(../caaspp_files/california/comprehensive/, ~6.7GB, gitignored) into the same per-district files
CDE publishes (sb_caYYYY_all_<county>_<district>_csv_vN.txt), in the district's own folder
under ../caaspp_files/ (its "files" setting in districts.py; add the district there first).

CDE's own per-district file is exactly the statewide file's header plus that district's rows, byte
for byte (checked against SMFCSD's files for every year), so this script copies lines as raw bytes
and never parses or rewrites them. From 2019 on the files are identical, line order included; for
2015-2018 CDE ordered the district file's lines differently, but every line is the same. The build
reads rows by key, so order doesn't matter. Run with --check to prove that again: it extracts
SMFCSD and compares the result with the committed SMFCSD files.

Usage:
    cd caaspp_ai_dashboard
    python3 extract_district.py 41 68882      # writes ../caaspp_files/burlingame_elementary/*.txt
    python3 extract_district.py --check       # SMFCSD (41 69039) must match its committed files
"""
import glob
import os
import re
import sys
from multiprocessing import Pool

import districts

HERE = os.path.dirname(os.path.abspath(__file__))
STATEWIDE = os.path.join(districts.FILES_DIR, "california", "comprehensive", "sb_ca*_all_csv_v*",
                         "sb_ca*_all_csv_v*.txt")


def extract(args):
    """Header plus every line whose first two fields are the county and district codes."""
    path, county, district = args
    m = re.search(r"sb_ca(\d{4})_all_csv_(v\d+)\.txt$", path)
    prefixes = [f'{county}{d}{district}{d}'.encode() for d in "^,"] + \
               [f'"{county}"{d}"{district}"{d}'.encode() for d in "^,"]
    out = []
    with open(path, "rb") as f:
        out.append(f.readline())
        for line in f:
            if line.startswith(tuple(prefixes)):
                out.append(line)
    return m.group(1), m.group(2), b"".join(out)


def rows(data):
    return data.count(b"\n") - 1  # minus the header


def run(county, district):
    paths = sorted(glob.glob(STATEWIDE))
    if not paths:
        sys.exit(f"No statewide files matched {STATEWIDE}")
    with Pool(min(6, len(paths))) as pool:
        return pool.map(extract, [(p, county, district) for p in paths])


def main():
    if sys.argv[1:] == ["--check"]:
        bad = 0
        smfc = districts.files_dir(districts.DISTRICTS["smfcsd"])
        for year, ver, data in run("41", "69039"):
            committed = os.path.join(smfc, f"sb_ca{year}_all_41_69039_csv_{ver}.txt")
            theirs = open(committed, "rb").read() if os.path.exists(committed) else b""
            if theirs == data:
                result = "identical"
            elif sorted(theirs.splitlines(True)) == sorted(data.splitlines(True)):
                result = "same lines, different order"
            else:
                result, bad = "DIFFERENT", bad + 1
            print(f"{year}: {result} ({rows(data)} rows)")
        sys.exit(1 if bad else 0)
    if len(sys.argv) != 3 or not all(re.fullmatch(r"\d+", a) for a in sys.argv[1:]):
        sys.exit(__doc__)
    county, district = sys.argv[1:]
    slug, d = districts.by_codes(county, district)
    if d is None:
        sys.exit(f"Add county {county} district {district} to districts.py first; its \"files\" "
                 "setting names the folder these go in.")
    folder = districts.files_dir(d)
    os.makedirs(folder, exist_ok=True)
    for year, ver, data in run(county, district):
        n = rows(data)
        if n == 0:
            print(f"{year}: no rows for {county} {district}, nothing written")
            continue
        out = os.path.join(folder, f"sb_ca{year}_all_{county}_{district}_csv_{ver}.txt")
        with open(out, "wb") as f:
            f.write(data)
        print(f"{year}: {n} rows -> {os.path.relpath(out, HERE)}")


if __name__ == "__main__":
    main()
