'use client';

import { useEffect, useState } from 'react';
import { Settings, X } from 'lucide-react';
import type { SandboxConfig, SandboxProfile, TrainingConfig } from '@/lib/types';

const GPU_OPTIONS = [
  { value: '', label: 'None (CPU only)' },
  { value: 'T4', label: 'T4 — 16 GB' },
  { value: 'L4', label: 'L4 — 24 GB' },
  { value: 'A10G', label: 'A10G — 24 GB' },
  { value: 'A100', label: 'A100 — 40 GB' },
];

/** Mirrors backend schemas.KNOWN_MODEL_FAMILIES (minus the "other" catch-all). */
const MODEL_FAMILY_OPTIONS = [
  { value: 'xgboost', label: 'XGBoost' },
  { value: 'lightgbm', label: 'LightGBM' },
  { value: 'sklearn', label: 'scikit-learn' },
  { value: 'pytorch', label: 'PyTorch' },
  { value: 'tensorflow', label: 'TensorFlow' },
  { value: 'huggingface', label: 'HuggingFace' },
];

const METRIC_SUGGESTIONS = [
  'roc_auc',
  'pr_auc',
  'f1',
  'accuracy',
  'precision',
  'recall',
  'log_loss',
  'rmse',
  'mae',
  'r2',
];

