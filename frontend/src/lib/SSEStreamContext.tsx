'use client';

import { createContext, useCallback, useContext, useMemo, useRef, type ReactNode } from 'react';
import type { SSEEvent } from './types';

type SSEListener = (event: SSEEvent) => void;

interface SSEStreamState {
  /** Subscribe to every parsed message from the single page-level session
   *  EventSource (`HomePage.connectSSE`, `/api/sessions/{id}/stream`).
   *  Returns an unsubscribe function. Consumers that need the same events
   *  (e.g. the notebook) should use this instead of opening a second
   *  EventSource to the identical endpoint. */
  subscribe: (listener: SSEListener) => () => void;
  /** Internal: invoked by `connectSSE` for every parsed message so
   *  subscribers stay in sync with the single connection. Not meant to be
   *  called by consumers of the stream. */
  publish: (event: SSEEvent) => void;
}

const SSEStreamContext = createContext<SSEStreamState | null>(null);

export function useSSEStream(): SSEStreamState {
  const ctx = useContext(SSEStreamContext);
  if (!ctx) throw new Error('useSSEStream must be used within SSEStreamProvider');
  return ctx;
}

export function SSEStreamProvider({ children }: { children: ReactNode }) {
  const listenersRef = useRef<Set<SSEListener>>(new Set());

  const subscribe = useCallback((listener: SSEListener) => {
    listenersRef.current.add(listener);
    return () => {
      listenersRef.current.delete(listener);
    };
  }, []);

  const publish = useCallback((event: SSEEvent) => {
    listenersRef.current.forEach((listener) => listener(event));
  }, []);

  const value = useMemo<SSEStreamState>(() => ({ subscribe, publish }), [subscribe, publish]);

  return <SSEStreamContext.Provider value={value}>{children}</SSEStreamContext.Provider>;
}
