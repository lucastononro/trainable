# AGENTS.md — frontend

Next.js 14 (app router), React 18, TypeScript strict. Talks to the backend over REST + SSE.

## Layout

```
src/app/             Next.js app router pages (one folder per route)
  page.tsx           Root — the studio split-pane (chat + canvas)
  experiments/       Experiment detail view
  models/            Model registry + deploy
  compare/           Multi-experiment compare
  projects/          Project gallery
  usage/             Cost dashboard
  layout.tsx         Root layout (providers, fonts, globals)
src/components/      React components — see components/AGENTS.md
src/lib/             AppContext, API client, types — see lib/AGENTS.md
public/              Static assets (logos, favicons)
```

## Core principles

1. **`'use client'` is the default for stateful components.** Server components are great for static layout, but anything reactive (chat, canvas, modals) needs to be client. Don't fight Next.js.
2. **No hydration mismatches.** Any component reading `localStorage`, `window`, `document`, or `Date.now()` at render time must gate the read with `useEffect` or use `dynamic({ ssr: false })`. We have shipped hydration mismatches; they look like random rendering bugs and are hard to debug.
3. **Build clean before shipping.** `npm run build` must pass locally. ESLint warnings (`<img>` instead of `<Image>`, missing deps in `useEffect`) are not optional — they've reached the user before.
4. **One global store: `AppContext`.** Don't proliferate stores. If something belongs in global state, it goes there. If something is local to a component, it stays local.
5. **API access goes through `src/lib/api.ts`.** No `fetch()` calls scattered through components.
6. **Display names, never IDs.** The user sees `xgb_baseline`, not `7f3a-...`. IDs go in the URL and in dev-tools.
7. **Screenshot-first for UI changes.** Before editing, reference the design (issue mockup, screenshot). After editing, load the dev server and verify against that reference. We've burned weeks on "ugly UI / redo" loops that a 30-second browser check would have caught.

## Streaming (SSE)

- **Subscribe once per session.** The `useSession` hook in `src/lib/` opens one EventSource and dispatches events to the right reducers.
- **Reconnect on disconnect.** Browsers close idle connections; the hook auto-reconnects with backoff.
- **Don't poll.** If you find yourself adding a `setInterval` to fetch the same endpoint, the broadcaster should be pushing that event instead — add it to the backend.

## State location decision tree

- **Persists across page reloads** (active project, agent model preferences) → `AppContext` + `localStorage`.
- **Lives for the session** (chat scroll position, modal open state) → component-local `useState`.
- **Fetched and shared across views** (experiments, models, providers) → `AppContext`, refreshed on demand.
- **Derived from props** → don't store; recompute.

## Layout patterns

- **Split-pane**: studio uses a draggable split between chat (left) and canvas (right). The split width persists. When you change a layout, reconcile both the initial state and the resize handler — we've had bugs where the manual setting and the auto-pop didn't match.
- **Sidebar**: collapsible, default-open desktop / default-collapsed mobile. State lives in `AppContext.sidebarOpen`.
- **Modals**: portal-based, escape-to-close, click-outside-to-close. Use `ConfirmModal` for destructive actions.
- **Empty states**: every list view has one. *"No experiments yet — upload a dataset to start."* Beats a blank screen.

## Common pitfalls

- **Importing a server-only module in a client component.** Next.js errors loudly; don't ignore it.
- **Using `<img>` instead of `next/image`.** ESLint catches it; CI catches it; fix it before pushing.
- **Mutating state with `useState` setters from inside a render.** React 18 strict mode catches it in dev; fix at the source.
- **Reading `localStorage` directly during SSR.** Wrap in `useEffect` and provide a default.
- **Coupling a component to AppContext when props would do.** Passing data via context locks the component out of being reused in a different page. Default to props.
- **Subscribing to SSE in a deeply nested component.** Subscribe at the page level, fan out via context or props. Multiple subscriptions cost CPU and complicate cleanup.
- **Storing API responses in component state instead of context.** Two components fetch the same data twice; mutations don't propagate.

## Performance

- **Virtualize long lists.** A session with 500 messages renders all 500 by default — virtualize with a windowing library if you hit perf walls. Don't preemptively though.
- **Memoize expensive renders.** `React.memo` for components that take stable props and re-render often (sidebar items, message bubbles).
- **Defer non-critical work.** Heavy components (Monaco editor, lineage canvas, code highlighting) load via `dynamic(import, { ssr: false })`.
- **Don't bundle prismjs grammars eagerly.** Load per-language on demand.

## Frontend ↔ Backend contract

- **Types come from `src/lib/types.ts`.** When the backend changes a schema, update types in the same PR. Type drift causes UI bugs that look like data bugs.
- **API base URL is configurable.** Don't hardcode `localhost:8000`; use `process.env.NEXT_PUBLIC_API_URL`.
- **Cost/usage numbers are computed backend-side.** Don't multiply in the frontend — pricing changes, and we want one source.

## Before you ship

- [ ] `npm run build` clean (no errors, no warnings on changed files)
- [ ] `npm run lint` clean
- [ ] No hydration mismatch in dev console
- [ ] Loaded the page in a browser; clicked through the change
- [ ] If UI work: compared against the reference screenshot
- [ ] Types in `src/lib/types.ts` match the backend schema
- [ ] No new `setInterval` polling (use SSE)
- [ ] No new direct `fetch` calls (use `src/lib/api.ts`)
