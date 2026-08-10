"""
CAASPP dashboard + AI chat.

Same 10-year Smarter Balanced data and charts as ../smfc_caaspp_dashboard.html,
served through a small Flask app that adds a chat panel backed by the Claude
API. The API key stays server-side (read from the ANTHROPIC_API_KEY env var)
so nothing secret ever reaches the browser.

Run:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...
    python app.py
Then open http://localhost:8060
"""
import json
import os

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(APP_DIR, "data", "caaspp_data.json")
INDEX_PATH = os.path.join(APP_DIR, "templates", "index.html")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

app = Flask(__name__)

with open(DATA_PATH, encoding="utf-8") as f:
    DATA = json.load(f)

SCHOOLS_BY_CODE = {s["code"]: s for s in DATA["schools"]}
GRADES_BY_CODE = {g["code"]: g for g in DATA["grades"]}
SUBGROUPS_BY_ID = {sg["id"]: sg for sg in DATA["subgroups"]}
NUMERIC_GRADES = ["3", "4", "5", "6", "7", "8"]
SUBJECT_NAMES = {"1": "ELA", "2": "Math"}


# ── Data helpers (mirrors key()/statsFor() in the dashboard's own JS) ─────────
def rec_key(school_code, grade, test_id, subgroup_id):
    return f"{school_code}|{grade}|{test_id}|{subgroup_id or '1'}"


def stats_for(rec):
    years = DATA["years"]
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


def describe_one(school_code, grade, test_id, subgroup_id):
    """Plain-text summary for a single school/grade/subject/subgroup combo."""
    rec = DATA["data"].get(rec_key(school_code, grade, test_id, subgroup_id))
    subj = SUBJECT_NAMES[test_id]
    grade_label = GRADES_BY_CODE[grade]["label"]
    if not rec:
        return f"{subj}, {grade_label}: no data (likely suppressed for a small student count)."
    s = stats_for(rec)
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


def describe_by_grade(school_code, test_id, subgroup_id):
    school = SCHOOLS_BY_CODE[school_code]
    grades = [g for g in school["available_grades"] if g in NUMERIC_GRADES]
    lines = []
    for g in grades:
        rec = DATA["data"].get(rec_key(school_code, g, test_id, subgroup_id))
        if not rec:
            continue
        s = stats_for(rec)
        if s:
            lines.append(f"Grade {g}: {s['latest']['value']}% latest, change {s['change']:+.1f} pts")
    return lines


def school_rankings(test_id, grade, subgroup_id, limit=5):
    rows = []
    for code, school in SCHOOLS_BY_CODE.items():
        if code == "0000000":
            continue
        rec = DATA["data"].get(rec_key(code, grade, test_id, subgroup_id))
        if not rec:
            continue
        s = stats_for(rec)
        if s:
            rows.append((school["name"], s["latest"]["value"], s["change"]))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:limit], rows[-limit:]


def build_context(filters):
    test_id = filters.get("testId", "1")
    school_code = filters.get("schoolCode", "0000000")
    grade = filters.get("grade", "13")
    subgroup_id = filters.get("subgroupId", "1")

    school = SCHOOLS_BY_CODE.get(school_code, SCHOOLS_BY_CODE["0000000"])
    grade_label = GRADES_BY_CODE.get(grade, GRADES_BY_CODE["13"])["label"]
    subgroup = SUBGROUPS_BY_ID.get(subgroup_id, SUBGROUPS_BY_ID["1"])
    subjects = ["1", "2"] if test_id == "all" else [test_id]

    lines = [
        f"Current dashboard selection: {school['name']}, {grade_label}, "
        f"student group '{subgroup['name']}', subject(s): "
        f"{'ELA & Math' if test_id == 'all' else SUBJECT_NAMES[test_id]}.",
        "",
        "Selected-combination stats (percent meeting or exceeding standard, i.e. "
        "'met_above' — the sum of the Met and Exceeded achievement bands):",
    ]
    for t in subjects:
        lines.append("- " + describe_one(school_code, grade, t, subgroup_id))

    if grade == "13":
        for t in subjects:
            by_grade = describe_by_grade(school_code, t, subgroup_id)
            if by_grade:
                lines.append(f"By-grade breakdown, {SUBJECT_NAMES[t]} (latest year, change since first tested year):")
                lines.extend(f"  - {row}" for row in by_grade)

    for t in subjects:
        top, bottom = school_rankings(t, grade, subgroup_id)
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


