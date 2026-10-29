"""Site editor: lets trusted, non-technical editors change the Intentional Tech site by
typing a plain-English request, previewing the result, and publishing it.

Flow: request -> Claude edits files in a fresh clone of main -> preview -> Publish
(commit + push to main, which Render deploys) or Discard. "Undo last change" reverts
the most recent commit on main if this tool made it.

Safety model (see README.md for more):
- Claude gets five file tools and no shell. It can only touch top-level .html pages
  (minus a few redirect stubs), and it can create new pages but never overwrite or
  delete whole existing ones.
- Each editor signs in with their own passcode; commits record who asked for what.
- One change in progress at a time (single in-memory STATE, so run with ONE worker).
"""

import difflib
import hmac
import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from flask import (Flask, Response, abort, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

# --------------------------------------------------------------------------- config

REPO_SLUG = os.environ.get("REPO_SLUG", "aatwal/intentional_tech")
BRANCH = os.environ.get("BRANCH", "main")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
# REPO_URL overrides the GitHub URL (used for local testing against a throwaway repo).
REPO_URL = os.environ.get("REPO_URL") or f"https://github.com/{REPO_SLUG}.git"
WORKDIR = Path(os.environ.get("SITE_EDITOR_WORKDIR", "/tmp/site-editor-repo"))
MODEL = os.environ.get("MODEL", "claude-sonnet-5-5")
SITE_URL = os.environ.get("SITE_URL", "https://www.intentionaltechinschools.com/")
BOT_EMAIL = os.environ.get("GIT_AUTHOR_EMAIL", "site-editor@users.noreply.github.com")
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "40"))
COMMIT_TRAILER = "Site-Editor: yes"


def parse_passcodes(raw):
    """EDITOR_PASSCODES="Suki:long-random-code,Uma:another-code" -> {code: name}."""
    out = {}
    for item in (raw or "").split(","):
        if ":" in item:
            name, code = item.split(":", 1)
            if name.strip() and len(code.strip()) >= 8:
                out[code.strip()] = name.strip()
    return out


PASSCODES = parse_passcodes(os.environ.get("EDITOR_PASSCODES", ""))

# Pages editors may change. Top-level .html only; these redirect stubs are off limits.
PROTECTED_PAGES = {
    "caaspp-2025-26.html",
    "reclassification-explained.html",
    "smfc_caaspp_dashboard.html",
}
PAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}\.html$")
LONG_LINE = 1500  # characters shown per line; longer lines (embedded data) are cut

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SECURE=os.environ.get("INSECURE_COOKIES") != "1",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14)

# --------------------------------------------------------------------------- state

LOCK = threading.Lock()
CLONE_LOCK = threading.Lock()    # the first clone can be triggered by a job or a page view


def fresh_state():
    """The one shared working copy: who's using it, and the unpublished draft in it."""
    return {
        "status": "idle",        # idle | running | ready (a draft is waiting) | publishing
        "id": None,
        "editor": None,          # who's running, publishing, or owns the draft
        "change_requests": [],   # prompts whose turn changed pages (for the commit message)
        "change_summary": "",    # Claude's reply to the latest turn that changed pages
        "activity": [],          # plain-English progress lines for the UI
        "changed": [],
        "warnings": [],
        "error": "",
        "started": None,
    }


STATE = fresh_state()
LAST_RESULT = {"message": ""}    # banner shown after publish / undo

# Each editor's own conversation, so two editors never overwrite each other's thread or
# Claude's memory of it. Kept until they start a new one (or the server restarts).
CONVOS = {}


def convo(editor):
    """Call with LOCK held."""
    return CONVOS.setdefault(editor, {
        "thread": [],            # what the editor sees: {"role": you|claude|error|note, "text", ...}
        "messages": [],          # the Claude conversation, so later requests build on earlier ones
        "notes": [],             # things that happened since Claude's last reply (published, ...)
    })


