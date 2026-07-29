'use client';

import { useState } from 'react';
import { AlertTriangle, Check, Info, OctagonAlert, Wand2 } from 'lucide-react';
import type { EdaFinding } from '@/lib/types';

// ---------------------------------------------------------------------------
// EdaFindingsPanel — canvas action cards for structured EDA findings
// (issue #111). Each card carries the finding type, affected columns, and a
// concrete recommendation; "Apply in prep" hands the recommendation to the
// chat input, pre-filled as a data_prep instruction for the orchestrator.
// ---------------------------------------------------------------------------

const TYPE_LABEL: Record<string, string> = {
  leakage: 'Leakage risk',
  class_imbalance: 'Class imbalance',
  high_cardinality: 'High cardinality',
  multicollinearity: 'Multicollinearity',
  missing_values: 'Missing values',
  outliers: 'Outliers',
  duplicates: 'Duplicates',
  skewed_target: 'Skewed target',
  id_column: 'ID-like column',
  constant_column: 'Constant column',
  datetime_leakage: 'Datetime leakage',
  other: 'Finding',
};

const SEVERITY_STYLE: Record<
  EdaFinding['severity'],
  { border: string; badge: string; icon: typeof Info }
> = {
  critical: {
    border: 'border-rose-500/30',
    badge: 'bg-rose-500/15 text-rose-300',
    icon: OctagonAlert,
  },
  warning: {
    border: 'border-amber-500/25',
    badge: 'bg-amber-500/15 text-amber-300',
    icon: AlertTriangle,
  },
  info: {
    border: 'border-sky-500/25',
    badge: 'bg-sky-500/15 text-sky-300',
    icon: Info,
  },
};

function FindingCard({
  finding,
  onApplyInPrep,
}: {
  finding: EdaFinding;
  onApplyInPrep: (finding: EdaFinding) => void;
}) {
  const [applied, setApplied] = useState(false);
  const style = SEVERITY_STYLE[finding.severity] ?? SEVERITY_STYLE.warning;
  const SeverityIcon = style.icon;

  return (
    <div className={`rounded-xl border ${style.border} bg-surface p-3.5 flex flex-col gap-2`}>
      <div className="flex items-center gap-2">
        <SeverityIcon className="w-4 h-4 shrink-0 text-gray-400" />
        <span className="text-sm font-medium text-gray-100">
          {TYPE_LABEL[finding.finding_type] || TYPE_LABEL.other}
        </span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded-md uppercase tracking-wider ${style.badge}`}>
          {finding.severity}
        </span>
      </div>

      {finding.columns.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {finding.columns.map((c) => (
            <span
              key={c}
              className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-white/[0.06] text-gray-300"
            >
              {c}
            </span>
          ))}
        </div>
      )}

      <p className="text-xs text-gray-300 leading-relaxed">{finding.summary}</p>

      <div className="text-xs text-gray-400 leading-relaxed">
        <span className="text-gray-500 uppercase tracking-wider text-[10px] mr-1.5">Recommend</span>
        {finding.recommendation}
      </div>

      <div className="mt-1">
        <button
          onClick={() => {
            onApplyInPrep(finding);
            setApplied(true);
          }}
          className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
            applied
              ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
              : 'bg-primary-600 hover:bg-primary-500 text-white'
          }`}
          title="Pre-fill the chat with a data_prep instruction for this recommendation"
        >
          {applied ? (
            <>
              <Check className="w-3.5 h-3.5" /> Added to prompt
            </>
          ) : (
            <>
              <Wand2 className="w-3.5 h-3.5" /> Apply in prep
            </>
          )}
        </button>
      </div>
    </div>
  );
}

export function EdaFindingsPanel({
  findings,
  onApplyInPrep,
}: {
  findings: EdaFinding[];
  onApplyInPrep: (finding: EdaFinding) => void;
}) {
  // Critical first, then warnings, then info — stable within each bucket.
  const order = { critical: 0, warning: 1, info: 2 } as const;
  const sorted = [...findings].sort(
    (a, b) => (order[a.severity] ?? 1) - (order[b.severity] ?? 1),
  );

  return (
    <div className="h-full overflow-y-auto bg-black p-4">
      <div className="mb-3 text-xs text-gray-500">
        Structured findings from the EDA pass. “Apply in prep” pre-fills the chat with a
        data-prep instruction — review it, then send.
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        {sorted.map((f) => (
          <FindingCard
            key={`${f.finding_type}:${f.columns.join(',')}:${f.summary}`}
            finding={f}
            onApplyInPrep={onApplyInPrep}
          />
        ))}
      </div>
    </div>
  );
}
