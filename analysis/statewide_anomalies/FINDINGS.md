# SMFCSD compared with other California districts: reclassification and other anomalies

Analysis run 2026-10-22, saved 2026-10-27. Every number below is printed by `analyze.py`; a full
snapshot of its output is in [`output.txt`](output.txt), so the numbers survive without the raw data.

## Data and method

- **Source:** CDE's comprehensive statewide CAASPP research files, 2018–2026 (file year = spring of
  the school year, so 2026 = 2025–26). Kept in `caaspp_files/california/comprehensive/` (~6.7GB,
  gitignored).
- **Scope:** district level, grades 3–8 combined. "Met%" means percent meeting or exceeding the
  standard, weighted by students with scores.
- **Comparison set:** about 575 districts with at least 300 grade 3–8 test takers. County offices of
  education (district codes 10xxx), State Board charters and county/state roll-ups are excluded.
  Group-level comparisons only include districts with at least 100 students in that group (30 for
  the hardship-group score change).
- **"Share" of a group** means its students with ELA scores divided by all students with ELA
  scores. CDE suppresses groups under 11 students in a grade, so small shares can read as 0.
- **SED** ("socioeconomically disadvantaged", or "facing hardship" on the pages) is CDE student
  group 31; not SED is 111. Race within SED is 200–207, and race within not SED is 220–227.

## Reproduce

```bash
cd analysis/statewide_anomalies
python3 -I extract_all.py      # ~20s -> work/all.json (~230MB, gitignored)
python3 analyze.py             # first run builds work/agg.pkl; prints everything below
```

## A. The original finding: SED reclassification (already on the published pages)

- **SED share:** 31.1% (2023) → 41.8% (2024) → 40.1% (2025) → 45.3% (2026).
- **Statewide rank of the increase:**
  - 2023→24: 46th
  - 2025→26: 42nd
  - 2023→26: 20th of ~578, against a median change of +1.0 points
- **San Mateo County neighbors** jumped in 2024 and went back the next year. SMFCSD's share
  didn't. The statewide share also rose in 2024 (63.0 → 65.6), so a statewide change that year
  likely caused the spike.

  | District | 2023 | 2024 | 2025 | 2026 |
  |---|---|---|---|---|
  | Hillsborough | 1.8 | 20.4 | 2.8 | 2.0 |
  | Portola Valley | 5.9 | 25.2 | 4.2 | 5.4 |
  | San Carlos | 9.8 | 21.2 | 10.9 | 10.9 |
  | Belmont-Redwood Shores | 8.0 | 15.6 | 10.8 | 11.1 |
  | Pacifica | 20.0 | 29.0 | 19.7 | 19.2 |
  | Brisbane | 20.6 | 30.2 | 23.4 | 23.8 |

- **Both groups up while all students went down, 2023→26:**
  - ELA: 15 districts. SMFCSD had the largest overall drop (−2.9; next is Novato at −2.3).
  - Math: 13 districts. SMFCSD's drop is small (−0.6).
  - Four districts show the pattern in both subjects: SMFCSD, Novato, Davis and Dublin.
- **Small differences from the published pages,** caused by slightly different district-eligibility
  rules in the original one-off script:
  - Eligible districts: this script finds 548, the pages say 549.
  - 2025→26 rank: this script gives 42nd, the pages say 43rd.

  Neither changes the conclusions.

## B. New: who was reclassified (strongest new finding)

The share of each racial group counted as SED in SMFCSD:

| | 2023 | 2024 | 2026 |
|---|---|---|---|
| Asian | 6.4% | 15.4% | 15.6% |
| White | 7.5% | 19.3% | 19.4% |
| Filipino | 10.6% | 31.1% | 30.4% |
| Two or more races | 9.8% | 18.9% | 21.7% |
| Hispanic | 63.1% | 72.4% | 78.8% |

- **Asian students counted as SED:** 103 → 230. That's +9.2 points of share, ranked 39th of 229
  districts with at least 100 Asian students.
