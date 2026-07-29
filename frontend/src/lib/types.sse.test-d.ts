// Compile-time tests for the SSEEvent discriminated union (PR #157).
//
// This file is never imported at runtime (Next only bundles reachable
// modules) but IS type-checked by both `npx tsc --noEmit` and `next build`
// (tsconfig includes `**/*.ts`), so a regression in the union's narrowing
// behavior fails the build. No test runner or extra deps required.
//
// Each `Expect<...>` line errors at compile time if the assertion is false.

import type {
  SSEEvent,
  ChartConfigSSEData,
  ChartConfigEntry,
  MetricEventData,
  ToolStartData,
  StateChangeData,
} from './types';

type Expect<T extends true> = T;
type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
type IsAssignable<A, B> = [A] extends [B] ? true : false;

/** The `data` payload the union associates with a given event `type`. */
type DataFor<K extends SSEEvent['type']> = Extract<SSEEvent, { type: K }>['data'];

export type SSEEventTypeTests = [
  // Narrowing on `type` yields the mapped payload type.
  Expect<Equal<DataFor<'chart_config'>, ChartConfigSSEData>>,
  Expect<Equal<DataFor<'metric'>, MetricEventData>>,
  Expect<Equal<DataFor<'tool_start'>, ToolStartData>>,
  Expect<Equal<DataFor<'state_change'>, StateChangeData>>,

  // `chart_config` uses the dedicated WIRE type: `charts` is optional, so
  // the defensive `data.charts && Array.isArray(data.charts)` guard in
  // page.tsx is load-bearing, not dead code. A malformed/empty payload must
  // remain assignable, and the property must include `undefined`.
  Expect<Equal<ChartConfigSSEData['charts'], ChartConfigEntry[] | undefined>>,
  Expect<IsAssignable<{ type: 'chart_config'; data: Record<never, never> }, SSEEvent>>,

  // Events with an unknown `type` are rejected by the union (the whole
  // point of the discriminated union — no silent `any` passthrough).
  Expect<
    Equal<
      IsAssignable<{ type: 'definitely_not_an_event'; data: Record<never, never> }, SSEEvent>,
      false
    >
  >,
];
