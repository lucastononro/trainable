'use client';

import { Component, type ReactNode } from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Rendered instead of `children` once a render error is caught. Receives
   *  the error and a `reset` callback that clears the boundary's error
   *  state and re-renders `children` — useful when the error was
   *  transient or the underlying data (e.g. a chat item, a workspace tab)
   *  has since changed. Falls back to a generic panel when omitted. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
  /** Short noun used in the generic fallback's headline, e.g. "the
   *  workspace" or "this message". Ignored when `fallback` is provided. */
  label?: string;
  /** Called once when a render error is caught, e.g. to log/report it. */
  onError?: (error: Error, info: { componentStack: string }) => void;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Generic React error boundary. Untyped/unvalidated SSE payloads and
 * live-rendered agent-authored markdown + self-contained HTML artifacts
 * mean a render throw is a real risk — without a boundary, one bad payload
 * takes down the entire SPA with a blank screen (React unmounts the whole
 * tree above the nearest boundary, and the root layout has none). Wrap the
 * workspace canvas and individual markdown renderers with this so a crash
 * is contained to that panel/bubble instead.
 *
 * Must be a class component — there is no hook equivalent for
 * `getDerivedStateFromError`/`componentDidCatch` as of React 18.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error('ErrorBoundary caught a render error:', error, info.componentStack);
    this.props.onError?.(error, info);
  }

  reset = () => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) return this.props.fallback(error, this.reset);

    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <AlertCircle className="w-6 h-6 text-red-400 shrink-0" />
        <div>
          <p className="text-sm text-gray-300">
            {this.props.label
              ? `Couldn't render ${this.props.label}.`
              : 'Something went wrong rendering this.'}
          </p>
          <p className="mt-1 max-w-md break-words font-mono text-xs text-gray-500">
            {error.message}
          </p>
        </div>
        <button
          type="button"
          onClick={this.reset}
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-white/[0.06] px-3 py-1.5 text-xs text-gray-300 transition-colors hover:bg-white/[0.1]"
        >
          <RefreshCw className="w-3 h-3" /> Try again
        </button>
      </div>
    );
  }
}
