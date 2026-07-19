'use client';

import { memo, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ErrorBoundary from '@/components/ErrorBoundary';

// ---------------------------------------------------------------------------
// ReportMarkdown -- the canvas report tab body. Memoized so parent renders
// triggered by chat / SSE / task ticks don't re-parse the markdown tree.
// ---------------------------------------------------------------------------

const ReportMarkdown = memo(function ReportMarkdown({
  content,
  sessionId,
}: {
  content: string;
  sessionId: string;
}) {
  // Stable `components` map: only rebuilt when sessionId changes (effectively
  // never within a session). Without useMemo the inline `img` lambda would
  // be a fresh ref each render and ReactMarkdown would re-key its tree.
  const components = useMemo(
    () => ({
      img: ({ src, alt }: { src?: string; alt?: string }) => {
        let imgSrc = src || '';
        if (imgSrc.startsWith('/data/')) {
          imgSrc = `/api/files/raw?path=${encodeURIComponent(imgSrc)}`;
        } else if (imgSrc && !imgSrc.startsWith('http')) {
          const workspace = `/sessions/${sessionId}/eda`;
          imgSrc = `/api/files/raw?path=${encodeURIComponent(workspace + '/' + imgSrc)}`;
        }
        return (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={imgSrc} alt={alt || ''} className="max-w-full rounded-lg shadow-md my-4" />
        );
      },
    }),
    [sessionId],
  );
  return (
    <div className="h-full overflow-y-auto p-6 bg-black">
      <div className="markdown-content">
        <ErrorBoundary label="this report">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
            {content}
          </ReactMarkdown>
        </ErrorBoundary>
      </div>
    </div>
  );
});

export default ReportMarkdown;
