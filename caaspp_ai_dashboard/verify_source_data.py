#!/usr/bin/env python3
"""
Cross-validate data/caaspp_data.json against the raw CDE CAASPP research-file exports in
../caaspp_files/. Those raw files are the actual source of truth for "did we pull and interpret
the CAASPP data correctly" -- nothing else in this repo reads them at runtime, so this is
currently the only way to check the derived data traces back to CDE's published figures rather
than a transcription error from whenever the original extraction happened.

Also checks that templates/index.html's own embedded copy of DATA (used for its client-side
charts -- see CLAUDE.md's data-sync note) is byte-for-byte identical to data/caaspp_data.json,
since nothing enforces that automatically and the two dashboards would silently disagree
otherwise.

Checks, per (school, grade, subject, student-group, year) combination found in DATA:
  - percent meeting/exceeding standard ("met_above") matches the source row's "Percentage
    Standard Met and Above", rounded to 1 decimal the same way the extraction apparently did
  - the four achievement-band percentages (exceeded/met/nearly/not_met) match the source row's
    "Percentage Standard Exceeded/Met/Nearly Met/Not Met" -- these back the "all four achievement
    bands" chart, a separate code path from met_above
  - "tested" matches the source row's tested-count column exactly
  - each claim-area percentage (rec["claims"][i]) matches the source row's "Area i+1 Percentage
    Above Standard"
  - suppression agrees in both directions: DATA shouldn't show a value where the source row is
    suppressed ('*'), and DATA shouldn't show null where the source row has a real value

2019-20 has no file (CAASPP testing was suspended statewide for COVID-19) and is skipped, same
as everywhere else in this codebase.

CDE changed this research-file format twice across 2015-2025, so column names and the delimiter
both vary by year:
  - 2015-2019: comma-delimited, quoted, "Test Id" / "Subgroup ID" / "Students Tested"
  - 2021-2023: caret-delimited, "Test ID" / "Student Group ID" / "Students Tested"
  - 2024-2025: caret-delimited, "Test ID" / "Student Group ID" / "Total Students Tested"
"Percentage Standard Met and Above", "Area N Percentage Above Standard", "School Code", and
"Grade" are spelled identically across all three eras. Delimiter and column names are resolved
per-file below rather than assumed, since guessing wrong here would silently produce a validator
that's confidently checking the wrong columns.

Usage:
    cd caaspp_ai_dashboard
    python3 verify_source_data.py [-v]

Exit status is non-zero if any mismatch is found.
"""
import csv
import glob
import json
import os
import re
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(APP_DIR, "data", "caaspp_data.json")
TEMPLATE_PATH = os.path.join(APP_DIR, "templates", "index.html")
SOURCE_DIR = os.path.join(APP_DIR, "..", "caaspp_files")
SOURCE_GLOB = os.path.join(SOURCE_DIR, "sb_ca*_all_41_69039_csv_v*.txt")

CLAIM_AREAS = 4  # DATA always stores 4 claim series even for Math (3 real + 1 zero-filled)

# DATA field name -> source column name, for the four achievement bands (spelled identically
# across all three format eras, unlike Test ID/Student Group ID/tested above).
BAND_FIELDS = {
    "exceeded": "Percentage Standard Exceeded",
    "met": "Percentage Standard Met",
    "nearly": "Percentage Standard Nearly Met",
    "not_met": "Percentage Standard Not Met",
}


def year_label_for(test_year):
    """CDE 'Test Year' (e.g. 2025, meaning spring 2025) -> the dashboard's year label
    (e.g. '2024–25'), matching the convention already used throughout this repo."""
    return f"{test_year - 1}–{str(test_year)[-2:]}"


def parse_pct(raw):
    return None if raw in ("*", "") else round(float(raw), 1)


def parse_int(raw):
    return None if raw in ("*", "") else int(raw)


# Column name candidates that vary across the format eras (see module docstring). Tried in
# order; the first one present in a given file's header wins.
TEST_ID_COLS = ["Test ID", "Test Id"]
SUBGROUP_ID_COLS = ["Student Group ID", "Subgroup ID"]
TESTED_COLS = ["Total Students Tested", "Students Tested"]


def field(row, candidates):
    for name in candidates:
        if name in row:
            return row[name]
    raise KeyError(f"none of {candidates} found in row (columns: {list(row.keys())})")


def sniff_delimiter(path):
    with open(path, encoding="utf-8") as f:
        first_line = f.readline()
    return "," if first_line.startswith('"') else "^"


