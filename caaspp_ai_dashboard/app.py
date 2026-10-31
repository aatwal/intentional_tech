"""
CAASPP dashboard + AI chat.

Same 11-year CAASPP data and charts as ../smfc_caaspp_dashboard.html,
served through a small Flask app that adds a chat panel backed by the Claude
API. The API key stays server-side (read from the ANTHROPIC_API_KEY env var)
so nothing secret ever reaches the browser.

One app, several districts (districts.py): SMFCSD at /, the others at their own unlinked,
noindex paths. Each has its own data, chat overview and lookup tool (the District class below),
and its own copy of the dashboard page, filled in from templates/index.html at startup.

Run:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...
    python app.py
Then open http://localhost:8060
"""
import hashlib
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

import requests
from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

import districts

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(APP_DIR, "templates", "index.html")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

app = Flask(__name__)

# Chat-query logging. Render (and most PaaS hosts) capture stdout automatically into a
# searchable Logs dashboard -- that's the whole storage layer here, deliberately: no database,
# no persistent disk (Render's web-service filesystem is ephemeral and wiped on every
# redeploy/restart anyway, so writing to a local file would just be lost). To find these lines
# in Render's Logs tab, search for "CHAT_QUERY". Each line is one JSON object: UTC timestamp,
# a salted/truncated hash of the requester's IP (not the raw IP, and not reversible without the
# salt -- just enough to tell distinct visitors apart within a day), the question asked, the
# active Dashboard-tab filters, and whether web search was on. Log retention is whatever your
# Render plan's log history window is; this doesn't add any retention of its own.
QUERY_LOG_SALT = os.environ.get("QUERY_LOG_SALT", "")
query_logger = logging.getLogger("chat_queries")
query_logger.setLevel(logging.INFO)
query_logger.propagate = False
if not query_logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    query_logger.addHandler(_handler)


def hashed_visitor_id(ip):
    digest = hashlib.sha256(f"{QUERY_LOG_SALT}{ip}".encode()).hexdigest()
    return digest[:12]


def log_chat_query(district, question, filters, web_search):
    query_logger.info("CHAT_QUERY " + json.dumps({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "district": district,
        "visitor": hashed_visitor_id(request.remote_addr or "unknown"),
        "question": question,
        "filters": filters,
        "web_search": web_search,
    }))

NUMERIC_GRADES = ["3", "4", "5", "6", "7", "8"]
SUBJECT_NAMES = {"1": "ELA", "2": "Math"}
SUBJECT_ID_BY_NAME = {"ELA": "1", "Math": "2"}

# Pairs used for a quick district-level achievement-gap read: (label, subgroup id, baseline
# subgroup id). Picked from the categories the dashboard's own filter already surfaces.
GAP_SUBGROUPS = [
    ("economic status", "31", "111"),      # socioeconomically disadvantaged vs not
    ("disability status", "128", "99"),    # reported disabilities vs none
    ("English-learner status", "160", "6"),  # EL vs fluent English proficient/English only
]


# ── Data helpers (mirrors key()/statsFor() in the dashboard's own JS) ─────────
def rec_key(school_code, grade, test_id, subgroup_id):
    return f"{school_code}|{grade}|{test_id}|{subgroup_id or '1'}"