SYSTEM_PREAMBLE = (
    "You are a data analyst assistant embedded in a public CAASPP (Smarter Balanced) "
    "results dashboard for the San Mateo-Foster City School District. The dataset covers "
    "school years 2014-15 through 2024-25; 2019-20 has no data because CAASPP testing was "
    "suspended statewide for COVID-19. Achievement bands are Exceeded / Met / Nearly Met / "
    "Not Met; 'met_above' (the headline percentage) is Exceeded + Met. Claim areas are "
    "skill-level breakdowns within ELA or Math, suppressed by the state below a minimum "
    "student count even when the overall score is reported — treat a missing claim/record "
    "as suppression, not zero. "
    "Answer primarily from the CAASPP data given below; do not invent figures. Keep answers "
    "concise (a few sentences or short bullet points), cite the specific numbers you're using, "
    "and if the user asks about something outside the current filter selection, say so and "
    "suggest which filter to change rather than guessing.\n\n"
)

WEB_SEARCH_ADDENDUM = (
    "The user has also enabled web search for this question. You may use it for external "
    "context the dataset can't provide (e.g. news coverage, curriculum or policy changes, "
    "other districts' results) — but the CAASPP numbers above remain the source of truth for "
    "anything about test scores, participation, or trends, and web search must never override "
    "or be blended into them. When you do use a web result, say so explicitly and note the "
    "source, and be skeptical of results that turn out to be about an unrelated district or "
    "topic.\n\n"
)

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}


@app.route("/")
def index():
    return send_file(INDEX_PATH)


@app.route("/data/<path:filename>")
def data_files(filename):
    return send_from_directory(os.path.join(APP_DIR, "data"), filename)


@app.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json(force=True, silent=True) or {}
    history = body.get("history") or []
    filters = body.get("filters") or {}
    web_search = bool(body.get("webSearch"))

    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in history
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    if not messages or messages[-1]["role"] != "user":
        return jsonify({"error": "No user message provided."}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Server is missing ANTHROPIC_API_KEY."}), 500

    try:
        context = build_context(filters)
    except Exception as e:  # bad/unknown filter values, etc.
        return jsonify({"error": f"Could not read the current selection: {e}"}), 400

    system = SYSTEM_PREAMBLE + (WEB_SEARCH_ADDENDUM if web_search else "") + context
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 1200 if web_search else 800,
        "system": system,
        "messages": messages,
    }
    if web_search:
        payload["tools"] = [WEB_SEARCH_TOOL]

    # web_search is a server-executed tool: Anthropic runs the search itself and the API
    # response comes back with stop_reason "pause_turn" mid-answer. We just resend the
    # accumulated content and let it continue, up to a few rounds, same pattern as
    # missing_children/app.py's ask_claude().
    texts = []
    try:
        for _ in range(5):
            resp = requests.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=60 if web_search else 30,
            )
            data = resp.json()
            if "error" in data:
                return jsonify({"error": f"Claude API error: {data['error'].get('message', data['error'])}"}), 502
            content = data.get("content", [])
            texts = [b["text"] for b in content if b.get("type") == "text"]
            if data.get("stop_reason") != "pause_turn":
                break
            payload["messages"] = payload["messages"] + [{"role": "assistant", "content": content}]
    except Exception as e:
        return jsonify({"error": f"Error calling Claude API: {e}"}), 502

    reply = "\n".join(texts) if texts else "(no response)"
    return jsonify({"reply": reply})


if __name__ == "__main__":
    app.run(debug=False, port=8060)
