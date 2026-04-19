'use client';

import type { KernelState } from '@/lib/notebook/types';

interface Props {
  state: KernelState;
  onStart: () => void;
  onInterrupt: () => void;
  onShutdown: () => void;
}

const LABELS: Record<KernelState, string> = {
  starting: 'Starting kernel…',
  idle: 'Idle',
  busy: 'Running',
  dead: 'Kernel off',
};

const DOT_CLS: Record<KernelState, string> = {
  starting: 'bg-amber-400 animate-pulse',
  idle: 'bg-emerald-500',
  busy: 'bg-amber-400 animate-pulse',
  dead: 'bg-neutral-500',
};

export default function KernelStatusBadge({ state, onStart, onInterrupt, onShutdown }: Props) {
  return (
    <div className="flex items-center gap-2 text-xs text-neutral-300">
      <span className={`inline-block h-2 w-2 rounded-full ${DOT_CLS[state]}`} />
      <span>{LABELS[state]}</span>
      {state === 'dead' && (
        <button
          onClick={onStart}
          className="rounded border border-emerald-700/60 bg-emerald-500/10 px-2 py-0.5 text-emerald-300 hover:bg-emerald-500/20"
          title="Spin up the Modal sandbox kernel"
        >
          Start kernel
        </button>
      )}
      {state === 'busy' && (
        <button
          onClick={onInterrupt}
          className="rounded border border-neutral-700 px-2 py-0.5 hover:bg-neutral-800"
        >
          Interrupt
        </button>
      )}
      {state !== 'dead' && (
        <button
          onClick={onShutdown}
          className="rounded border border-neutral-700 px-2 py-0.5 hover:bg-neutral-800"
        >
          Shutdown
        </button>
      )}
    </div>
  );
}
