'use client';

import { useEffect, useState } from 'react';
import { Settings, X } from 'lucide-react';
import type { SandboxConfig, SandboxProfile } from '@/lib/types';

// Canonical labels — must match backend/schemas.py CANONICAL_GPUS and the
// (provider, gpu) rate keys in backend/services/sandbox.yml. The old
// 'A100' value silently broke billing (rates are keyed 'A100-40GB').
const GPU_OPTIONS = [
  { value: '', label: 'None (CPU only)' },
  { value: 'T4', label: 'T4 — 16 GB' },
  { value: 'L4', label: 'L4 — 24 GB' },
  { value: 'A10G', label: 'A10G — 24 GB' },
  { value: 'A100-40GB', label: 'A100 — 40 GB' },
  { value: 'A100-80GB', label: 'A100 — 80 GB' },
  { value: 'H100', label: 'H100 — 80 GB' },
];

// Options for the agent allowance checkboxes ("cpu" is always allowed and
// shown as locked).
const ALLOWANCE_OPTIONS = GPU_OPTIONS.filter((o) => o.value !== '');

interface Props {
  isOpen: boolean;
  projectName: string;
  sandboxConfig: SandboxConfig;
  onSave: (config: SandboxConfig) => void;
  onClose: () => void;
}

function ProfileSection({
  label,
  description,
  gpu,
  timeout,
  defaultTimeout,
  onGpuChange,
  onTimeoutChange,
}: {
  label: string;
  description: string;
  gpu: string;
  timeout: number;
  defaultTimeout: number;
  onGpuChange: (v: string) => void;
  onTimeoutChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="flex items-baseline gap-2 mb-2">
        <h4 className="text-xs font-semibold text-gray-300">{label}</h4>
        <span className="text-[11px] text-gray-600">{description}</span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <label className="text-[11px] text-gray-500">GPU</label>
          <select
            value={gpu}
            onChange={(e) => onGpuChange(e.target.value)}
            className="w-full px-2.5 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08] text-xs text-white focus:outline-none focus:border-blue-500/50 transition-colors"
          >
            {GPU_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value} className="bg-black">
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <label className="text-[11px] text-gray-500">Timeout (s)</label>
          <input
            type="number"
            min={10}
            max={7200}
            value={timeout}
            onChange={(e) => onTimeoutChange(Number(e.target.value))}
            className="w-full px-2.5 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08] text-xs text-white focus:outline-none focus:border-blue-500/50 transition-colors"
          />
        </div>
      </div>
    </div>
  );
}

export default function ProjectSettingsModal({
  isOpen,
  projectName,
  sandboxConfig,
  onSave,
  onClose,
}: Props) {
  const [defaultGpu, setDefaultGpu] = useState('');
  const [defaultTimeout, setDefaultTimeout] = useState(600);
  const [trainingGpu, setTrainingGpu] = useState('');
  const [trainingTimeout, setTrainingTimeout] = useState(1800);
  const [allowedGpus, setAllowedGpus] = useState<string[]>([]);
  const [maxTimeout, setMaxTimeout] = useState<number | ''>('');

  useEffect(() => {
    if (isOpen) {
      const d = sandboxConfig.default;
      const t = sandboxConfig.training;
      setDefaultGpu(d?.gpu || '');
      setDefaultTimeout(d?.timeout ?? 600);
      setTrainingGpu(t?.gpu || '');
      setTrainingTimeout(t?.timeout ?? 1800);
      setAllowedGpus(sandboxConfig.allowed_gpus ?? []);
      setMaxTimeout(sandboxConfig.max_timeout ?? '');
    }
  }, [isOpen, sandboxConfig]);

  const toggleAllowedGpu = (value: string) => {
    setAllowedGpus((prev) =>
      prev.includes(value) ? prev.filter((g) => g !== value) : [...prev, value]
    );
  };

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const buildProfile = (gpu: string, timeout: number): SandboxProfile | null => {
    const hasGpu = gpu !== '';
    const hasTimeout = timeout > 0;
    if (!hasGpu && !hasTimeout) return null;
    return {
      gpu: hasGpu ? gpu : null,
      timeout: hasTimeout ? timeout : null,
    };
  };

  const handleSave = () => {
    onSave({
      default: buildProfile(defaultGpu, defaultTimeout),
      training: buildProfile(trainingGpu, trainingTimeout),
      allowed_gpus: allowedGpus.length ? allowedGpus : null,
      max_timeout: maxTimeout === '' ? null : maxTimeout,
    });
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-[520px] max-w-[92vw] bg-black border border-white/[0.08] rounded-2xl shadow-2xl overflow-hidden animate-scale-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center shrink-0">
            <Settings className="w-4 h-4 text-blue-400" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-sm font-semibold text-white">Project Settings</h2>
            <p className="text-xs text-gray-500 truncate">{projectName}</p>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg hover:bg-white/[0.06] transition-colors"
          >
            <X className="w-4 h-4 text-gray-500" />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 border-t border-white/[0.06] space-y-5">
          <h3 className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
            Compute Sandbox
          </h3>

          <ProfileSection
            label="Default"
            description="EDA, data prep, lightweight tasks"
            gpu={defaultGpu}
            timeout={defaultTimeout}
            defaultTimeout={600}
            onGpuChange={setDefaultGpu}
            onTimeoutChange={setDefaultTimeout}
          />

          <div className="border-t border-white/[0.04]" />

          <ProfileSection
            label="Training"
            description="Model training, tuning, heavy compute"
            gpu={trainingGpu}
            timeout={trainingTimeout}
            defaultTimeout={1800}
            onGpuChange={setTrainingGpu}
            onTimeoutChange={setTrainingTimeout}
          />

          <div className="border-t border-white/[0.04]" />

          {/* Agent GPU allowance */}
          <div>
            <div className="flex items-baseline gap-2 mb-2">
              <h4 className="text-xs font-semibold text-gray-300">Agent GPU allowance</h4>
              <span className="text-[11px] text-gray-600">
                hardware the agent may request per call
              </span>
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              <label className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-white/[0.02] border border-white/[0.06] text-xs text-gray-500 cursor-not-allowed">
                <input type="checkbox" checked disabled className="accent-blue-600" />
                CPU (always allowed)
              </label>
              {ALLOWANCE_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08] text-xs text-gray-300 cursor-pointer hover:border-blue-500/40 transition-colors"
                >
                  <input
                    type="checkbox"
                    checked={allowedGpus.includes(opt.value)}
                    onChange={() => toggleAllowedGpu(opt.value)}
                    className="accent-blue-600"
                  />
                  {opt.label}
                </label>
              ))}
            </div>
            <div className="mt-3 space-y-1">
              <label className="text-[11px] text-gray-500">
                Max per-call timeout (s) — empty = largest profile timeout
              </label>
              <input
                type="number"
                min={10}
                max={7200}
                value={maxTimeout}
                onChange={(e) =>
                  setMaxTimeout(e.target.value === '' ? '' : Number(e.target.value))
                }
                className="w-full px-2.5 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08] text-xs text-white focus:outline-none focus:border-blue-500/50 transition-colors"
              />
            </div>
          </div>

          <p className="text-[11px] text-gray-600">
            Agents pick the cheapest allowed hardware per call via the{' '}
            <code className="text-gray-500">gpu</code> argument; the training profile
            remains the <code className="text-gray-500">heavy=true</code> fallback.
            Profile GPUs are always implicitly allowed.
          </p>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-white/[0.06]">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-gray-300 hover:bg-white/[0.06] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white transition-colors"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