- **White students counted as SED:** 90 → 186. That's +11.9 points, ranked 38th of 459.
- **Hispanic students *not* counted as SED:** 956 → 530 (−45%), while total Hispanic test takers
  barely changed (2,592 → 2,505). That's the 26th-steepest drop of 396 districts (median −6%).
- **The newly added SED students score high.** In 2024, Asian SED students met the ELA standard
  at 80.9%, against a district average of 55.0%. White SED ELA went 41.1% → 58.2% in that one
  year.
- **SED ELA jumped +9.0 points in 2023→24 alone,** ranked 22nd of 569 (median +1.2). Math rose
  +7.9 (37th).

**Race-mix decomposition.** This asks what the SED group's met% would be if its racial makeup had
stayed at 2023 levels, keeping each racial group's actual later scores:

| SED group, 2023→2026 | Reported gain | Gain with 2023 race mix |
|---|---|---|
| ELA | +4.9 | +1.5 |
| Math | +5.0 | +1.3 |

That means roughly 70% of the SED group's reported gain comes from *which students were added*,
not from scores rising. The estimate is conservative: it only captures shifts between racial
groups. Movement within a group, such as higher-scoring Hispanic students moving into SED, inflates
the within-race rates too. The not-SED group's ELA gain (+3.0) also shrinks to +1.0 with a fixed
mix.

Meanwhile, the racial and English-learner makeup of all students barely moved (Hispanic 40.2% →
41.6%, Asian 24.9% → 24.4%). **The students stayed about the same; the labels changed.**

## C. New: homeless students nearly tripled

- **Homeless students:** 167 (2.6%) in 2023 → 349 (5.5%) in 2024 → 445 (7.4%) in 2026.
- **The increase** ranked 43rd of 576. The median district is at 2.1% homeless.
- **Among the 194 districts with SED under 50%,** SMFCSD's homeless share is the 6th highest.
- **Homeless students count as SED automatically,** so this accounts for about 280 of the roughly
  730 added SED students.
- **The data can't say** whether this reflects real housing hardship (McKinney-Vento counts
  doubled-up families) or a change in how students are identified.
- **Homeless students' met%** went up: ELA 10.2 → 15.1 and Math 7.6 → 15.7. The Math change
  ranked 16th of 129.

## D. New: students with disabilities declined sharply (a real decline, not reclassification)

- **Met%, 2023 → 2026:**
  - ELA: 18.2% → 13.8% (−4.4), ranked 403rd of 431 (median +0.6)
  - Math: 18.3% → 13.8% (−4.6), ranked 398th of 429 (median +0.4)
- **Their share of students** rose normally (9.8% → 11.1%), so this isn't a composition effect.

## E. New: English-fluent students' ELA is slipping

- **Fluent and English-only students (group 6):** ELA met% 70.1 → 67.4 (−2.8), ranked 513th of
  576 (median +1.8). The level is still high: 117th of 576.
- **English learners:** ELA met% 6.5% in 2026, against a median of 10.2% (320th of 414). Math is
  9.2% (median 10.2%).

## F. Checked and normal, or not unusual on their own

- **Participation (tested ÷ enrolled):** 97.1% in 2026. The median district is 98.2%, and the
  change since 2023 is −0.1. Students opting out isn't driving anything.
- **Enrollment in grades 3–8:** down 16.4% from 2019 to 2026, ranked 145th of 574 (median −10.1%).
  That's more than typical, but not extreme.
- **Parent education "declined to state":** 4.5% → 25.1% in 2024, then 17.8% in 2026. At the same
  time, "not a high school graduate" fell 10.5% → 5.0%. Both point to a 2024 change in how the
  district collects this information. But 58 districts had jumps of 15 points or more, so it isn't
  unusual by itself. Parent education can't explain the SED rise either, because a parent without
  a diploma is itself a reason to count as SED, and that share fell.

## Open questions for the district

1. Why did so many more students, especially Asian, White and higher-income Hispanic students,
   start counting as SED in 2024? And why didn't it revert as it did in neighboring districts?
2. What changed in how homeless students are identified, given the jump from 167 to 445?
3. What's behind the 4–5 point decline for students with disabilities since 2023?
4. Did the district change its enrollment forms or student information system in 2023–24?