def tell(editor, thread_text, claude_note):
    """Note something that happened outside an editor's conversation (published, discarded,
    undone): shown in their thread, and passed to Claude with their next message. Call with
    LOCK held."""
    c = convo(editor)
    if c["messages"]:
        c["thread"].append({"role": "note", "text": thread_text})
        c["notes"].append(claude_note)


def reset_state():
    """Call with LOCK held."""
    STATE.clear()
    STATE.update(fresh_state())


def set_state(**kw):
    with LOCK:
        STATE.update(kw)


def note(line):
    with LOCK:
        STATE["activity"].append(line)
        STATE["activity"] = STATE["activity"][-60:]


# --------------------------------------------------------------------------- git

def authenticated_url():
    if GITHUB_TOKEN and REPO_URL.startswith("https://github.com/"):
        return REPO_URL.replace("https://", f"https://x-access-token:{GITHUB_TOKEN}@", 1)
    return REPO_URL


def scrub(text):
    return text.replace(GITHUB_TOKEN, "***") if GITHUB_TOKEN else text


def run_git(*args, check=True, cwd=None):
    """All git calls go through here so the token never reaches logs or the browser."""
    proc = subprocess.run(["git", *args], cwd=cwd or WORKDIR, capture_output=True,
                          text=True, timeout=180)
    if check and proc.returncode != 0:
        raise RuntimeError(scrub(f"git {args[0]} failed: {proc.stderr.strip() or proc.stdout.strip()}"))
    return scrub(proc.stdout)


def ensure_clone():
    """Clone the repo into WORKDIR if it isn't there yet (e.g. after a cold start)."""
    with CLONE_LOCK:
        if not (WORKDIR / ".git").exists():
            WORKDIR.parent.mkdir(parents=True, exist_ok=True)
            run_git("clone", "--depth", "20", "--branch", BRANCH, authenticated_url(),
                    str(WORKDIR), cwd=WORKDIR.parent)


def reset_to_main():
    """Make WORKDIR an exact copy of the current main (cloning first if needed)."""
    ensure_clone()
    run_git("remote", "set-url", "origin", authenticated_url())
    run_git("fetch", "--depth", "20", "origin", BRANCH)
    run_git("checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}")
    run_git("reset", "-q", "--hard", f"origin/{BRANCH}")
    run_git("clean", "-q", "-fd")


def changed_files():
    out = run_git("status", "--porcelain", "--untracked-files=all")
    files = []
    for line in out.splitlines():
        path = line[3:].strip().strip('"')
        if path:
            files.append(path)
    return sorted(files)


def push_main():
    """Push HEAD to main; if main moved meanwhile, rebase once and retry."""
    try:
        run_git("push", "origin", f"HEAD:{BRANCH}")
    except RuntimeError:
        run_git("fetch", "--depth", "20", "origin", BRANCH)
        try:
            run_git("rebase", f"origin/{BRANCH}")
        except RuntimeError:
            run_git("rebase", "--abort", check=False)
            raise RuntimeError("Someone else changed the same part of the site while you "
                               "were working. Discard this change and try your request again.")
        run_git("push", "origin", f"HEAD:{BRANCH}")
    return run_git("rev-parse", "--short", "HEAD").strip()


def last_main_commit():
    """(sha, subject, body) of the newest commit on main, read from the last fetch."""
    out = run_git("log", "-1", "--format=%h%x00%s%x00%b", f"origin/{BRANCH}")
    sha, subject, body = (out.split("\x00") + ["", ""])[:3]
    return sha.strip(), subject.strip(), body


# --------------------------------------------------------------------------- file tools

class ToolError(Exception):
    pass


def page_path(name, must_exist=True):
    name = (name or "").strip().lstrip("./")
    if not PAGE_NAME_RE.match(name):
        raise ToolError(f"'{name}' isn't a page you can edit. Only top-level .html pages are allowed.")
    if name in PROTECTED_PAGES:
        raise ToolError(f"{name} is a redirect to the CAASPP dashboard app and can't be edited here.")
    path = WORKDIR / name
    if must_exist and not path.is_file():
        raise ToolError(f"{name} doesn't exist. Use list_pages to see the available pages.")
    return path


