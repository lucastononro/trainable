'use client';

import { useEffect } from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';

// Route-level error boundary (Next.js App Router convention: a file named
// `error.tsx` automatically wraps its route segment — here, the whole app,
// since this lives directly under `src/app/`). Catches render errors thrown
// anywhere in the tree below it that aren't already contained by a nested
// boundary (see `src/components/ErrorBoundary.tsx`, used around the
// workspace canvas and chat markdown renderers) and shows a recoverable
// screen instead of Next's default error overlay / a blank page.
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('Route-level error boundary caught:', error);
  }, [error]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-black p-8 text-center">
      <AlertCircle className="w-10 h-10 text-red-400" />
      <div>
        <h1 className="text-lg font-medium text-gray-100">Something went wrong</h1>
        <p className="mt-2 max-w-md text-sm text-gray-400">
          Trainable hit an unexpected error. You can try again, or reload the page if it keeps
          happening.
        </p>
        {error.message && (
          <p className="mt-3 max-w-lg break-words rounded-md border border-white/[0.08] bg-white/[0.04] px-3 py-2 font-mono text-xs text-gray-500">
            {error.message}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => reset()}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-2 text-sm text-white transition-colors hover:bg-primary-500"
        >
          <RefreshCw className="w-3.5 h-3.5" /> Try again
        </button>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-white/[0.06] px-4 py-2 text-sm text-gray-300 transition-colors hover:bg-white/[0.1]"
        >
          Reload page
        </button>
      </div>
    </div>
  );
}
