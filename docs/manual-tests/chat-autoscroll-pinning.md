# Manual test tutorial — chat auto-scroll pinning (PR #152 / issue #99)

The frontend has no unit-test harness, and the behavior under test (browser
smooth-scroll animation, `scroll` events, streaming SSE tokens) cannot be
reproduced in jsdom anyway. Verify the scroll-pinning behavior manually with
this checklist in a real browser.

## Setup

1. Start the app (`docker compose up` or backend + `cd frontend && npm run dev`).
2. Open the chat page and start a session.
3. Send a few prompts that produce **long streaming replies** (e.g.
   "Explain gradient descent in 2000 words") until the chat pane has enough
   history to scroll (scrollbar visible).

## Test 1 — pinned follow (baseline)

1. Scroll to the very bottom of the chat pane.
2. Send a prompt with a long streaming reply.
3. **Expected:** the view stays glued to the bottom for the whole stream;
   every new token is visible without touching the scrollbar.

## Test 2 — scrollback is not hijacked (the original #99 bug)

1. Send a prompt with a long streaming reply.
2. While tokens are still streaming, scroll **up** several screens and stop.
3. **Expected:** the view stays exactly where you put it; it does NOT jump
   back to the bottom on each token. You can read old messages undisturbed
   until the stream finishes.

## Test 3 — re-pin by scrolling back down

1. Continue from Test 2 (still streaming, scrolled up).
2. Scroll back down to the bottom (within ~96 px of it).
3. **Expected:** auto-follow resumes; the view sticks to the bottom for the
   rest of the stream.

## Test 4 — sending a message re-pins instantly (the Greptile P1 fix)

1. With plenty of history, scroll far **up** in the pane (several screens).
2. Type a new prompt and hit send.
3. **Expected:**
   - The view jumps **instantly** to the bottom (no smooth animation
     gliding down — a smooth animation is exactly what used to un-pin the
     view mid-flight).
   - Your new user bubble is visible at the bottom.
   - When the assistant's first tokens arrive (even if they arrive
     immediately), the view **follows the stream to the end**. It must not
     get stuck at an intermediate scroll position.

Repeat Test 4 a few times with a fast-responding model; the failure mode it
guards against was a race between the smooth-scroll animation and
first-token latency, so it was timing-dependent.

## Test 5 — threshold sanity

1. During a stream, scroll up just a tiny nudge (well under ~96 px from the
   bottom). **Expected:** still pinned; the view keeps following.
2. Scroll up more than ~96 px. **Expected:** un-pinned; the view stops
   following (Test 2 behavior).