def editable_pages():
    return sorted(p.name for p in WORKDIR.glob("*.html")
                  if PAGE_NAME_RE.match(p.name) and p.name not in PROTECTED_PAGES)


def show_line(i, line):
    if len(line) > LONG_LINE:
        line = line[:LONG_LINE] + f" …[{len(line) - LONG_LINE:,} more characters on this line not shown]"
    return f"{i:>5}\t{line}"


def tool_list_pages(_):
    rows = []
    for name in editable_pages():
        text = (WORKDIR / name).read_text(encoding="utf-8", errors="replace")
        m = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
        rows.append(f"{name}\t{text.count(chr(10)) + 1} lines\t{(m.group(1).strip() if m else '')}")
    return "\n".join(rows)


def tool_read_page(args):
    path = page_path(args.get("page"))
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    start = max(1, int(args.get("start_line") or 1))
    end = min(len(lines), int(args.get("end_line") or start + 299), start + 399)
    body = "\n".join(show_line(i, lines[i - 1]) for i in range(start, end + 1))
    more = f"\n[lines {start}-{end} of {len(lines)}]"
    return body + more


def tool_search(args):
    needle = (args.get("text") or "").lower()
    if not needle:
        raise ToolError("Give some text to search for.")
    pages = [args["page"]] if args.get("page") else editable_pages()
    hits = []
    for name in pages:
        path = page_path(name)
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            pos = line.lower().find(needle)
            if pos >= 0:
                snippet = line if len(line) <= 300 else "…" + line[max(0, pos - 120):pos + 180] + "…"
                hits.append(f"{name}:{i}: {snippet.strip()}")
                if len(hits) >= 60:
                    return "\n".join(hits) + "\n[stopped at 60 matches; narrow the search]"
    return "\n".join(hits) or "No matches."


def tool_edit_page(args):
    path = page_path(args.get("page"))
    old, new = args.get("old_text", ""), args.get("new_text", "")
    if not old:
        raise ToolError("old_text can't be empty.")
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise ToolError("old_text wasn't found. Copy it exactly from read_page (without the line numbers).")
    if count > 1:
        raise ToolError(f"old_text appears {count} times. Include more surrounding text so it's unique.")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"Edited {path.name}."


def tool_create_page(args):
    path = page_path(args.get("page"), must_exist=False)
    if path.exists():
        raise ToolError(f"{path.name} already exists. Use edit_page to change it.")
    content = args.get("content") or ""
    if "<html" not in content.lower():
        raise ToolError("A new page needs to be a complete HTML document.")
    path.write_text(content, encoding="utf-8")
    return f"Created {path.name}."


TOOLS = {
    "list_pages": tool_list_pages,
    "read_page": tool_read_page,
    "search": tool_search,
    "edit_page": tool_edit_page,
    "create_page": tool_create_page,
}

TOOL_SPECS = [
    {"name": "list_pages", "description": "List the site's editable pages with line counts and titles.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_page",
     "description": "Read a range of lines from a page (max 400 per call). Lines are numbered; "
                    "very long lines (embedded data or images) are cut off.",
     "input_schema": {"type": "object", "properties": {
         "page": {"type": "string"}, "start_line": {"type": "integer"},
         "end_line": {"type": "integer"}}, "required": ["page"]}},
    {"name": "search",
     "description": "Case-insensitive text search across all pages (or one page). Returns page:line: snippet.",
     "input_schema": {"type": "object", "properties": {
         "text": {"type": "string"}, "page": {"type": "string"}}, "required": ["text"]}},
    {"name": "edit_page",
     "description": "Replace one exact piece of text in a page. old_text must appear exactly once.",
     "input_schema": {"type": "object", "properties": {
         "page": {"type": "string"}, "old_text": {"type": "string"},
         "new_text": {"type": "string"}}, "required": ["page", "old_text", "new_text"]}},
    {"name": "create_page",
     "description": "Create a NEW top-level page (complete HTML document). Can't overwrite existing pages.",
     "input_schema": {"type": "object", "properties": {
         "page": {"type": "string", "description": "e.g. events.html"},
         "content": {"type": "string"}}, "required": ["page", "content"]}},
]