class District:
    """One district's data plus everything the chat derives from it."""

    def __init__(self, slug, config):
        self.slug, self.config = slug, config
        with open(config["data"], encoding="utf-8") as f:
            self.data = json.load(f)
        DATA = self.data
        self.schools_by_code = {s["code"]: s for s in DATA["schools"]}
        self.grades_by_code = {g["code"]: g for g in DATA["grades"]}
        self.subgroups_by_id = {sg["id"]: sg for sg in DATA["subgroups"]}

        # Name -> code reverse maps, used only by the lookup_caaspp_data tool below to turn the
        # exact strings Claude picks (constrained to these via the tool's input_schema enums)
        # back into the codes the rest of this class already keys everything by.
        self.school_code_by_name = {s["name"]: s["code"] for s in DATA["schools"]}
        self.grade_code_by_label = {g["label"]: g["code"] for g in DATA["grades"]}

        # Subgroup names alone aren't unique: the 7 race/ethnicity names each appear 3 times
        # (once plain, once nested under "Socioeconomically Disadvantaged", once under
        # "Not..."), each a different id. Qualify only the names that actually collide with
        # their category so the enum below stays exact -- a plain {name: id} dict would silently
        # collapse those to one id.
        counts = Counter(sg["name"] for sg in DATA["subgroups"])

        def display_name(sg):
            return f"{sg['name']} ({sg['category']})" if counts[sg["name"]] > 1 else sg["name"]

        self.subgroup_id_by_name = {display_name(sg): sg["id"] for sg in DATA["subgroups"]}
        self.lookup_tool = self.build_lookup_tool()
        self.overview = self.build_district_overview()
        self.preamble = SYSTEM_PREAMBLE.replace("the San Mateo-Foster City School District",
                                                "the " + config["name"])
        self.page = render_page(self)

    def stats_for(self, rec):
        years = self.data["years"]
        vals = [(i, v) for i, v in enumerate(rec["met_above"]) if v is not None]
        if not vals:
            return None
        first_i, first_v = vals[0]
        latest_i, latest_v = vals[-1]
        peak_i, peak_v = max(vals, key=lambda x: x[1])
        return {
            "first": {"year": years[first_i], "value": first_v},
            "latest": {"year": years[latest_i], "value": latest_v},
            "peak": {"year": years[peak_i], "value": peak_v},
            "change": round(latest_v - first_v, 1),
        }

    def describe_one(self, school_code, grade, test_id, subgroup_id):
        """Plain-text summary for a single school/grade/subject/subgroup combo."""
        rec = self.data["data"].get(rec_key(school_code, grade, test_id, subgroup_id))
        subj = SUBJECT_NAMES[test_id]
        grade_label = self.grades_by_code[grade]["label"]
        if not rec:
            return f"{subj}, {grade_label}: no data (likely suppressed for a small student count)."
        s = self.stats_for(rec)
        if not s:
            return f"{subj}, {grade_label}: no reported years."
        tested = [v for v in rec["tested"] if v is not None]
        line = (
            f"{subj}, {grade_label}: {s['first']['value']}% ({s['first']['year']}) -> "
            f"{s['latest']['value']}% ({s['latest']['year']}), change {s['change']:+.1f} pts, "
            f"peak {s['peak']['value']}% ({s['peak']['year']}), "
            f"{tested[-1] if tested else '—'} students tested most recently."
        )
        if rec.get("claims"):
            labels = (
                ["Reading", "Writing", "Listening", "Research/Inquiry"]
                if test_id == "1"
                else ["Concepts & Procedures", "Problem Solving & Data Analysis", "Communicating Reasoning"]
            )
            claim_bits = []
            for lbl, series in zip(labels, rec["claims"]):
                vals = [v for v in series if v is not None]
                if vals:
                    claim_bits.append(f"{lbl} {vals[-1]}%")
            if claim_bits:
                line += " Claim areas (most recent year): " + ", ".join(claim_bits) + "."
        return line

    def describe_by_grade(self, school_code, test_id, subgroup_id):
        school = self.schools_by_code[school_code]
        grades = [g for g in school["available_grades"] if g in NUMERIC_GRADES]
        lines = []
        for g in grades:
            rec = self.data["data"].get(rec_key(school_code, g, test_id, subgroup_id))
            if not rec:
                continue
            s = self.stats_for(rec)
            if s:
                lines.append(f"Grade {g}: {s['latest']['value']}% latest, change {s['change']:+.1f} pts")
        return lines

    def school_rankings(self, test_id, grade, subgroup_id, limit=5):
        rows = []
        for code, school in self.schools_by_code.items():
            if code == "0000000":
                continue
            rec = self.data["data"].get(rec_key(code, grade, test_id, subgroup_id))
            if not rec:
                continue
            s = self.stats_for(rec)
            if s:
                rows.append((school["name"], s["latest"]["value"], s["change"]))
        rows.sort(key=lambda r: r[1], reverse=True)
        return rows[:limit], rows[-limit:]

    # ── Raw-data fallback tool ─────────────────────────────────────────────────
    # The district overview and build_context() only cover two fixed slices of the dataset
    # (the district-wide baseline, and whatever's currently filtered on the Dashboard tab).
    # This tool lets Claude ask for one specific school/grade/subject/subgroup combination
    # outside those two slices instead of saying it doesn't have the data. It still never sends
    # the raw dataset itself -- each call returns one computed line via describe_one(), the
    # same helper the two pre-computed sections are built from. Parameters are constrained to
    # enums of the real school/grade/subgroup names in this district's data, so resolving a
    # call back to internal codes is an exact dict lookup, not fuzzy string matching.
    def build_lookup_tool(self):
        return {
            "name": "lookup_caaspp_data",
            "description": (
                "Look up exact CAASPP figures for one specific school, grade, subject, and student "
                "group combination. Use this when the question asks about a combination that isn't "
                "covered by the district overview or the current Dashboard-tab selection given in the "
                "system prompt -- instead of guessing or saying the data isn't available -- since the "
                "underlying dataset covers every school/grade/subject/subgroup combination even though "
                "only two slices of it are included above by default."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "school": {
                        "type": "string",
                        "enum": sorted(self.school_code_by_name.keys()),
                        "description": "Exact school name, or 'District Total (all schools)' for district-wide figures.",
                    },
                    "grade": {
                        "type": "string",
                        "enum": [g["label"] for g in self.data["grades"]],
                    },
                    "subject": {
                        "type": "string",
                        "enum": ["ELA", "Math"],
                    },
                    "subgroup": {
                        "type": "string",
                        "enum": sorted(self.subgroup_id_by_name.keys()),
                        "description": (
                            "Exact student-group name, or 'All Students' for no breakdown. Race/"
                            "ethnicity names always include a category in parentheses (e.g. 'Asian "
                            "(Race and Ethnicity)' for the overall group vs. 'Asian (Ethnicity for "
                            "Socioeconomically Disadvantaged)' for that group disaggregated by "
                            "socioeconomic status) because the same race/ethnicity name is reused "
                            "across categories with different underlying figures."
                        ),
                    },
                },
                "required": ["school", "grade", "subject", "subgroup"],
            },
        }

    def run_lookup_tool(self, args):
        school_name = args.get("school") or "District Total (all schools)"
        grade_label = args.get("grade") or "All Grades"
        subject_name = args.get("subject") or "ELA"
        subgroup_name = args.get("subgroup") or "All Students"

        school_code = self.school_code_by_name.get(school_name)
        grade_code = self.grade_code_by_label.get(grade_label)
        test_id = SUBJECT_ID_BY_NAME.get(subject_name)
        subgroup_id = self.subgroup_id_by_name.get(subgroup_name)
        if not all([school_code, grade_code, test_id, subgroup_id]):
            return (
                f"Could not resolve one or more of school={school_name!r}, grade={grade_label!r}, "
                f"subject={subject_name!r}, subgroup={subgroup_name!r} to a known value."
            )

        line = self.describe_one(school_code, grade_code, test_id, subgroup_id)
        return f"{school_name}, student group '{subgroup_name}' -- {line}"

    def build_district_overview(self):
        """Plain-text, filter-independent summary of the whole district: baseline trend, every
        grade, every school, and a few equity-gap comparisons. Computed once at startup (it
        doesn't depend on the request) and prepended to every chat turn's context so broad or
        cross-school/cross-grade questions can be answered even when a narrow filter is
        selected on the Dashboard tab."""
        lines = [
            "District-wide overview (All Grades, All Students unless noted -- this section is "
            "independent of whatever is currently filtered on the Dashboard tab; use it for "
            "broad, district-level, or cross-school/cross-grade questions):",
        ]
        for t in ("1", "2"):
            lines.append("- " + self.describe_one("0000000", "13", t, "1"))

        for t in ("1", "2"):
            by_grade = self.describe_by_grade("0000000", t, "1")
            if by_grade:
                lines.append(f"District by grade, {SUBJECT_NAMES[t]} (latest year, change since first tested year): "
                             + "; ".join(by_grade))

        for t in ("1", "2"):
            top, bottom = self.school_rankings(t, "13", "1", limit=6)
            if top:
                lines.append(
                    f"All schools ranked by latest {SUBJECT_NAMES[t]} % meeting/exceeding, highest first: "
                    + "; ".join(f"{n} {v}%" for n, v, c in top)
                )
            if bottom:
                lines.append(
                    f"Lowest {SUBJECT_NAMES[t]} schools: "
                    + "; ".join(f"{n} {v}%" for n, v, c in reversed(bottom))
                )

        gap_bits = []
        for label, group_id, baseline_id in GAP_SUBGROUPS:
            for t in ("1", "2"):
                rec_group = self.data["data"].get(rec_key("0000000", "13", t, group_id))
                rec_base = self.data["data"].get(rec_key("0000000", "13", t, baseline_id))
                s_group = self.stats_for(rec_group) if rec_group else None
                s_base = self.stats_for(rec_base) if rec_base else None
                if s_group and s_base:
                    gap = round(s_base["latest"]["value"] - s_group["latest"]["value"], 1)
                    gap_bits.append(
                        f"{SUBJECT_NAMES[t]} {label}: {self.subgroups_by_id[group_id]['name']} "
                        f"{s_group['latest']['value']}% vs {self.subgroups_by_id[baseline_id]['name']} "
                        f"{s_base['latest']['value']}% (gap {gap:+.1f} pts)"
                    )
        if gap_bits:
            lines.append("District-level achievement gaps, latest year: " + "; ".join(gap_bits))

        return "\n".join(lines)

    def build_context(self, filters):
        test_id = filters.get("testId", "1")
        school_code = filters.get("schoolCode", "0000000")
        grade = filters.get("grade", "13")
        subgroup_id = filters.get("subgroupId", "1")

        school = self.schools_by_code.get(school_code, self.schools_by_code["0000000"])
        grade_label = self.grades_by_code.get(grade, self.grades_by_code["13"])["label"]
        subgroup = self.subgroups_by_id.get(subgroup_id, self.subgroups_by_id["1"])
        subjects = ["1", "2"] if test_id == "all" else [test_id]

        lines = [
            f"Current Dashboard-tab selection (more specific than the district overview above -- "
            f"use this for anything about the exact school/grade/group in view): {school['name']}, "
            f"{grade_label}, student group '{subgroup['name']}', subject(s): "
            f"{'ELA & Math' if test_id == 'all' else SUBJECT_NAMES[test_id]}.",
            "",
            "Selected-combination stats (percent meeting or exceeding standard, i.e. "
            "'met_above', the sum of the Met and Exceeded achievement bands):",
        ]
        for t in subjects:
            lines.append("- " + self.describe_one(school_code, grade, t, subgroup_id))

        if grade == "13":
            for t in subjects:
                by_grade = self.describe_by_grade(school_code, t, subgroup_id)
                if by_grade:
                    lines.append(f"By-grade breakdown, {SUBJECT_NAMES[t]} (latest year, change since first tested year):")
                    lines.extend(f"  - {row}" for row in by_grade)

        for t in subjects:
            top, bottom = self.school_rankings(t, grade, subgroup_id)
            if top:
                lines.append(
                    f"Schools ranked by latest {SUBJECT_NAMES[t]} % meeting/exceeding "
                    f"(same grade & student group), highest first: "
                    + "; ".join(f"{n} {v}% ({c:+.1f} pts change)" for n, v, c in top)
                )
            if bottom:
                lines.append(
                    f"Lowest {SUBJECT_NAMES[t]} schools for this grade & group: "
                    + "; ".join(f"{n} {v}% ({c:+.1f} pts change)" for n, v, c in reversed(bottom))
                )

        return "\n".join(lines)


