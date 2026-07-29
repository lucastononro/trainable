# AGENTS.md — frontend/src/components

React components. One concern per file.

## Naming

- **PascalCase filenames** matching the default export: `Sidebar.tsx` exports `Sidebar`.
- **Component name = file name.** Don't name a file `Sidebar.tsx` and export `SideNav`.
- **Subdirectories for component families**: `lineage/`, `notebook/`. Inside, a `index.tsx` re-exports the public API.

## Component shape

```tsx
'use client';

import { useState } from 'react';
import type { Experiment } from '@/lib/types';

interface Props {
  experiment: Experiment;
  onSelect: (id: string) => void;
}

export function ExperimentCard({ experiment, onSelect }: Props) {
  // 1. Hooks at the top, in order: state, refs, context, effects.
  // 2. Derived values (no extra state).
  // 3. Handlers.
  // 4. Return JSX.
}
```

Rules:
- **Props interface lives in the same file.** Don't extract to `types.ts` unless reused.
- **Default export only for page components.** Everything else is a named export — easier to refactor, easier to grep.
- **No anonymous components.** `function ExperimentCard` beats `export default ({ ... }) =>`. Stack traces and devtools need names.

## Props design

1. **Take the smallest input that works.** If the component only needs `experiment.name` and `experiment.id`, take those, not the whole object. Wide props couple components to schema.
2. **Callbacks named `onX`**: `onSelect`, `onDelete`, `onSubmit`. Past-tense for things that already happened, present-tense for intent.
3. **No "magic" boolean props.** `variant="primary" | "ghost"` beats `primary={true} ghost={false}`.
4. **Children for layout, props for data.** A modal takes `children`; a card takes `experiment`.

## What goes in `components/` vs `app/`

- **`app/<route>/page.tsx`**: page-level orchestration. Fetches data, owns layout, composes components.
- **`components/`**: reusable UI. Doesn't fetch (gets data via props or context).
- **`components/<family>/`**: when a feature has multiple related components (lineage canvas needs `LineageNode`, `LineageEdge`, `LineageMinimap`), group under a folder.

If a component is used by exactly one page, it can still live in `components/` if it's a recognizable UI unit. But if it's tightly coupled to the page's state, just inline it in the page file.

## Displaying agent state

The agent has many states (idle, running, done, failed). The UI surfaces this through:
- **`StatusIcon`**: pure icon. Pass `state` prop.
- **`AgentStatusIndicator`**: full row with name + state + last action.
- **`InlineTasks`**: the per-session task panel.

When you add a new agent state, update **all three** in the same PR. We've shipped states where the icon updated but the indicator didn't.

## Modals

- **All modals use the `ConfirmModal` shell** for consistency: same close button, same focus trap, same escape-handler.
- **Confirm before destructive actions.** Delete experiment, abort session, drop dataset — all go through `ConfirmModal` with explicit copy.
- **Modals own their open state when locally triggered; AppContext when globally triggered.** Project settings → context. Confirm-delete-this-item → local.

## ID vs name (again, because it matters)

- **Display**: name.
- **URL params**: id.
- **Aria labels and titles**: name + " (" + truncated id + ")" if disambiguation is needed.
- **Logs**: id.
- **Dev tools**: both.

Don't render `experiment.id` in a heading. The user doesn't know what `7f3a-...` is.

## Animation

- **CSS transitions for layout** (sidebar collapse, modal fade, canvas slide). Use `transition` properties; no JS animation libraries for these.
- **`@xyflow/react` for graph animations** (lineage canvas). The library handles physics; don't replicate it.
- **Spinner = `<Loader2 className="animate-spin" />`** from lucide-react. Don't ship a custom spinner per component.

## Common pitfalls

- **Component fetches on mount, parent also fetches on mount.** Pick one. Usually the parent owns the data, passes via props.
- **`useEffect` with no cleanup.** SSE subscriptions, intervals, event listeners — always return a cleanup function.
- **Conditional hooks.** `if (foo) { useState(...) }` is a React rules violation. Hooks always top-level, always same order.
- **Inline styles for one-off values that should be CSS classes.** If you find yourself writing `style={{ color: theme.accent }}` repeatedly, hoist to a class.
- **Rendering `null` as a child unexpectedly.** Conditional `{cond && <X />}` returns `false` if `cond` is `false`, but `{x && <X />}` where `x` is `0` renders `0`. Use `{x ? <X /> : null}`.
- **Imports from `@/lib/api` in a server component.** The API client uses `fetch` with credentials — keep it client-side.

## Before you ship

- [ ] Component renders without devtools warnings
- [ ] No hydration mismatch
- [ ] Props typed; no `any`
- [ ] Loading + error states handled
- [ ] Empty state handled (if applicable)
- [ ] Accessibility: keyboard navigation works, focus is trapped in modals
- [ ] Displayed text uses `name`, not `id`
