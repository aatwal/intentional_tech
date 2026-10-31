# CAASPP Dashboard: AI Chat Edition

Same 11-year CAASPP dashboard as `../smfc_caaspp_dashboard.html`
(identical charts and filters), plus a chat panel for asking questions about
the currently selected data. This variant needs a small Python server; it is
**not** a drop-in replacement for the static file, which stays a standalone,
open-in-any-browser page.

## Why a separate app

The static dashboard is a single self-contained HTML file with no server and
no build step, by design. A chat feature needs to call the Claude API, and
that call has to happen somewhere that can hold an API key safely, and a
browser tab can't keep a secret. So this version runs as a tiny Flask app: it serves
the same page, and adds a `/api/chat` endpoint that holds the key server-side
and proxies chat requests to Claude.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python app.py
```

Then open <http://localhost:8060>.

## How the chat is grounded

The full 11-year dataset (`data/caaspp_data.json`, extracted from the same
source as the static dashboard) is loaded once at startup. On each chat turn,
the server does **not** send the whole dataset to Claude: that's ~4.5MB of
JSON, too large and too expensive per message. Instead it builds a compact
plain-text summary scoped to whatever subject/school/grade/student-group is
currently selected in the dashboard (the same numbers the charts are already
showing, plus a cross-school ranking for context) and sends only that summary
as the system prompt. This keeps answers grounded in real numbers, keeps
requests small, and means the chat's scope follows the filters: asking about
a different school/grade means changing the filter first.

## Regenerating the data

`build_data.py` rebuilds DATA from the raw CDE research files in `../caaspp_files/smfc/` and writes
`data/caaspp_data.json`, which `app.py` puts into the page at startup. For another district in
`districts.py`, add `--district <slug>` to both commands below. After adding a new year's file there:

```bash
python3 build_data.py --dry-run   # report what would change
python3 build_data.py             # write data/caaspp_data.json
python3 verify_source_data.py -v  # cross-check every value against the source files
```

`build_data.py` refuses to write if any year already in the existing JSON would come out
different (pass `--force` only for a deliberate correction, e.g. CDE re-issuing an old file).
Subgroup names/categories aren't in the research files, so a new subgroup id needs a name added
to `subgroups` in the JSON before it will build.

## Statewide comparison data

The "Statewide comparison" tab reads `data/statewide.json`, built from CDE's statewide
All-Students research files (one `sb_caYYYY_1_csv_vN/` folder per year in
`../caaspp_files/california/`, kept out of git because they total ~224MB):

```bash
python3 build_statewide.py          # write data/statewide.json
python3 verify_statewide_data.py -v # cross-check it against the raw files
```

It scores every district on grades 3–8 combined. `verify_statewide_data.py` re-reads the raw
files with its own code (it doesn't reuse the build script) and checks that every value
recomputes exactly, that SMFCSD's figures match this dashboard's own data, that the grades 3–8
method agrees with CDE's own totals for K–8 districts, and that county offices and State Board
charters are excluded from rankings. It exits non-zero if any check fails.
