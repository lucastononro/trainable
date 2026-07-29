'use client';

import { memo, useEffect, useState } from 'react';
import { AlertCircle, ExternalLink, Globe, Loader2 } from 'lucide-react';
import type { HtmlArtifact } from '@/lib/types';
import { api } from '@/lib/api';

// ---------------------------------------------------------------------------
// HtmlPanel -- renders an agent-published HTML artifact in a sandboxed
// iframe. Used both by the canvas_html tab type and the FileViewer .html
// branch. The iframe deliberately omits `allow-same-origin`: scripts
// inside still run (Plotly/D3 interactivity works), but they can't read
// cookies, fetch the API as the user, or postMessage usefully to the
// parent. The backend pairs this with a strict CSP on /files/raw for
// text/html responses (no outbound connect-src; img-src/script-src
// allow-listed).
// ---------------------------------------------------------------------------

function humanArtifactBytes(n: number | null | undefined): string | null {
  if (n == null) return null;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const HtmlPanel = memo(function HtmlPanel({ artifact }: { artifact: HtmlArtifact | undefined }) {
  const [loadFailed, setLoadFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setLoadFailed(false);
    setLoaded(false);
  }, [artifact?.path]);

  if (!artifact) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-600 bg-black">
        <Globe className="w-8 h-8 mb-2 text-gray-700" />
        <p className="text-xs">HTML artifact not loaded yet.</p>
      </div>
    );
  }

  const rawUrl = api.filesRawUrl(artifact.path);
  const sizeLabel = humanArtifactBytes(artifact.size);

  return (
    <div className="h-full flex flex-col bg-black">
      <div className="flex items-center justify-between gap-3 px-4 h-8 border-b border-white/[0.06] shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Globe className="w-3.5 h-3.5 shrink-0 text-fuchsia-400" />
          <span className="text-[12px] text-gray-300 truncate">{artifact.title}</span>
          {sizeLabel && <span className="text-[11px] text-gray-600 shrink-0">· {sizeLabel}</span>}
        </div>
        <a
          href={rawUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-300 transition-colors shrink-0"
          title="Open this artifact in a new tab"
        >
          <ExternalLink className="w-3 h-3" />
          Open in new tab
        </a>
      </div>
      <div className="flex-1 overflow-hidden relative bg-white">
        {loadFailed ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-700 bg-black">
            <AlertCircle className="w-7 h-7 mb-2 text-amber-500" />
            <p className="text-sm text-gray-400">HTML artifact failed to load.</p>
            <a
              href={rawUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-3 text-[12px] text-fuchsia-400 hover:text-fuchsia-300 underline"
            >
              Download raw HTML
            </a>
          </div>
        ) : (
          <iframe
            // Keys the iframe on the artifact path so regenerating an artifact
            // with the same key forces a fresh load instead of a cached view.
            key={artifact.path}
            src={rawUrl}
            sandbox="allow-scripts allow-pointer-lock allow-popups"
            referrerPolicy="no-referrer"
            loading="lazy"
            style={{ colorScheme: 'dark' }}
            className="w-full h-full bg-white border-0"
            title={artifact.title}
            onLoad={() => setLoaded(true)}
            onError={() => setLoadFailed(true)}
          />
        )}
        {!loaded && !loadFailed && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/40">
            <Loader2 className="w-5 h-5 text-gray-400 animate-spin" />
          </div>
        )}
      </div>
    </div>
  );
});

export default HtmlPanel;
