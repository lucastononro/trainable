'use client';

import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Zap, Sparkles, Gauge } from 'lucide-react';
import { useApp } from '@/lib/AppContext';

const TIER_CONFIG: Record<string, { icon: typeof Sparkles; color: string; dot: string }> = {
  premium: { icon: Sparkles, color: 'text-amber-400', dot: 'bg-amber-400' },
  standard: { icon: Zap, color: 'text-blue-400', dot: 'bg-blue-400' },
  fast: { icon: Gauge, color: 'text-green-400', dot: 'bg-green-400' },
};

export default function ModelSelector() {
  const { models, selectedModel, setSelectedModel } = useApp();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const current = models.find((m) => m.id === selectedModel) || models[0];
  if (!current) return null;

  const tier = TIER_CONFIG[current.tier] || TIER_CONFIG.standard;
  const TierIcon = tier.icon;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        title={`Model: ${current.name}`}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg hover:bg-white/[0.06] transition-colors text-xs"
      >
        <span className={`w-1.5 h-1.5 rounded-full ${tier.dot}`} />
        <span className="text-gray-300 font-medium">{current.name.replace('Claude ', '')}</span>
        <ChevronDown
          className={`w-3 h-3 text-gray-500 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute top-full right-0 mt-1 w-72 bg-black border border-white/[0.08] rounded-xl shadow-xl z-50 overflow-hidden animate-scale-in">
          <div className="px-3 py-2 border-b border-white/[0.06]">
            <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
              Model
            </span>
          </div>
          {models.map((m) => {
            const mt = TIER_CONFIG[m.tier] || TIER_CONFIG.standard;
            const MIcon = mt.icon;
            const isActive = m.id === selectedModel;
            return (
              <button
                key={m.id}
                onClick={() => {
                  setSelectedModel(m.id);
                  setOpen(false);
                }}
                title={`${m.name} — ${m.description}`}
                className={`w-full flex items-start gap-3 px-3 py-2.5 text-left transition-colors ${
                  isActive ? 'bg-white/[0.06]' : 'hover:bg-white/[0.04]'
                }`}
              >
                <MIcon className={`w-4 h-4 mt-0.5 shrink-0 ${mt.color}`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-sm font-medium ${isActive ? 'text-white' : 'text-gray-300'}`}
                    >
                      {m.name}
                    </span>
                    {isActive && <span className="w-1.5 h-1.5 rounded-full bg-primary-500" />}
                  </div>
                  <p className="text-[11px] text-gray-500 mt-0.5">{m.description}</p>
                  <div className="flex items-center gap-3 mt-1 text-[10px] text-gray-600">
                    <span>${m.input_cost}/MTok in</span>
                    <span>${m.output_cost}/MTok out</span>
                    <span>{m.context} ctx</span>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