interface Props {
  isOpen: boolean;
  projectName: string;
  sandboxConfig: SandboxConfig;
  /** Hard-stop USD spend cap for the project. null = uncapped. */
  budgetUsd: number | null;
  trainingConfig: TrainingConfig;
  onSave: (config: SandboxConfig, budgetUsd: number | null, training: TrainingConfig) => void;
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
  budgetUsd,
  trainingConfig,
  onSave,
  onClose,
}: Props) {
  const [defaultGpu, setDefaultGpu] = useState('');
  const [defaultTimeout, setDefaultTimeout] = useState(600);
  const [trainingGpu, setTrainingGpu] = useState('');
  const [trainingTimeout, setTrainingTimeout] = useState(1800);
  // Budget kept as a string so the field can be emptied (= no limit).
  const [budget, setBudget] = useState('');

  // Pre-flight training controls (issue #104). Empty string / empty list =
  // "no constraint" — the trainer agent keeps full autonomy.
  const [optimizationMetric, setOptimizationMetric] = useState('');
  const [modelFamilies, setModelFamilies] = useState<string[]>([]);
  const [maxTrials, setMaxTrials] = useState('');
  const [maxWallclock, setMaxWallclock] = useState('');
  const [maxCost, setMaxCost] = useState('');

  useEffect(() => {
    if (isOpen) {
      const d = sandboxConfig.default;
      const t = sandboxConfig.training;
      setDefaultGpu(d?.gpu || '');
      setDefaultTimeout(d?.timeout ?? 600);
      setTrainingGpu(t?.gpu || '');
      setTrainingTimeout(t?.timeout ?? 1800);
      setBudget(budgetUsd != null ? String(budgetUsd) : '');
      setOptimizationMetric(trainingConfig.optimization_metric || '');
      setModelFamilies(trainingConfig.model_families || []);
      setMaxTrials(trainingConfig.max_trials != null ? String(trainingConfig.max_trials) : '');
      setMaxWallclock(
        trainingConfig.max_wallclock_minutes != null
          ? String(trainingConfig.max_wallclock_minutes)
          : '',
      );
      setMaxCost(trainingConfig.max_cost_usd != null ? String(trainingConfig.max_cost_usd) : '');
    }
  }, [isOpen, sandboxConfig, budgetUsd, trainingConfig]);

  const toggleFamily = (value: string) => {
    setModelFamilies((prev) =>
      prev.includes(value) ? prev.filter((f) => f !== value) : [...prev, value],
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
    const parsed = parseFloat(budget);
    const parsePositive = (raw: string, integer = false): number | undefined => {
      const n = Number(raw);
      if (raw.trim() === '' || !Number.isFinite(n) || n <= 0) return undefined;
      return integer ? Math.floor(n) : n;
    };
    const training: TrainingConfig = {
      optimization_metric: optimizationMetric.trim() || undefined,
      model_families: modelFamilies.length > 0 ? modelFamilies : undefined,
      max_trials: parsePositive(maxTrials, true),
      max_wallclock_minutes: parsePositive(maxWallclock, true),
      max_cost_usd: parsePositive(maxCost),
    };
    onSave(
      {
        default: buildProfile(defaultGpu, defaultTimeout),
        training: buildProfile(trainingGpu, trainingTimeout),
      },
      Number.isFinite(parsed) && parsed >= 0 ? parsed : null,
      training,
    );
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
        <div className="px-5 py-4 border-t border-white/[0.06] space-y-5 max-h-[70vh] overflow-y-auto">
          <h3 className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
            Modal Sandbox
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

          <p className="text-[11px] text-gray-600">
            Agents automatically select the right profile. The training profile is used when{' '}
            <code className="text-gray-500">heavy=true</code> is set on code execution.
          </p>

          <div className="border-t border-white/[0.04]" />

          <h3 className="text-xs font-semibold text-gray-300 uppercase tracking-wider">Budget</h3>
          <div>
            <div className="flex items-baseline gap-2 mb-2">
              <h4 className="text-xs font-semibold text-gray-300">Spend cap (USD)</h4>
              <span className="text-[11px] text-gray-600">whole project, LLM + compute</span>
            </div>
            <input
              type="number"
              min={0}
              step="0.5"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              placeholder="No limit"
              className="w-full px-2.5 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08] text-xs text-white focus:outline-none focus:border-blue-500/50 transition-colors"
            />
            <p className="text-[11px] text-gray-600 mt-2">
              Hard stop: agents halt as soon as the project&apos;s accumulated spend crosses this
              cap. Leave empty for no limit.
            </p>
          </div>

          <div className="border-t border-white/[0.06]" />

          {/* Pre-flight training controls (issue #104) */}
          <div>
            <h3 className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
              Training Controls
            </h3>
            <p className="mt-1 text-[11px] text-gray-600">
              Constrain the trainer agent before it runs. Leave a field empty to let the agent
              decide.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-[11px] text-gray-500">Optimization metric</label>
              <input
                type="text"
                list="metric-suggestions"
                placeholder="agent's choice"
                value={optimizationMetric}
                onChange={(e) => setOptimizationMetric(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08] text-xs text-white placeholder:text-gray-600 focus:outline-none focus:border-blue-500/50 transition-colors"
              />
              <datalist id="metric-suggestions">
                {METRIC_SUGGESTIONS.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </div>
            <div className="space-y-1">
              <label className="text-[11px] text-gray-500">Trial budget (max trials)</label>
              <input
                type="number"
                min={1}
                max={1000}
                step={1}
                placeholder="agent's choice"
                value={maxTrials}
                onChange={(e) => setMaxTrials(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08] text-xs text-white placeholder:text-gray-600 focus:outline-none focus:border-blue-500/50 transition-colors"
              />
            </div>
          </div>

          <div className="space-y-1">
            <label className="text-[11px] text-gray-500">Model families</label>
            <div className="flex flex-wrap gap-1.5">
              {MODEL_FAMILY_OPTIONS.map((opt) => {
                const selected = modelFamilies.includes(opt.value);
                return (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => toggleFamily(opt.value)}
                    className={`px-2.5 py-1 rounded-full text-[11px] font-medium border transition-colors ${
                      selected
                        ? 'bg-blue-500/20 border-blue-500/50 text-blue-300'
                        : 'bg-white/[0.04] border-white/[0.08] text-gray-400 hover:border-white/[0.16]'
                    }`}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
            <p className="text-[11px] text-gray-600">
              {modelFamilies.length === 0
                ? 'None selected — the agent may use any framework.'
                : 'The agent may only train models from the selected families.'}
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-[11px] text-gray-500">Wall-clock cap (minutes)</label>
              <input
                type="number"
                min={1}
                max={1440}
                step={1}
                placeholder="no cap"
                value={maxWallclock}
                onChange={(e) => setMaxWallclock(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08] text-xs text-white placeholder:text-gray-600 focus:outline-none focus:border-blue-500/50 transition-colors"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[11px] text-gray-500">Cost cap (USD)</label>
              <input
                type="number"
                min={0}
                step={0.5}
                placeholder="no cap"
                value={maxCost}
                onChange={(e) => setMaxCost(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08] text-xs text-white placeholder:text-gray-600 focus:outline-none focus:border-blue-500/50 transition-colors"
              />
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
