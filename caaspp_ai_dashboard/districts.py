"""
The districts this app can show, each at its own path: SMFCSD (the main dashboard) at /smfc,
which / forwards to; the others are linked from nowhere and marked noindex.

Each district's data is built by `python3 build_data.py --district <slug>` from CDE's
per-district research files in its own folder, ../caaspp_files/<files>/ (named
sb_caYYYY_all_<county>_<code>_csv_vN.txt, as CDE names them). For a district other than SMFCSD
those files are cut out of the statewide comprehensive files with
`python3 extract_district.py <county> <code>` -- CDE's own district files are the same rows.

To add a district: add an entry here, extract its files, build its data, and verify it with
`python3 verify_source_data.py --district <slug>`.
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))

DISTRICTS = {
    "smfcsd": {
        "path": "/smfc",
        "county": "41",
        "code": "69039",
        "files": "smfc",                   # ../caaspp_files/smfc/
        "name": "San Mateo-Foster City School District",
        "place": "San Mateo-Foster City",  # page title and source notes
        "short": "SMFCSD",                 # in headings: "Where SMFCSD falls among..."
        "county_name": "San Mateo County",
        "org": "SMFCSD",                   # footer: "not an official <org> analysis"
        "data": os.path.join(APP_DIR, "data", "caaspp_data.json"),
        "listed": True,                    # linked from the site, indexed by search engines
        "writeups": True,                  # links the SMFCSD-only reclassification write-up
    },
    "burlingame": {
        "path": "/burlingame",
        "county": "41",
        "code": "68882",
        "files": "burlingame_elementary",
        "name": "Burlingame Elementary School District",
        "place": "Burlingame Elementary",
        "short": "Burlingame",
        "county_name": "San Mateo County",
        "org": "Burlingame Elementary School District",
        "data": os.path.join(APP_DIR, "data", "caaspp_data_burlingame.json"),
        "listed": False,
        "writeups": False,
    },
    "newark": {
        "path": "/newark",
        "county": "01",
        "code": "61234",
        "files": "newark_unified",
        "name": "Newark Unified School District",
        "place": "Newark Unified",
        "short": "Newark Unified",
        "county_name": "Alameda County",
        "org": "Newark Unified School District",
        "data": os.path.join(APP_DIR, "data", "caaspp_data_newark.json"),
        "listed": False,
        "writeups": False,
    },
}
DEFAULT = "smfcsd"


FILES_DIR = os.path.join(APP_DIR, "..", "caaspp_files")


def files_dir(d):
    return os.path.join(FILES_DIR, d["files"])


def source_glob(d):
    return os.path.join(files_dir(d), f"sb_ca*_all_{d['county']}_{d['code']}_csv_v*.txt")


def by_codes(county, code):
    """The (slug, district) with these CDE codes, or (None, None)."""
    for slug, d in DISTRICTS.items():
        if (d["county"], d["code"]) == (county, code):
            return slug, d
    return None, None


def from_argv(argv=None):
    """The --district <slug> option shared by build_data.py and verify_source_data.py."""
    argv = sys.argv if argv is None else argv
    if "--district" not in argv:
        return DEFAULT, DISTRICTS[DEFAULT]
    i = argv.index("--district")
    slug = argv[i + 1] if i + 1 < len(argv) else ""
    if slug not in DISTRICTS:
        sys.exit(f"--district must be one of: {', '.join(DISTRICTS)}")
    return slug, DISTRICTS[slug]
