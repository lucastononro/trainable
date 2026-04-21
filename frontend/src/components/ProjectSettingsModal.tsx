'use client';

import { useEffect, useState } from 'react';
import { Settings, X } from 'lucide-react';
import type { SandboxConfig } from '@/lib/types';

const GPU_OPTIONS = [
  { value: '', label: 'None (CPU only)' },
  { value: 'T4', label: 'T4 — 16 GB' },
  { value: 'L4', label: 'L4 — 24 GB' },
  { value: 'A10G', label: 'A10G — 24 GB' },
  { value: 'A100', label: 'A100 — 40 GB' },
];

interface Props {
  isOpen: boolean;
  projectName: string;
  sandboxConfig: SandboxConfig;
  onSave: (config: SandboxConfig) => void;
  onClose: () => void;
}

export default function ProjectSettingsModal({
  isOpen,
  projectName,
  sandboxConfig,
  onSave,
  onClose,
}: Props) {
  const [gpu, setGpu] = useState(sandboxConfig.gpu || '');
  const [timeout, setTimeout] = useState(sandboxConfig.timeout ?? 600);

  useEffect(() => {
    if (isOpen) {
      setGpu(sandboxConfig.gpu || '');
      setTimeout(sandboxConfig.timeout ?? 600);
    }
  }, [isOpen, sandboxConfig]);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSave = () => {
    onSave({
      gpu: gpu || null,
      timeout: timeout || null,
    });
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-[480px] max-w-[92vw] bg-black border border-white/[0.08] rounded-2xl shadow-2xl overflow-hidden animate-scale-in"
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
        <div className="px-5 py-4 border-t border-white/[0.06] space-y-4">
          <div>
            <h3 className="text-xs font-semibold text-gray-300 uppercase tracking-wider mb-3">
              Modal Sandbox
            </h3>

            {/* GPU */}
            <div className="space-y-1.5 mb-3">
              <label className="text-xs text-gray-400">GPU</label>
              <select
                value={gpu}
                onChange={(e) => setGpu(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-white/[0.04] border border-white/[0.08] text-sm text-white focus:outline-none focus:border-blue-500/50 transition-colors"
              >
                {GPU_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value} className="bg-black">
                    {opt.label}
                  </option>
                ))}
              </select>
              <p className="text-[11px] text-gray-600">
                GPU attached to each code execution sandbox. Costs apply per second of use.
              </p>
            </div>

            {/* Timeout */}
            <div className="space-y-1.5">
              <label className="text-xs text-gray-400">Timeout (seconds)</label>
              <input
                type="number"
                min={10}
                max={7200}
                value={timeout}
                onChange={(e) => setTimeout(Number(e.target.value))}
                className="w-full px-3 py-2 rounded-lg bg-white/[0.04] border border-white/[0.08] text-sm text-white focus:outline-none focus:border-blue-500/50 transition-colors"
              />
              <p className="text-[11px] text-gray-600">
                Max runtime per code execution. Default is 600s (10 min).
              </p>
            </div>
          </div>
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