ACTIVITY = {
    "list_pages": lambda a: "Looking over the site's pages",
    "read_page": lambda a: f"Reading {a.get('page')}",
    "search": lambda a: f"Searching for “{(a.get('text') or '')[:60]}”",
    "edit_page": lambda a: f"Editing {a.get('page')}",
    "create_page": lambda a: f"Creating {a.get('page')}",
}

SYSTEM_PROMPT = """You edit the Intentional Tech in Schools website (a parent group's advocacy site \
about technology in the San Mateo-Foster City School District) on behalf of a non-technical editor, \
and answer their questions about it. \
The site is hand-written static HTML: each top-level .html page is self-contained, with its own \
<style> block and :root color variables, and its own copy of the header/navigation and footer.

Questions or changes:
- The editor may ask a question (for example "which pages aren't linked from any other page?" or \
"what does the FAQ say about screen time?") or ask for a change. For a question, use the read and \
search tools to answer it and make no edits. Only edit when they clearly ask for a change. If \
you can't tell whether they want a change, answer and ask rather than editing.
- Earlier messages in this conversation still apply, so "do that" or "undo the last part" refer \
back to them.

How to make changes:
- Start by finding the right place (search is usually fastest), read the surrounding lines, then \
make the smallest edit that does what was asked. Don't restyle, reorganize or "improve" anything \
that wasn't requested.
- Match the page's existing look: reuse its existing CSS classes and :root variables rather than \
adding new colors, fonts or inline styles.
- Navigation and footers are repeated on every page. If the request changes them (for example, \
adding a new page to the menu), update every page that has that menu so they stay consistent.
- To add a new page, read an existing page of a similar kind and copy its structure and styles, \
then use create_page.
- Some lines hold huge embedded data or images and are shown cut off. Never edit those lines.
- Write in plain, direct language that matches the site's tone. Avoid em dashes in new text.
- Don't invent facts, figures, dates, quotes or links. If the request needs information you don't \
have, make the change with what you were given and say what's missing.
- The CAASPP dashboard is a separate app and can't be changed here. If asked to change it, \
or anything else outside these pages, make no edits and explain.
- If a change request is unclear, make your best reasonable interpretation; the editor will see \
a preview and can ask for a tweak.

When you're done, reply in plain English. After a change: 1-3 short sentences saying what you \
changed and on which page(s), plus anything they should check in the preview. After a question: \
answer it directly; short lists are fine (one item per line, starting with "- "). Never say you \
changed something unless you did. No code, no HTML, no file jargon beyond page names."""


# --------------------------------------------------------------------------- checks

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr", "param"}
WATCHED = {"html", "head", "body", "main", "header", "footer", "nav", "section", "article",
           "div", "script", "style", "table", "ul", "ol", "form", "a", "p", "details"}


class TagCounter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.balance = {}

    def handle_starttag(self, tag, attrs):
        if tag in WATCHED:
            self.balance[tag] = self.balance.get(tag, 0) + 1

    def handle_endtag(self, tag):
        if tag in WATCHED:
            self.balance[tag] = self.balance.get(tag, 0) - 1


def tag_balance(text):
    parser = TagCounter()
    parser.feed(text)
    parser.close()
    return {k: v for k, v in parser.balance.items() if v}


def check_changes(originals):
    """Compare each changed page with its original. Returns plain-English problems."""
    problems = []
    for name in changed_files():
        path = WORKDIR / name
        if not path.exists():
            problems.append(f"{name} was removed.")
            continue
        new = path.read_text(encoding="utf-8", errors="replace")
        old = originals.get(name)
        if old is None:  # new page
            if tag_balance(new):
                problems.append(f"{name} has unclosed or extra tags: {tag_balance(new)}")
            continue
        if tag_balance(new) != tag_balance(old):
            problems.append(f"{name}: opening and closing tags no longer match "
                            f"(before {tag_balance(old) or 'balanced'}, after {tag_balance(new) or 'balanced'}).")
        if len(new) < 0.7 * len(old):
            problems.append(f"{name} shrank by more than 30%, which is more than a normal edit.")
        if "</html>" in old.lower() and "</html>" not in new.lower():
            problems.append(f"{name} lost its closing </html> tag.")
    return problems