def load_source_rows(path):
    """{(school_code, grade, test_id, subgroup_id): row_dict}, warning on any duplicate key
    (would silently overwrite otherwise -- the same class of unverified-uniqueness bug already
    found once in app.py's subgroup reverse map)."""
    rows = {}
    dupes = 0
    delimiter = sniff_delimiter(path)
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=delimiter):
            key = (
                r["School Code"],
                r["Grade"],
                field(r, TEST_ID_COLS),
                field(r, SUBGROUP_ID_COLS),
            )
            if key in rows:
                dupes += 1
            rows[key] = r
    if dupes:
        print(f"  WARNING: {dupes} duplicate (school, grade, subject, subgroup) rows in "
              f"{os.path.basename(path)} -- later rows silently won", file=sys.stderr)
    return rows


def check_file(path, data_records, year_index, mismatches, checked_counter):
    source_rows = load_source_rows(path)

    for rec_key, rec in data_records.items():
        school_code, grade, test_id, subgroup_id = rec_key.split("|")
        source_row = source_rows.get((school_code, grade, test_id, subgroup_id))
        data_met_above = rec["met_above"][year_index]

        if source_row is None:
            if data_met_above is not None:
                mismatches.append(
                    f"{rec_key}: DATA has met_above={data_met_above} but no matching row in "
                    f"{os.path.basename(path)}"
                )
            continue

        checked_counter[0] += 1
        src_met_above = parse_pct(source_row["Percentage Standard Met and Above"])
        if src_met_above != data_met_above:
            mismatches.append(f"{rec_key}: met_above DATA={data_met_above} source={src_met_above}")

        data_tested = rec["tested"][year_index]
        src_tested = parse_int(field(source_row, TESTED_COLS))
        if src_tested != data_tested:
            mismatches.append(f"{rec_key}: tested DATA={data_tested} source={src_tested}")

        for band_field, source_col in BAND_FIELDS.items():
            data_band = rec[band_field][year_index]
            src_band = parse_pct(source_row[source_col])
            if src_band != data_band:
                mismatches.append(
                    f"{rec_key}: {band_field} DATA={data_band} source={src_band}"
                )

        claims = rec.get("claims")
        if claims:
            for i in range(CLAIM_AREAS):
                data_claim = claims[i][year_index] if i < len(claims) else None
                src_claim = parse_pct(source_row[f"Area {i + 1} Percentage Above Standard"])
                if src_claim != data_claim:
                    mismatches.append(
                        f"{rec_key}: claim[{i}] DATA={data_claim} source={src_claim}"
                    )


def check_template_sync():
    """templates/index.html carries its own copy of the same DATA blob for its client-side
    charts (see CLAUDE.md's data-sync note) -- nothing enforces the two stay identical, so a
    dashboard could be showing wrong numbers even with a perfect data/caaspp_data.json."""
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        for line in f:
            if line.startswith("const DATA = "):
                raw = line[len("const DATA = "):].rstrip("\n").rstrip(";")
                return json.loads(raw)
    raise RuntimeError(f"couldn't find 'const DATA = ' in {TEMPLATE_PATH}")


def main():
    verbose = "-v" in sys.argv or "--verbose" in sys.argv

    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    template_data = check_template_sync()
    if template_data != data:
        print(
            f"MISMATCH: {TEMPLATE_PATH}'s embedded DATA blob does not match {DATA_PATH}. "
            "One of the two dashboards is showing different numbers than the other.",
            file=sys.stderr,
        )
        sys.exit(1)
    if verbose:
        print(f"{TEMPLATE_PATH}'s embedded DATA blob matches {DATA_PATH}.")
    years = data["years"]
    year_index_by_label = {label: i for i, label in enumerate(years)}

    files = sorted(glob.glob(SOURCE_GLOB))
    if not files:
        print(f"No source files matched {SOURCE_GLOB}", file=sys.stderr)
        sys.exit(2)

    mismatches = []
    checked = [0]
    for path in files:
        m = re.search(r"sb_ca(\d{4})_", os.path.basename(path))
        if not m:
            print(f"Skipping {path}: couldn't parse a test year from the filename", file=sys.stderr)
            continue
        test_year = int(m.group(1))
        label = year_label_for(test_year)
        if label not in year_index_by_label:
            print(f"Skipping {path}: year label {label!r} not in DATA['years']", file=sys.stderr)
            continue
        if verbose:
            print(f"Checking {os.path.basename(path)} against year {label}...")
        check_file(path, data["data"], year_index_by_label[label], mismatches, checked)

    print(f"\nChecked {checked[0]} (record, year) combinations across {len(files)} source files.")
    if mismatches:
        print(f"\n{len(mismatches)} MISMATCH(ES):\n")
        for m in mismatches[:200]:
            print(" -", m)
        if len(mismatches) > 200:
            print(f"  ... and {len(mismatches) - 200} more")
        sys.exit(1)

    print("All checked values match the source CDE files.")


if __name__ == "__main__":
    main()
