# Site editor

A small web app that lets trusted, non-technical editors change the Intentional Tech in
Schools site without GitHub, Claude accounts, or Andy.

1. An editor signs in with their own passcode and types a request in plain English.
2. Claude edits the site's pages in a fresh copy of `main` and explains what it changed.
3. The editor checks a live preview and can ask for tweaks. A panel on the right always
   shows the site: the current published version until a change is ready, then the draft.
4. **Publish** commits to `main` (Render then deploys the site in a few minutes), or
   **Discard** throws the change away.
5. **Undo this change** reverts the most recent published change, if this tool made it.

## What it can and can't touch

- Only top-level `.html` pages. Not `caaspp_ai_dashboard/`, `caaspp_files/`, `analysis/`,
  `videos/`, `site-editor/`, config files, or the three CAASPP redirect pages
  (`caaspp-2025-26.html`, `reclassification-explained.html`, `smfc_caaspp_dashboard.html`).
- Claude has five tools: list pages, read part of a page, search, replace one exact snippet,
  and create a brand-new page. There's no shell, and it can't delete or overwrite a whole page.
- An automatic check compares opening/closing tags before and after each edit. If the edit
  breaks the HTML, Claude is asked to fix it; if it still fails, Publish is blocked.
- One change at a time. Only the person who started a change can publish it; anyone signed
  in can discard an abandoned one.
- Commits are authored as "<Name> (site editor)" and include the original requests plus a
  `Site-Editor: yes` trailer (that trailer is how Undo knows it may revert a commit).

## Set up on Render (one time)

1. **GitHub token.** github.com → Settings → Developer settings → Fine-grained tokens →
   Generate. Repository access: only `aatwal/intentional_tech`. Permissions: Contents →
   Read and write. Set an expiry and a reminder to rotate it.
2. **Anthropic API key.** console.anthropic.com → API keys. Put a monthly spend limit on the
   workspace (a typical edit costs a few cents to ~$0.30).
3. **New Web Service** from this repo:
   - Root Directory: `site-editor`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT`
     (exactly one worker: the in-progress change lives in memory)
   - Build Filters → Included Paths: `site-editor/**`, so editors' publishes don't restart it.
   - Instance type: Starter or above. The free tier sleeps and its first request is slow.
4. **Environment variables:**

   | Variable | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | from step 2 |
   | `GITHUB_TOKEN` | from step 1 |
   | `EDITOR_PASSCODES` | `Suki:some-long-random-code,Uma:another-long-code` (8+ characters each) |
   | `FLASK_SECRET_KEY` | any long random string (keeps people signed in across restarts) |
   | `MODEL` | optional, default `claude-sonnet-5-5` |
   | `SITE_URL` | optional, default `https://www.intentionaltechinschools.com/` |

   To add or remove an editor, edit `EDITOR_PASSCODES` and save (Render restarts the app).

5. Send each editor the `onrender.com` URL and their passcode.

## Running locally

```bash
cd site-editor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-... EDITOR_PASSCODES="Andy:local-test-code" INSECURE_COOKIES=1
# Point at a throwaway copy, never the real repo, when testing:
git clone --bare .. /tmp/test-origin.git
export REPO_URL=/tmp/test-origin.git SITE_EDITOR_WORKDIR=/tmp/site-editor-work
python app.py   # http://localhost:8070
```

`INSECURE_COOKIES=1` is only for plain-http local testing.

## Notes for maintainers

- All git calls go through `run_git()`, which removes the token from any error text. Keep it
  that way when adding git commands.
- `reset_to_main()` re-fetches and hard-resets the working copy before every new change, so
  Render's ephemeral disk and cold starts are fine.
- If `main` moves while someone is editing, Publish rebases once; on a conflict it asks the
  editor to discard and redo.
- The system prompt in `app.py` (`SYSTEM_PROMPT`) holds the site conventions Claude follows.
  Update it if the site's structure changes.
- The editable-page rules are `PAGE_NAME_RE` and `PROTECTED_PAGES` in `app.py`.