# --------------------------------------------------------------------------- Claude loop

def anthropic_client():
    import anthropic
    return anthropic.Anthropic(max_retries=3, timeout=180)


CLIENT_FACTORY = anthropic_client  # tests swap this for a fake


def run_tool(name, args):
    note(ACTIVITY.get(name, lambda a: name)(args))
    try:
        return TOOLS[name](args), False
    except ToolError as exc:
        return str(exc), True
    except Exception as exc:  # noqa: BLE001 - report anything else back to the model
        return f"Error: {exc}", True


def clean_block(block):
    """Turn an SDK content block into the plain dict we send back on the next turn."""
    b = block.model_dump(exclude_none=True) if hasattr(block, "model_dump") else dict(block)
    if b.get("type") == "text":
        return {"type": "text", "text": b.get("text", "")}
    if b.get("type") == "tool_use":
        return {"type": "tool_use", "id": b["id"], "name": b["name"], "input": b.get("input") or {}}
    return b  # e.g. thinking blocks, which must be passed back unchanged


def converse(client, messages):
    """Run the tool loop until Claude stops. Returns its final text."""
    for _ in range(MAX_TOOL_ROUNDS):
        resp = client.messages.create(model=MODEL, max_tokens=16000, system=SYSTEM_PROMPT,
                                      tools=TOOL_SPECS, messages=messages)
        content = [clean_block(block) for block in resp.content]
        messages.append({"role": "assistant", "content": content})
        if resp.stop_reason != "tool_use":
            return "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        results = []
        for block in content:
            if block.get("type") == "tool_use":
                output, is_error = run_tool(block["name"], block.get("input") or {})
                results.append({"type": "tool_result", "tool_use_id": block["id"],
                                "content": output, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    raise RuntimeError("This request took too many steps. Try breaking it into smaller requests.")


def draft_snapshot():
    """Contents of every page that differs from main, to tell which ones a turn touched."""
    return {n: (WORKDIR / n).read_bytes() for n in changed_files() if (WORKDIR / n).is_file()}


def edit_job(change_id, editor, prompt, fresh, viewing=None):
    """One turn of an editor's conversation: a question (no edits) or a change. fresh means
    there's no draft to build on, so start from a fresh copy of main."""
    with LOCK:
        c = convo(editor)
        messages, notes = c["messages"], c["notes"][:]
        c["notes"].clear()
    base = len(messages)
    originals = None
    try:
        if fresh:
            note("Getting the latest version of the site")
            reset_to_main()
        originals = {n: (WORKDIR / n).read_text(encoding="utf-8", errors="replace")
                     for n in editable_pages()}
        # Originals must be main's versions, not an earlier round's edits.
        for name in changed_files():
            try:
                originals[name] = run_git("show", f"HEAD:{name}")
            except RuntimeError:
                originals.pop(name, None)
        before = draft_snapshot()

        context = []
        if notes:
            context.append("[Since your last reply: " + " ".join(notes) + " Pages may have "
                           "changed, so read them again before editing.]")
        if viewing:
            context.append(f"[The editor is looking at {viewing} in the preview, so \"this page\" "
                           "means that page.]")
        content = "\n".join(context + [prompt]) if context else prompt
        messages.append({"role": "user", "content": content})
        client = CLIENT_FACTORY()
        reply = converse(client, messages)

        problems = check_changes(originals)
        if problems:
            note("Double-checking the HTML")
            messages.append({"role": "user", "content":
                             "An automatic check found problems with your edits. Fix them without "
                             "changing anything else, then give the editor your summary again:\n- "
                             + "\n- ".join(problems)})
            reply = converse(client, messages)
            problems = check_changes(originals)

        after = draft_snapshot()
        turn_changed = sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))
        changed = changed_files()
        with LOCK:
            c["thread"].append({"role": "claude", "text": reply or "Done.",
                                "changed": turn_changed})
            if STATE["id"] != change_id:
                return  # discarded while running
            if turn_changed:
                STATE["change_requests"].append(prompt)
                STATE["change_summary"] = reply
            finish(changed, problems)
    except Exception as exc:  # noqa: BLE001
        # Drop this turn's half-finished messages (only ever the end of the history) so the
        # conversation can carry on.
        del messages[base:]
        with LOCK:
            c["notes"][:0] = notes  # still unsent
            c["thread"].append({"role": "error", "text": scrub(str(exc))})
            if STATE["id"] != change_id:
                return
            if originals is None and fresh:
                # Couldn't get a fresh copy of the site, so the working copy can't be trusted.
                # Nothing to publish; the next message retries.
                reset_state()
                return
            try:
                changed, warnings = changed_files(), check_changes(originals or {})
            except Exception:  # noqa: BLE001
                changed, warnings = [], []
            finish(changed, warnings)


