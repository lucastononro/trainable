'use client';

import type { CellOutput } from '@/lib/notebook/types';

function stripAnsi(s: string): string {
  // eslint-disable-next-line no-control-regex
  return s.replace(/\x1b\[[0-9;]*m/g, '');
}

function joinText(t: string | string[]): string {
  return Array.isArray(t) ? t.join('') : t;
}

interface Props {
  outputs: CellOutput[];
}

export default function CellOutputs({ outputs }: Props) {
  if (!outputs || outputs.length === 0) return null;
  return (
    <div className="border-t border-neutral-800 bg-neutral-950/60">
      {outputs.map((o, i) => (
        <OutputBlock key={i} output={o} />
      ))}
    </div>
  );
}

function OutputBlock({ output }: { output: CellOutput }) {
  if (output.output_type === 'stream') {
    const cls =
      output.name === 'stderr' ? 'text-rose-400' : 'text-neutral-200';
    return (
      <pre
        className={`whitespace-pre-wrap break-words px-4 py-2 font-mono text-xs ${cls}`}
      >
        {joinText(output.text)}
      </pre>
    );
  }
  if (output.output_type === 'error') {
    const tb = output.traceback
      .map(stripAnsi)
      .join('\n');
    return (
      <pre className="whitespace-pre-wrap break-words px-4 py-2 font-mono text-xs text-rose-400">
        {tb || `${output.ename}: ${output.evalue}`}
      </pre>
    );
  }
  // display_data | execute_result
  const data = output.data || {};
  const png = data['image/png'];
  const html = data['text/html'];
  const plain = data['text/plain'];

  if (png) {
    const src = `data:image/png;base64,${joinText(png)}`;
    return (
      <div className="px-4 py-2">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={src} alt="output" className="max-w-full" />
      </div>
    );
  }
  if (html) {
    // Sandbox iframe — DataFrame HTML, rich reprs, etc.
    const srcdoc = joinText(html);
    return (
      <iframe
        title="cell-html-output"
        sandbox=""
        srcDoc={`<html><head><style>
          body{margin:0;padding:8px;font:12px/1.4 -apple-system,Segoe UI,sans-serif;color:#e5e5e5;background:transparent;}
          table{border-collapse:collapse;}
          th,td{padding:4px 8px;border:1px solid #333;}
          th{background:#1f2937;}
        </style></head><body>${srcdoc}</body></html>`}
        className="w-full min-h-[80px] bg-neutral-950"
      />
    );
  }
  if (plain) {
    return (
      <pre className="whitespace-pre-wrap break-words px-4 py-2 font-mono text-xs text-neutral-200">
        {joinText(plain)}
      </pre>
    );
  }
  return null;
}