with open(TEMPLATE_PATH, encoding="utf-8") as f:
    TEMPLATE = f.read()
RECLASS_LINK = '      <a class="tab-btn" href="reclassification-explained.html">Reclassification explained</a>\n'


def render_page(d):
    """templates/index.html with this district's names and data filled in. One pass, so text
    from the data is never itself treated as a placeholder."""
    c = d.config
    values = {
        "DATA": json.dumps(d.data, ensure_ascii=False).replace("</", "<\\/"),  # can't end the <script>
        "NAME": c["name"], "PLACE": c["place"], "SHORT": c["short"], "ORG": c["org"],
        "COUNTY": c["county"], "COUNTY_NAME": c["county_name"],
        "HOME_ID": f"{c['county']}|{c['code']}", "SLUG": d.slug,
        "HEAD_EXTRA": "" if c["listed"] else '<meta name="robots" content="noindex, nofollow">\n',
        "RECLASS_LINK": RECLASS_LINK if c["writeups"] else "",
    }
    return re.sub(r"%%([A-Z_]+)%%", lambda m: values[m.group(1)], TEMPLATE)


SYSTEM_PREAMBLE = (
    "You are a data analyst assistant embedded in a public CAASPP "
    "results dashboard for the San Mateo-Foster City School District. The dataset covers "
    "school years 2014-15 through 2025-26; 2019-20 has no data because CAASPP testing was "
    "suspended statewide for COVID-19. "
    "Achievement bands are Exceeded / Met / Nearly Met / "
    "Not Met; 'met_above' (the headline percentage) is Exceeded + Met. Starting with 2025-26, "
    "CDE relabeled these levels Advanced / Proficient / Developing / Minimal ('Proficient and "
    "Above' = met_above); they are the same four levels, so trends are comparable across the "
    "label change. Claim areas are "
    "skill-level breakdowns within ELA or Math, suppressed by the state below a minimum "
    "student count even when the overall score is reported. Treat a missing claim/record "
    "as suppression, not zero. "
    "Answer primarily from the CAASPP data given below; do not invent figures. Keep answers "
    "concise (a few sentences or short bullet points), and cite the specific numbers you're "
    "using. You're given two sections: a district-wide overview (all schools, all grades, "
    "gap comparisons -- for broad or cross-school/cross-grade questions) and the current "
    "Dashboard-tab selection (a narrower, more specific slice -- for questions about that "
    "exact school/grade/group). Use whichever fits the question, and say which one you're "
    "drawing from if it's not obvious. If the question asks about a specific school, grade, "
    "subject, or student group combination that isn't covered by either section, call the "
    "lookup_caaspp_data tool to fetch that exact combination rather than guessing or saying "
    "the data isn't available -- the underlying dataset covers every combination even though "
    "only two slices of it are shown to you by default.\n\n"
)

