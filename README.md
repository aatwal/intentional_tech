# Intentional Tech

Tools examining educational technology spending and student outcomes in the
San Mateo–Foster City School District: a CAASPP test-score dashboard (a small
Flask app) and a self-contained vendor spending ledger.

## Contents

### `caaspp_ai_dashboard/` — CAASPP 11-Year Trends Dashboard

An interactive dashboard of the district's CAASPP results from 2014–15 through
2025–26, with an AI chat panel for asking questions about the data. It's a small
Flask app (the chat needs a server to hold the Anthropic API key), deployed at
<https://smfcsd-tech-analysis.onrender.com/>. See
[`caaspp_ai_dashboard/README.md`](caaspp_ai_dashboard/README.md) for setup and
how to add a new year of data.

Filter every chart at once by **subject** (ELA, Math, or both), **school**,
**grade**, and **student group**. The dropdowns adapt to the selection — schools
only list the grades they actually tested, and student groups only appear where
there's reportable data.

Five linked views:

1. **Percent meeting or exceeding standard, 2015–2026** — the headline trend line.
2. **Achievement levels over time** — all four performance bands, year by year.
3. **By grade level** — each grade tracked separately to surface which bands are driving a change.
4. **Which skills eroded most** — claim-area (skill) breakdowns, available for any student group CDE reports it for (very small groups are suppressed by CDE itself).
5. **Students tested each year** — participation counts for the current selection.

Note: 2019–20 has no data because CAASPP testing was suspended statewide for
COVID-19. Starting in 2025–26, CDE labels the four achievement levels Advanced /
Proficient / Developing / Minimal; the dashboard treats them as the same levels
previously labeled Standard Exceeded / Met / Nearly Met / Not Met.

**Source:** CAASPP districtwide research files for San Mateo–Foster City
(2014–15 through 2025–26, kept in `caaspp_files/`), combined with the CDE's
official student-group reference file. Charts are rendered with
[Chart.js](https://www.chartjs.org/) (loaded from a CDN).

`smfc_caaspp_dashboard.html` used to be a standalone, offline version of this
dashboard. It's now just a redirect to the deployed app, kept so old links still
work.

### `vendor_purchase_tracker.html` — District Vendor Ledger

A ledger of the district's software and curriculum purchases, styled as a public
record. It ships with seed records (Amplify, NWEA, Renaissance, Zearn, and
others) and lets you **search**, **filter by category** (Assessment, Curriculum
/ Software, AI Platform, Printed Materials), and **sort** by amount, vendor, or
term start date. Summary stats (total spend, vendor count, average per vendor)
update with your filters.

You can also **add your own records** through the form at the bottom. Entries you
add are saved locally via the browser's storage and can be removed; the seed
records are read-only. Records with a note are flagged for review (e.g., a
line-item total that doesn't match a quote summary).

## Usage

Open `vendor_purchase_tracker.html` directly in any modern browser; it's fully
standalone, with no external requests. The CAASPP
dashboard runs as a Flask app — use the deployed site, or see
`caaspp_ai_dashboard/README.md` to run it locally.

## Data & disclaimer

These tools compile publicly available district records and state assessment
data for informational and analytical purposes. Figures should be verified
against original source documents before being relied upon.