def finish(changed, warnings):
    """After a turn: keep the draft waiting if pages changed; otherwise free the working copy
    for anyone. Call with LOCK held."""
    if changed:
        STATE.update(status="ready", changed=changed, warnings=warnings)
    else:
        reset_state()


# --------------------------------------------------------------------------- auth

def current_editor():
    return session.get("editor")


@app.before_request
def require_login():
    if request.endpoint in ("login", "static", "health"):
        return None
    if not current_editor():
        if request.path.startswith("/api/"):
            return jsonify(error="Please sign in again."), 401
        return redirect(url_for("login"))
    if request.method == "POST" and request.headers.get("X-Requested-With") != "site-editor":
        abort(403)  # simple CSRF guard: only our own page's fetch() calls set this header
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        code = (request.form.get("passcode") or "").strip()
        name = next((n for c, n in PASSCODES.items() if hmac.compare_digest(c, code)), None)
        if name:
            session.permanent = True
            session["editor"] = name
            return redirect(url_for("home"))
        time.sleep(1.5)
        error = "That passcode didn't work."
    return render_template("login.html", error=error, configured=bool(PASSCODES))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/healthz")
def health():
    return "ok"


# --------------------------------------------------------------------------- pages & API

@app.route("/")
def home():
    return render_template("index.html", editor=current_editor(), site_url=SITE_URL)


def public_state():
    you = current_editor()
    with LOCK:
        s = {k: STATE[k] for k in ("status", "editor", "changed", "warnings", "error", "started")}
        s["activity"] = list(STATE["activity"]) if STATE["editor"] == you else []
        s["thread"] = list(convo(you)["thread"])  # always your own conversation
    s["you"] = you
    s["banner"] = LAST_RESULT["message"]
    return s


@app.route("/api/status")
def status():
    return jsonify(public_state())


@app.route("/api/submit", methods=["POST"])
def submit():
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    viewing = (body.get("viewing") or "").strip()
    if not (PAGE_NAME_RE.match(viewing) and viewing not in PROTECTED_PAGES):
        viewing = None  # the page shown in the preview, if it's one Claude can work with
    if not prompt:
        return jsonify(error="Type what you'd like to change."), 400
    if len(prompt) > 4000:
        return jsonify(error="That request is too long. Try splitting it up."), 400
    editor = current_editor()
    with LOCK:
        status_ = STATE["status"]
        if status_ in ("running", "publishing"):
            return jsonify(error=f"{STATE['editor']} is working on something. Try again in a minute."), 409
        if status_ == "ready" and STATE["editor"] != editor:
            return jsonify(error=f"{STATE['editor']} has a change waiting to be published or discarded."), 409
        # With your own draft waiting, build on it; otherwise start from the latest main.
        # Your conversation carries on either way, and nobody else's is touched.
        fresh = status_ != "ready"
        change_id = uuid.uuid4().hex if fresh else STATE["id"]
        if fresh:
            reset_state()
            STATE.update(id=change_id, editor=editor,
                         started=datetime.now(timezone.utc).isoformat())
        STATE["status"] = "running"
        STATE["error"] = ""
        STATE["activity"].append(f"Request: {prompt[:200]}")
        convo(editor)["thread"].append({"role": "you", "text": prompt})
        LAST_RESULT["message"] = ""
    threading.Thread(target=edit_job, args=(change_id, editor, prompt, fresh, viewing),
                     daemon=True).start()
    return jsonify(public_state())


