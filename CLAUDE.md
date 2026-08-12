# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Standalone tools examining educational technology spending and student outcomes in the
San Mateo–Foster City School District (SMFCSD). No build system, no test suite, no package
manager at the repo root — most of it is self-contained HTML.

- `index.html` — landing page linking to the two tools below.
- `smfc_caaspp_dashboard.html` — no longer a standalone dashboard. It's now a thin redirect stub
  (meta refresh + `location.replace`) that forwards to the deployed `caaspp_ai_dashboard/` app at
  `https://smfcsd-tech-analysis.onrender.com/`, the current home of the CAASPP dashboard. Kept
  around so old links/bookmarks still land somewhere. If that deployment's URL ever changes,
  update it here too. The embedded `DATA` blob is gone from this file — see the data-sync note
  below, which now has one fewer copy to keep in sync.
- `vendor_purchase_tracker.html` — district vendor spending ledger. Seed records are hardcoded;
  user-added records persist via the Artifacts `window.storage` API (`window.storage.get/set`),
  which only exists when this file is published through the Artifact tool with the storage
  capability enabled. Opened as a plain local file, `window.storage` doesn't exist, so adds are
  caught and silently degrade to session-only (see `saveStored()`/`loadStored()`).
- `caaspp_ai_dashboard/` — the Flask-backed CAASPP dashboard with an added AI chat panel. Used to
  be a second copy alongside a standalone static dashboard; now it's the only working copy. See
  below.
- `caaspp_files/` — raw CDE CAASPP research-file exports (`sb_ca20NN_all_41_69039_csv_vN.txt`),
  one per year 2015–2025 (2020 excluded — COVID testing suspension). Source data only; nothing
  reads these at runtime currently (both dashboards consume the already-derived `DATA` blob).

## Running things

No commands to build or lint the static HTML files — just open them in a browser.

`caaspp_ai_dashboard/` (Flask app):
```bash
cd caaspp_ai_dashboard
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python app.py               # http://localhost:8060
```
No test suite. Sanity-check changes with `python3 -c "import app"` (catches import/syntax errors
and, since `DATA` and `DISTRICT_OVERVIEW` are built at module load, most data-shape bugs too) and
by curling `/api/chat` (expect a clean JSON `{"error": "Server is missing ANTHROPIC_API_KEY."}`
if run without a key — a non-JSON/HTML response means something's wrong upstream of Flask, e.g. a
gunicorn/proxy timeout, not an app bug).

## `caaspp_ai_dashboard/` architecture

**History:** this used to be a second, Flask-backed copy of a standalone `smfc_caaspp_dashboard.html`
— kept separate because that file's design point was zero-server/zero-key (just open it), and a
chat feature needs somewhere to hold an API key safely, which a browser tab can't do. That's no
longer a live constraint: `smfc_caaspp_dashboard.html` is now just a redirect stub pointing here
(see above), so `caaspp_ai_dashboard/` is the only working copy of the dashboard. It's still a
small Flask app for the same reason as before — `POST /api/chat` holds `ANTHROPIC_API_KEY`
server-side (env var, never sent to the browser) and proxies to the Messages API.

**Data lives in two places that must be kept in sync** — there's no shared source of truth at
runtime:
1. `caaspp_ai_dashboard/templates/index.html` — embedded `const DATA = {...}` (one JS line), for
   its own client-side charts.
2. `caaspp_ai_dashboard/data/caaspp_data.json` — the same data again, extracted so `app.py` can
   load it without parsing a multi-MB HTML file. Regeneration one-liner is in
   `caaspp_ai_dashboard/README.md`.

If the underlying CAASPP data is ever refreshed, update `templates/index.html` first, then re-copy/
re-extract into the JSON file.

**Chat grounding — the important design constraint:** the full dataset is ~4.5MB (~6,700
school×grade×subject×subgroup records). It is never sent to the model. Instead `app.py` builds a
compact plain-text summary per request:
- `DISTRICT_OVERVIEW` (in `app.py`) — computed **once at module load**, filter-independent:
  district-wide baseline trend, every grade, every school ranked, and a few equity-gap comparisons
  (economic/disability/EL status). Present on every chat turn regardless of what's filtered.
