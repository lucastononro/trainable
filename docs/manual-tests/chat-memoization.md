# Manual test: chat item memoization & streaming (PR #150)

PR #150 memoizes `ChatItemView` (frontend/src/app/page.tsx) and hoists the
`remarkGfm` plugin array so markdown bubbles stop re-parsing on every streamed
token. Each item also receives a per-item `isStreaming` boolean (not the shared
streaming-item id), so when a stream starts or ends only the affected bubble
re-renders. There is no frontend unit-test infrastructure in this repo, so
verify the behavior manually as follows.

## Setup

1. Start the stack (backend + frontend) as usual, e.g. `docker compose up`
   or `cd frontend && npm ci && npm run dev` against a running backend.
2. Open the app in Chrome and open a session with an existing chat history
   (or create one and send a few messages first, including at least one
   assistant reply containing markdown: headings, a table, a code block).
3. Open Chrome DevTools → install/enable **React Developer Tools** →
   Profiler tab → gear icon → check **"Record why each component rendered"**
   and enable **"Highlight updates when components render"** in the settings.

## Test 1 — only the streaming bubble re-renders per token

1. Start Profiler recording.
2. Send a new message and let the assistant stream a long reply.
3. Stop recording after the stream ends.

**Expected:**
- During token updates, render highlights flash only around the streaming
  assistant bubble, not around earlier chat bubbles.
- In the Profiler flamegraph, prior `ChatItemView` instances show
  "Did not render" / memo bailout for the token-update commits.
- At the stream **start** and **end** commits, only the streaming item
  re-renders (its `isStreaming` prop flips); other items still bail out —
  this is the per-item boolean fix from the Greptile review.

## Test 2 — markdown is not re-parsed in old bubbles

1. With a history containing a large markdown reply (table + code block),
   start streaming a new reply.
2. In the Profiler, select the old markdown bubble's `ChatItemView`.

**Expected:** it reports no renders during the stream. If it re-rendered
every token (the pre-PR behavior), `ReactMarkdown` would re-parse its content
each time — visible as jank/CPU in the Performance tab on long histories.

## Test 3 — streaming caret behaves correctly

1. Watch the streaming assistant bubble while tokens arrive.

**Expected:**
- A blinking caret is shown at the end of the streaming bubble only.
- When the stream finishes, the caret disappears from that bubble.
- No other bubble ever shows a caret.

## Test 4 — no visual/behavioral regressions

Quickly sanity-check every chat item type still renders:
user messages (incl. file pills and @-mentions), assistant markdown
(GFM tables/strikethrough/task lists must still render — this exercises the
hoisted `CHAT_MARKDOWN_PLUGINS`), tool cards, sub-agent cards, clarification
cards, and error items. Scroll through an existing long session to confirm.