@app.route("/api/discard", methods=["POST"])
def discard():
    """Throw away the unpublished draft but keep the conversation, or with {"new": true},
    end the conversation too (New conversation / Start over)."""
    new = bool((request.get_json(silent=True) or {}).get("new"))
    you = current_editor()
    with LOCK:
        who, had_changes = STATE["editor"], STATE["status"] == "ready"
        busy = STATE["status"] in ("running", "publishing")
        if busy and (who == you or not new):
            return jsonify(error="Wait for the current step to finish."), 409
        if new:
            # Your conversation only; someone else's draft stays put.
            CONVOS.pop(you, None)
            if who == you:
                reset_state()
            LAST_RESULT["message"] = ""
        else:
            reset_state()
            if had_changes:
                tell(who, "Unpublished changes discarded" + ("" if you == who else f" by {you}") + ".",
                     "The unpublished changes were discarded, so the site is back to the "
                     "published version.")
            LAST_RESULT["message"] = (f"Discarded {who}'s change. Nothing was published."
                                      if had_changes else "")
    return jsonify(public_state())  # outside the lock: public_state() takes it too


@app.route("/api/publish", methods=["POST"])
def publish():
    editor = current_editor()
    with LOCK:
        if STATE["status"] != "ready":
            return jsonify(error="There's nothing ready to publish."), 409
        if STATE["editor"] != editor:
            return jsonify(error=f"Only {STATE['editor']} can publish their change."), 403
        if STATE["warnings"]:
            return jsonify(error="This change didn't pass the automatic check. Ask for a fix or discard it."), 409
        STATE["status"] = "publishing"
        requests_ = list(STATE["change_requests"]) or ["(no request recorded)"]
        summary, files = STATE["change_summary"], list(STATE["changed"])
    try:
        title = summary.split("\n")[0].strip() or requests_[0]
        title = (title[:68] + "…") if len(title) > 70 else title
        body = (f"Requested by {editor} through the site editor:\n\n"
                + "\n".join(f"> {r}" for r in requests_)
                + f"\n\nPages: {', '.join(files)}\n\n{COMMIT_TRAILER}\nRequested-By: {editor}")
        run_git("add", "--", *files)
        run_git("-c", f"user.name={editor} (site editor)", "-c", f"user.email={BOT_EMAIL}",
                "commit", "-q", "-m", title, "-m", body)
        sha = push_main()
    except Exception as exc:  # noqa: BLE001
        with LOCK:
            STATE["status"] = "ready"
        return jsonify(error=scrub(str(exc))), 500
    with LOCK:
        reset_state()
        tell(editor, f"Published ({sha}). Your next message builds on the live site.",
             f"The editor published your changes to {', '.join(files)}; they're live now.")
        LAST_RESULT["message"] = (f"Published ({sha}). The live site updates in about 2–5 minutes; "
                                  "refresh it after that.")
    return jsonify(public_state())


@app.route("/api/last-change")
def last_change():
    with LOCK:
        if STATE["status"] in ("running", "publishing"):
            return jsonify(available=False, reason="busy")
    try:
        ensure_clone()
        run_git("fetch", "--depth", "20", "origin", BRANCH)
        sha, subject, body = last_main_commit()
    except Exception as exc:  # noqa: BLE001
        return jsonify(available=False, reason=scrub(str(exc)))
    by = re.search(r"^Requested-By: (.+)$", body, re.M)
    return jsonify(available=COMMIT_TRAILER in body, sha=sha, subject=subject,
                   by=by.group(1) if by else "Andy (outside this tool)")