- `build_context(filters)` — computed per-request from whatever subject/school/grade/subgroup is
  currently selected on the Dashboard tab (same numbers the charts show, plus a same-scope
  school ranking).

Both sections go into the system prompt (`SYSTEM_PREAMBLE + DISTRICT_OVERVIEW + build_context(...)`),
with instructions telling the model which section answers which kind of question. This is why the
chat can answer both broad ("which grade changed the most district-wide?") and narrow
("how did this school do in Math this year?") questions without ever seeing the raw dataset.
`rec_key()`/`stats_for()` in `app.py` mirror `key()`/`statsFor()` in the dashboards' own client-side
JS — keep them in sync if the achievement-band/change logic ever changes.

**`lookup_caaspp_data`** is a third, on-demand path for anything outside those two fixed slices —
e.g. a specific school × grade × subgroup combo nobody filtered to. It's a client-executed Claude
tool, always included (not opt-in like web search, since it's still the dashboard's own data, not
an external source): the model calls it with a school/grade/subject/subgroup, `run_lookup_tool()`
resolves those to codes and returns one line via `describe_one()` — still never the raw dataset,
just the one combination asked for. Parameters are constrained to enums built from the real
school/grade/subject/subgroup names in `DATA`, so resolution is an exact dict lookup, not fuzzy
matching. One wrinkle: 7 race/ethnicity subgroup names (Asian, White, Hispanic or Latino, etc.)
are each reused across 3 categories (plain, and disaggregated by socioeconomic status) with
different ids — `SUBGROUP_ID_BY_NAME` qualifies every colliding name with its category (e.g.
`"Asian (Race and Ethnicity)"` vs `"Asian (Ethnicity for Socioeconomically Disadvantaged)"`) so
the enum stays 1:1 with ids; a naive `{name: id}` dict would silently collapse those to one id.

**Web search** is an opt-in, off-by-default checkbox (`webSearch` in the request body) using
Anthropic's server-executed `web_search` tool. That tool returns `stop_reason: "pause_turn"`
mid-answer (search happens Anthropic-side, handled by resending accumulated messages) while
`lookup_caaspp_data` returns `stop_reason: "tool_use"` (we execute it locally and send back a
`tool_result`) — `chat()`'s loop in `app.py` handles both, same pattern as the sibling
`../missing_children/app.py`'s `ask_claude()` for the `pause_turn` half.
Timeouts are deliberately tight (`max_rounds`/`per_call_timeout` in `chat()`) to stay under
gunicorn's worker timeout and hosting-platform proxy limits — a raw HTML error page reaching the
frontend (`SyntaxError: Unexpected token '<'`) means a platform-level timeout, not an app bug; a
JSON `{"error": "...Read timed out..."}` means the per-call timeout itself is too tight. Rebalance
`max_rounds` vs `per_call_timeout` rather than just raising both.

**Deploying (e.g. Render):** Root Directory must be `caaspp_ai_dashboard` (this repo has multiple
projects at the top level). Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120`.
Set `ANTHROPIC_API_KEY` in the platform's environment settings, not in code. Currently deployed at
`https://smfcsd-tech-analysis.onrender.com/`, which is also the redirect target hardcoded into
`smfc_caaspp_dashboard.html`.

## Editorial/visual conventions

All of the HTML tools share a "public record" print aesthetic — serif display type for headings
(Iowan Old Style in the CAASPP dashboards, Fraunces on the `index.html` landing page) over
Helvetica Neue/Inter UI chrome — but each file defines its own `:root` custom properties rather
than sharing a stylesheet, and the palettes differ:
- `caaspp_ai_dashboard/templates/index.html`: warm paper `--paper:#f6f3ec`, `--accent` teal
  `#2a6f6f`, gold `#d9a441`, rust `#b5493f`. `smfc_caaspp_dashboard.html` (now just a redirect
  stub) reuses the same tokens for its brief landing message.
- `vendor_purchase_tracker.html`: cooler paper `--paper:#eef1f5`, no single `--accent` — instead
  per-category colors `--cat-assessment`/`--cat-curriculum`/`--cat-ai`/`--cat-materials`.

When extending a file, match its own existing custom properties rather than importing another
file's palette or introducing new ad hoc colors.
