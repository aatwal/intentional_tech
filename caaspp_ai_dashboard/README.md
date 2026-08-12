# CAASPP Dashboard: AI Chat Edition

Same 10-year Smarter Balanced dashboard as `../smfc_caaspp_dashboard.html`
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

The full 10-year dataset (`data/caaspp_data.json`, extracted from the same
source as the static dashboard) is loaded once at startup. On each chat turn,
the server does **not** send the whole dataset to Claude: that's ~4.5MB of
JSON, too large and too expensive per message. Instead it builds a compact
plain-text summary scoped to whatever subject/school/grade/student-group is
currently selected in the dashboard (the same numbers the charts are already
showing, plus a cross-school ranking for context) and sends only that summary
as the system prompt. This keeps answers grounded in real numbers, keeps
requests small, and means the chat's scope follows the filters: asking about
a different school/grade means changing the filter first.

## Regenerating `data/caaspp_data.json`

If the static dashboard's embedded data is ever updated, re-extract it:

```bash
cd ..
python3 -c "
import json
with open('smfc_caaspp_dashboard.html', encoding='utf-8') as f:
    for line in f:
        if line.startswith('const DATA = '):
            raw = line[len('const DATA = '):].rstrip('\n').rstrip(';')
            json.dump(json.loads(raw), open('caaspp_ai_dashboard/data/caaspp_data.json', 'w'))
            break
"
```

Note `templates/index.html` in this directory also carries its own copy of
that same embedded `DATA` blob (for the charts, which run client-side same as
the static version); keep the two in sync if you regenerate one.