@app.route("/api/undo", methods=["POST"])
def undo():
    editor = current_editor()
    with LOCK:
        if STATE["status"] != "idle":
            return jsonify(error="Publish or discard the change in progress first."), 409
        STATE.update(status="publishing", editor=editor)
    try:
        reset_to_main()
        sha, subject, body = last_main_commit()
        if COMMIT_TRAILER not in body:
            raise RuntimeError("The latest change wasn't made with this tool, so it can't be undone here.")
        run_git("-c", f"user.name={editor} (site editor)", "-c", f"user.email={BOT_EMAIL}",
                "revert", "--no-edit", "HEAD")
        run_git("-c", f"user.name={editor} (site editor)", "-c", f"user.email={BOT_EMAIL}",
                "commit", "--amend", "-q", "-m", f"Undo: {subject}"[:72],
                "-m", f"Reverts {sha}. Undone by {editor} through the site editor.\n\nRequested-By: {editor}")
        new_sha = push_main()
        msg = f"Undid “{subject}” ({new_sha}). The live site updates in about 2–5 minutes."
        ok = True
    except Exception as exc:  # noqa: BLE001
        msg, ok = scrub(str(exc)), False
    with LOCK:
        reset_state()
        if ok:
            tell(editor, f"Undid “{subject}”.",
                 f"The editor undid the most recent published change (“{subject}”), so the "
                 "live site is back to how it was before it.")
            LAST_RESULT["message"] = msg
    return (jsonify(public_state()) if ok else (jsonify(error=msg), 500))


@app.route("/api/diff")
def diff():
    with LOCK:
        if STATE["status"] != "ready":
            return jsonify(diff="")
    chunks = []
    for name in changed_files():
        try:
            old = run_git("show", f"HEAD:{name}").split("\n")
        except RuntimeError:
            old = []
        new = (WORKDIR / name).read_text(encoding="utf-8", errors="replace").split("\n")
        for line in difflib.unified_diff(old, new, f"before/{name}", f"after/{name}", n=2, lineterm=""):
            chunks.append(line if len(line) <= 400 else line[:400] + " …")
    return jsonify(diff="\n".join(chunks[:3000]))


def viewable(subpath):
    allowed = (PAGE_NAME_RE.match(subpath) and subpath not in PROTECTED_PAGES) or \
        re.match(r"^videos/[\w./-]+$", subpath)
    if not allowed or ".." in subpath:
        abort(404)


def no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/pages")
def pages():
    """Editable pages on main, for the viewer's page picker."""
    try:
        ensure_clone()
        out = run_git("ls-tree", "--name-only", f"origin/{BRANCH}")
    except Exception as exc:  # noqa: BLE001
        return jsonify(error=scrub(str(exc))), 500
    names = [n for n in out.split() if PAGE_NAME_RE.match(n) and n not in PROTECTED_PAGES]
    return jsonify(pages=sorted(names))


@app.route("/preview/<path:subpath>")
def preview(subpath):
    """The working copy, including the change in progress."""
    with LOCK:
        if STATE["status"] not in ("ready", "running"):
            abort(409)
    viewable(subpath)
    return no_store(send_from_directory(WORKDIR, subpath))


@app.route("/current/<path:subpath>")
def current(subpath):
    """The site as it is on main (as of the last fetch), ignoring any change in progress.
    Pages come from git, since the working copy may hold someone's draft; videos are never
    edited, so they're served straight from the working copy."""
    viewable(subpath)
    ensure_clone()
    if subpath.startswith("videos/"):
        return no_store(send_from_directory(WORKDIR, subpath))
    try:
        html = run_git("show", f"origin/{BRANCH}:{subpath}")
    except RuntimeError:
        abort(404)
    return no_store(Response(html, mimetype="text/html"))


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8070")), debug=False, threaded=True)
