'use client';

// Root-layout error boundary (Next.js App Router convention). `error.tsx`
// only catches errors thrown *below* the root layout — if `layout.tsx`
// itself (or anything providers it mounts, e.g. AppProvider/ToastProvider)
// throws during render, only `global-error.tsx` can catch it, and to do so
// it must render its own <html>/<body> since it replaces the root layout
// entirely while active. It intentionally does NOT depend on
// globals.css/Tailwind or any app component — those are exactly what may
// have failed to mount — so everything here is inline-styled and
// dependency-free.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '16px',
          padding: '32px',
          textAlign: 'center',
          backgroundColor: '#000',
          color: '#e5e5e5',
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
        }}
      >
        <div>
          <h1 style={{ fontSize: '18px', fontWeight: 500, margin: 0 }}>
            Trainable failed to load
          </h1>
          <p style={{ marginTop: '8px', maxWidth: '420px', fontSize: '14px', color: '#a3a3a3' }}>
            Something went wrong before the app could start. Try again, or reload the page if it
            keeps happening.
          </p>
          {error.message && (
            <p
              style={{
                marginTop: '12px',
                maxWidth: '480px',
                wordBreak: 'break-word',
                borderRadius: '6px',
                border: '1px solid rgba(255,255,255,0.08)',
                backgroundColor: 'rgba(255,255,255,0.04)',
                padding: '8px 12px',
                fontFamily: 'ui-monospace, monospace',
                fontSize: '12px',
                color: '#737373',
              }}
            >
              {error.message}
            </p>
          )}
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            type="button"
            onClick={() => reset()}
            style={{
              borderRadius: '8px',
              border: 'none',
              backgroundColor: '#4f46e5',
              color: '#fff',
              padding: '8px 16px',
              fontSize: '14px',
              cursor: 'pointer',
            }}
          >
            Try again
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{
              borderRadius: '8px',
              border: '1px solid rgba(255,255,255,0.08)',
              backgroundColor: 'rgba(255,255,255,0.06)',
              color: '#d4d4d4',
              padding: '8px 16px',
              fontSize: '14px',
              cursor: 'pointer',
            }}
          >
            Reload page
          </button>
        </div>
      </body>
    </html>
  );
}