WEB_SEARCH_ADDENDUM = (
    "The user has also enabled web search for this question. You may use it for external "
    "context the dataset can't provide (e.g. news coverage, curriculum or policy changes, "
    "other districts' results), but the CAASPP numbers above remain the source of truth for "
    "anything about test scores, participation, or trends, and web search must never override "
    "or be blended into them. When you do use a web result, say so explicitly and note the "
    "source, and be skeptical of results that turn out to be about an unrelated district or "
    "topic.\n\n"
)

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}


# Built once at startup: every district's data, chat overview and page.
DISTRICTS = {slug: District(slug, c) for slug, c in districts.DISTRICTS.items()}


def district_page(slug):
    d = DISTRICTS[slug]
    resp = Response(d.page, mimetype="text/html")
    if not d.config["listed"]:
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.route("/")
def index():
    return district_page(districts.DEFAULT)


for _slug, _c in districts.DISTRICTS.items():
    if _slug != districts.DEFAULT:
        app.add_url_rule(_c["path"], f"district_{_slug}", lambda _slug=_slug: district_page(_slug))


# Standalone write-ups that sit alongside the dashboard (plain HTML, not Jinja templates).
PAGES = {"caaspp-2025-26.html", "reclassification-explained.html"}


@app.route("/<page>")
def page(page):
    if page not in PAGES:
        abort(404)
    return send_file(os.path.join(APP_DIR, "templates", page))


