'use client';

import { useEffect } from 'react';
import { AlertTriangle } from 'lucide-react';

interface Props {
  isOpen: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmModal({
  isOpen,
  title,
  message,
  confirmLabel = 'Delete',
  cancelLabel = 'Cancel',
  destructive = true,
  onConfirm,
  onCancel,
}: Props) {
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
      if (e.key === 'Enter') onConfirm();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onCancel, onConfirm]);

  if (!isOpen) return null;

  const confirmCls = destructive
    ? 'bg-red-600 hover:bg-red-500 text-white'
    : 'bg-primary-600 hover:bg-primary-500 text-white';

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 backdrop-blur-sm animate-fade-in"
      onClick={onCancel}
    >
      <div
        className="w-[420px] max-w-[92vw] bg-black border border-white/[0.08] rounded-2xl shadow-2xl overflow-hidden animate-scale-in"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 flex items-start gap-3">
          {destructive && (
            <div className="w-8 h-8 rounded-lg bg-red-500/10 flex items-center justify-center shrink-0 mt-0.5">
              <AlertTriangle className="w-4 h-4 text-red-400" />
            </div>
          )}
          <div className="flex-1 min-w-0">
            <h2 className="text-sm font-semibold text-white">{title}</h2>
            <p className="text-xs text-gray-400 mt-1 leading-relaxed">{message}</p>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-white/[0.06]">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-gray-300 hover:bg-white/[0.06] transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            autoFocus
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${confirmCls}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
