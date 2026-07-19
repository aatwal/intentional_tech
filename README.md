# Intentional Tech

Two self-contained, single-file HTML tools examining educational technology
spending and student outcomes in the San Mateo–Foster City School District.
Each file is a complete web page — no build step, no server, no dependencies to
install. Just open it in a browser.

## Contents

### `smfc_caaspp_dashboard.html` — CAASPP 10-Year Trends Dashboard

An interactive dashboard of the district's Smarter Balanced (CAASPP) results
from 2014–15 through 2024–25. All test data is embedded directly in the file, so
it works fully offline.

Filter every chart at once by **subject** (ELA, Math, or both), **school**,
**grade**, and **student group**. The dropdowns adapt to the selection — schools
only list the grades they actually tested, and student groups only appear where
there's reportable data.

Five linked views:

1. **Percent meeting or exceeding standard, 2015–2025** — the headline trend line.
2. **Achievement levels over time** — all four performance bands, year by year.
3. **By grade level** — each grade tracked separately to surface which bands are driving a change.
4. **Which skills eroded most** — claim-area (skill) breakdowns, available for "All Students."
5. **Students tested each year** — participation counts for the current selection.

Note: 2019–20 has no data because CAASPP testing was suspended statewide for
COVID-19.

**Source:** CAASPP Smarter Balanced districtwide research files for San
Mateo–Foster City (2014–15 through 2024–25), combined with the CDE's official
student-group reference file. Charts are rendered with
[Chart.js](https://www.chartjs.org/) (loaded from a CDN).

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

Open either `.html` file directly in any modern browser. Both are standalone —
the only external requests are the Chart.js library and Google Fonts, both loaded
from CDNs.

## Data & disclaimer

These tools compile publicly available district records and state assessment
data for informational and analytical purposes. Figures should be verified
against original source documents before being relied upon.
