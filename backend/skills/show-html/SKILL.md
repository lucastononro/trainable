---
name: show-html
description: Surface a self-contained HTML page (optionally with companion JS/CSS in the same folder) on the workspace canvas. The agent saves the file(s) anywhere under its session workspace, then calls this skill to open a sandboxed iframe tab for the user.
when_to_use: Use when you want the user to see an interactive HTML/JS composition you authored — a Plotly/Bokeh dashboard, a ydata-profiling report, an edge-case explorer, a side-by-side comparison view, a bespoke single-page app illustrating a result. Static results that fit a markdown report or a scalar chart should stay there; reach for show-html only when interactivity is the point.
version: '0.1'
kind: capability
---

# show-html

Publish an HTML artifact on the canvas. The agent's job is to write the
HTML to its session workspace (via `execute-code` or any other write
path); this skill registers it, fires the SSE event the UI listens to,
and persists a system message so reload restores the tab.

## Why this is a skill, not an SDK helper

HTML artifacts are arbitrary user-facing presentations — they have no
schema, no step axis, no metric semantics. They live alongside the
markdown report and the metrics dashboard, not inside them. Keeping
them as an explicit tool call (rather than a magic stdout envelope from
inside `trainable`) means: the agent is conscious about which artifact
warrants its own canvas tab, and the artifact lifecycle is the same as
any other deliverable on the volume.

## Inputs

- `path` (required): volume path to the HTML entry file (e.g.
  `/sessions/<session_id>/canvas/myviz/index.html`). MUST be under your
  session workspace (`/sessions/<session_id>/`) — paths outside are
  rejected. The file must already exist on the volume and be under
  10 MB.
- `title` (optional): tab label. Defaults to the filename stem.
- `key` (optional): stable identifier for this artifact. Pass the same
  `key` on regeneration to overwrite the existing tab instead of
  stacking a new one. Defaults to the filename stem.

## Companion files (JS / CSS / images)

The iframe is served from `/api/files/raw?path=…` on the backend
origin. Because the CSP allows `'self'`, your HTML can pull in
sibling files via absolute URLs that hit the same endpoint:

```html
<link rel="stylesheet"
      href="/api/files/raw?path=/sessions/<session_id>/canvas/myviz/styles.css" />
<script src="/api/files/raw?path=/sessions/<session_id>/canvas/myviz/chart.js"></script>
<img src="/api/files/raw?path=/sessions/<session_id>/figures/sample.png" />
```

You can also:
- Inline `<script>...</script>` / `<style>...</style>` blocks (CSP allows
  `'unsafe-inline'`).
- Load Plotly / Bokeh / D3 from CDN — `cdn.plot.ly`, `cdn.bokeh.org`,
  `d3js.org`, `cdnjs.cloudflare.com` are allow-listed.
- Reference images by `data:` / `blob:` URI.

What you can't do (by design): `fetch()` or `XMLHttpRequest`. The CSP
sets `connect-src 'none'`, and the iframe sandbox does NOT include
`allow-same-origin` — so the page cannot beacon out, cannot read app
cookies, cannot call our API as the user. Bake any data into the page
at generation time.

## Worked example — Plotly figure (single file)

```python
# Inside execute-code:
import plotly.express as px, os
os.makedirs(f"/data/sessions/{session_id}/canvas", exist_ok=True)
fig = px.scatter(df, x="confidence", y="loss", color="correct")
fig.write_html(
    f"/data/sessions/{session_id}/canvas/conf_vs_loss.html",
    include_plotlyjs="cdn",
    full_html=True,
)
```

Then:

```text
show-html(
  path="/sessions/<session_id>/canvas/conf_vs_loss.html",
  title="Confidence vs loss (val set)",
  key="conf_vs_loss",
)
```

## Worked example — HTML + companion JS

```python
# Inside execute-code:
canvas = f"/data/sessions/{session_id}/canvas/edge_cases"
os.makedirs(canvas, exist_ok=True)
with open(f"{canvas}/data.js", "w") as f:
    f.write(f"window.ROWS = {json.dumps(rows)};")
with open(f"{canvas}/render.js", "w") as f:
    f.write(open("render_template.js").read())
with open(f"{canvas}/index.html", "w") as f:
    f.write(f'''<!doctype html>
<html><head><meta charset="utf-8"><title>Edge cases</title></head>
<body>
  <div id="root"></div>
  <script src="/api/files/raw?path=/sessions/{session_id}/canvas/edge_cases/data.js"></script>
  <script src="/api/files/raw?path=/sessions/{session_id}/canvas/edge_cases/render.js"></script>
</body></html>''')
```

Then:

```text
show-html(
  path="/sessions/<session_id>/canvas/edge_cases/index.html",
  title="Edge-case explorer",
  key="edge_cases",
)
```

## When NOT to use this

- A single line / bar / area chart of training-time scalars → use the
  `trainable.log(...)` SDK helper; the metrics dashboard renders it.
- A single image, image grid, table, or confusion matrix → use
  `trainable.log_image` / `log_images` / `log_table` /
  `log_confusion_matrix` — those get first-class panels with step scrubbing.
- A text-first narrative with a few inline figures → write a markdown
  `report.md` at the workspace root.

## Failure modes

- `path` outside the session workspace → rejected (403-like error from
  the skill).
- File doesn't exist or isn't readable → rejected.
- File larger than 10 MB → rejected with a hint to split the page or
  externalize large assets.
- `path` not ending in `.html` → rejected (the iframe expects HTML).

## Returns

```json
{
  "key": "conf_vs_loss",
  "title": "Confidence vs loss (val set)",
  "path": "/sessions/<sid>/canvas/conf_vs_loss.html",
  "size": 4242
}
```
