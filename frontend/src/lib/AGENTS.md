# AGENTS.md — frontend/src/lib

Global state, API client, types, and shared utilities.

## Layout

```
AppContext.tsx     The single global store — providers, experiments, projects, agent overrides
api.ts             Typed API client — every backend endpoint has a method here
types.ts           TypeScript types matching the backend schemas
mentions.ts        @-mention parsing for the input box
useFileTree.ts     File tree hook used by S3Browser and SessionFiles
notebook/          Notebook-related helpers
```

## AppContext — the global store

`AppContext` is the **only** global state container. Everything app-wide goes here: active project, experiments list, models, providers, per-agent overrides, sidebar state, running flag.

```tsx
const { activeProjectId, experiments, refreshExperiments, agentModels, setAgentModel } = useApp();
```

Rules:
1. **One context, one provider.** If you find yourself adding a second `createContext`, ask whether it really belongs in AppContext.
2. **Local state stays local.** If only one component reads it, it stays in that component. Context is for cross-cutting state.
3. **Persist to localStorage at the boundary**, not inside reducers. `useEffect` writes; the reducer is pure.
4. **Refresh functions return the new data.** `refreshExperiments()` returns the fetched list — callers can use it directly without waiting for a re-render.
5. **Selective subscriptions are not implemented.** Every consumer re-renders when context changes. If this becomes a bottleneck, switch to Zustand or jotai — don't sprinkle `useMemo` on consumers.

## What goes in AppContext

✅ Active project / experiment / session IDs.
✅ Lists fetched once and shared across views (experiments, models, providers).
✅ Per-agent UI preferences (model override, thinking level).
✅ Global flags (sidebar open, running indicator).

❌ Chat messages — they're page-level, owned by the studio.
❌ Form state — owned by the form.
❌ Animation state — owned by the animating component.
❌ SSE events — fan out via reducers, not raw events in context.

## API client (`api.ts`)

Every backend route has a typed method:

```ts
export const api = {
  experiments: {
    list: (): Promise<Experiment[]> => http.get('/api/experiments'),
    create: (body: CreateExperimentPayload): Promise<CreateExperimentResponse> =>
      http.post('/api/experiments', body),
    delete: (id: string): Promise<DeleteResponse> => http.delete(`/api/experiments/${id}`),
  },
  // ...
};
```

Rules:
- **No `fetch()` outside this file.** All HTTP goes through `api.*`.
- **Methods are typed in and out.** Use types from `types.ts`.
- **Errors throw.** `http.get` wraps fetch; non-2xx throws a typed `ApiError` with status + message. Callers `try/catch`.
- **Base URL from env**: `process.env.NEXT_PUBLIC_API_URL`. Fall back to `''` for relative paths.
- **No query string concatenation by hand.** Use `URLSearchParams`.
- **Don't add a method that doesn't have a backend route yet.** That creates dead client code.

## Types (`types.ts`)

Types match backend Pydantic schemas one-for-one.

```ts
export interface Experiment {
  id: string;
  name: string;
  hypothesis: string;
  state: 'created' | 'prepping' | 'training' | 'trained' | 'failed' | 'abandoned';
  created_at: string;  // ISO timestamp
  // ...
}
```

Rules:
- **Mirror the backend.** Field names match snake_case from the API. Don't transform on read — let TypeScript do the work.
- **Enums as string literal unions.** `state: 'created' | 'trained'`, not `enum`. Easier to debug, narrower at the use site.
- **Dates as ISO strings.** Parse only when you need to display. Store as string.
- **Optional fields use `?`, not `| null`** unless the backend can actually return null (some can — keep it accurate).
- **Update types in the same PR as the backend schema.** Type drift is a major source of "why doesn't the UI show this" bugs.

## Hooks

- **One hook per file.** `useFileTree.ts` exports `useFileTree`.
- **Hooks own their cleanup.** SSE, intervals, listeners — return cleanup from `useEffect`.
- **Hooks don't render.** A hook returns state + setters; the component renders.

## Common pitfalls

- **Adding state to AppContext that only one component reads.** That's component-local state.
- **Calling `api.*` inside a reducer.** Reducers are pure. Async work happens in event handlers or effects.
- **Forgetting to refresh after mutation.** `api.experiments.create(...)` → also call `refreshExperiments()` or the UI is stale.
- **Hardcoding the API URL.** Use the env var.
- **Inlining type definitions.** If `Props` has a backend-shaped object, the type belongs in `types.ts`, not the component file.
- **Caching responses in AppContext forever.** Use it as a freshness layer (re-fetch on focus, after mutation), not a permanent cache.

## Adding a new global piece of state

1. Decide: **does this really belong here?** (See the "what goes in AppContext" lists.)
2. Add the field to the `AppState` interface in `AppContext.tsx`.
3. Add the setter / refresher function.
4. Persist to localStorage if it should survive a reload.
5. Update `types.ts` if the data is backend-sourced.
6. Use it via `useApp()` in consuming components.

## Before you ship

- [ ] AppState interface updated if state was added
- [ ] localStorage persistence wired if cross-reload
- [ ] Types in `types.ts` match backend schema exactly
- [ ] All API access goes through `api.ts`
- [ ] No new direct `fetch` calls
- [ ] Refresh functions called after mutations