@app.route("/data/<path:filename>")
def data_files(filename):
    return send_from_directory(os.path.join(APP_DIR, "data"), filename)


@app.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json(force=True, silent=True) or {}
    history = body.get("history") or []
    filters = body.get("filters") or {}
    web_search = bool(body.get("webSearch"))
    district = DISTRICTS.get(body.get("district") or districts.DEFAULT)
    if district is None:
        return jsonify({"error": "Unknown district."}), 400

    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in history
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    if not messages or messages[-1]["role"] != "user":
        return jsonify({"error": "No user message provided."}), 400

    log_chat_query(district.slug, messages[-1]["content"], filters, web_search)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Server is missing ANTHROPIC_API_KEY."}), 500

    try:
        context = district.build_context(filters)
    except Exception as e:  # bad/unknown filter values, etc.
        return jsonify({"error": f"Could not read the current selection: {e}"}), 400

    system = district.preamble + (WEB_SEARCH_ADDENDUM if web_search else "") + district.overview + "\n\n" + context
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 1200 if web_search else 800,
        "system": system,
        "messages": messages,
        "tools": [district.lookup_tool] + ([WEB_SEARCH_TOOL] if web_search else []),
    }

    # Two different tool styles can extend a turn beyond one API call, each needing different
    # handling:
    #   - web_search is server-executed: Anthropic runs the search itself and the response
    #     comes back with stop_reason "pause_turn" mid-answer. We just resend the accumulated
    #     content and let it continue, same pattern as missing_children/app.py's ask_claude().
    #   - lookup_caaspp_data is client-executed: the response comes back with stop_reason
    #     "tool_use" and we have to run it ourselves (it's just an in-memory dict lookup, no
    #     real latency) and send the result back as a tool_result before Claude can continue.
    # Executing a web search takes real time, so a single round needs more than 30s; lookup
    # rounds are effectively instant. Rebalance max_rounds vs per_call_timeout (not just raise
    # both) to stay under gunicorn's --timeout 120 in the worst case (a lookup then a search
    # then a final answer, all in one turn).
    max_rounds = 4 if web_search else 3
    per_call_timeout = 35 if web_search else 30
    texts = []
    try:
        for _ in range(max_rounds):
            resp = requests.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=per_call_timeout,
            )
            data = resp.json()
            if "error" in data:
                return jsonify({"error": f"Claude API error: {data['error'].get('message', data['error'])}"}), 502
            content = data.get("content", [])
            texts = [b["text"] for b in content if b.get("type") == "text"]
            stop_reason = data.get("stop_reason")
            if stop_reason == "pause_turn":
                payload["messages"] = payload["messages"] + [{"role": "assistant", "content": content}]
                continue
            if stop_reason == "tool_use":
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": district.run_lookup_tool(block.get("input") or {}),
                    }
                    for block in content
                    if block.get("type") == "tool_use" and block.get("name") == "lookup_caaspp_data"
                ]
                payload["messages"] = payload["messages"] + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": tool_results},
                ]
                continue
            break
    except Exception as e:
        return jsonify({"error": f"Error calling Claude API: {e}"}), 502

    reply = "\n".join(texts) if texts else "(no response)"
    return jsonify({"reply": reply})


if __name__ == "__main__":
    app.run(debug=False, port=8060)
