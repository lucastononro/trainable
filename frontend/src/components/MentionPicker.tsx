'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FileText, MessageSquare, Paperclip, Loader2 } from 'lucide-react';
import type { Experiment, Mention } from '@/lib/types';
import { api } from '@/lib/api';

interface ProjectFileItem {
  path: string;
  name: string;
  relative_path?: string;
  size: number | null;
  in_sandbox?: boolean | null;
}

interface Props {
  projectId: string | null;
  experiments: Experiment[];
  attachedFilesInSession: { name: string; sandboxPath: string }[];
  query: string;
  anchor: { bottom: number; left: number } | null;
  onPick: (mention: Mention) => void;
  onClose: () => void;
}

function statusDotClass(state: string | null | undefined): string {
  if (!state) return 'bg-gray-600';
  if (state.includes('running')) return 'bg-amber-400 animate-pulse';
  if (state.includes('done')) return 'bg-green-400';
  if (state === 'failed') return 'bg-red-400';
  return 'bg-gray-600';
}

export default function MentionPicker({
  projectId,
  experiments,
  attachedFilesInSession,
  query,
  anchor,
  onPick,
  onClose,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [projectFiles, setProjectFiles] = useState<ProjectFileItem[] | null>(null);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    if (!projectId) {
      setProjectFiles([]);
      return;
    }
    let cancelled = false;
    setLoadingFiles(true);
    api
      .listProjectFiles(projectId)
      .then((res) => {
        if (cancelled) return;
        setProjectFiles(res.files as ProjectFileItem[]);
      })
      .catch(() => {
        if (cancelled) return;
        setProjectFiles([]);
      })
      .finally(() => {
        if (cancelled) return;
        setLoadingFiles(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const lower = query.toLowerCase();
  const matchesName = useCallback(
    (name: string) => !lower || name.toLowerCase().includes(lower),
    [lower],
  );

  const sessionExperiments = useMemo(() => {
    if (!projectId) return [] as Experiment[];
    return experiments.filter(
      (e) => e.project_id === projectId && e.latest_session_id && matchesName(e.name),
    );
  }, [experiments, projectId, matchesName]);

  const attachedMatches = attachedFilesInSession.filter((f) => matchesName(f.name));
  const projectMatches = (projectFiles ?? []).filter((f) => matchesName(f.name));

  // Flat, ordered list used for keyboard navigation.
  const flat: Mention[] = useMemo(() => {
    const out: Mention[] = [];
    for (const f of attachedMatches) {
      out.push({ kind: 'file', ref: f.sandboxPath, label: f.name, sandbox_path: f.sandboxPath });
    }
    for (const f of projectMatches) {
      out.push({
        kind: 'file',
        ref: f.path,
        label: f.name,
        sandbox_path: f.path,
      });
    }
    for (const e of sessionExperiments) {
      if (!e.latest_session_id) continue;
      out.push({
        kind: 'session',
        ref: e.latest_session_id,
        label: e.name,
        experiment_id: e.id,
      });
    }
    return out;
  }, [attachedMatches, projectMatches, sessionExperiments]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query, flat.length]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIndex((i) => (flat.length ? (i + 1) % flat.length : 0));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex((i) => (flat.length ? (i - 1 + flat.length) % flat.length : 0));
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        if (flat[activeIndex]) {
          e.preventDefault();
          onPick(flat[activeIndex]);
        }
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [flat, activeIndex, onClose, onPick]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (!containerRef.current) return;
      if (!containerRef.current.contains(e.target as Node)) onClose();
    };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [onClose]);

  if (!anchor) return null;

  // Render a flat running index so we can highlight the active row.
  let runningIdx = 0;
  const renderItem = (
    key: string,
    icon: JSX.Element,
    label: string,
    secondary: string,
    onSelect: () => void,
  ) => {
    const idx = runningIdx++;
    const active = idx === activeIndex;
    return (
      <button
        key={key}
        type="button"
        onMouseEnter={() => setActiveIndex(idx)}
        onClick={onSelect}
        className={`w-full flex items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors ${
          active ? 'bg-white/[0.08] text-white' : 'text-gray-300 hover:bg-white/[0.04]'
        }`}
      >
        {icon}
        <span className="truncate flex-1 min-w-0">{label}</span>
        <span className="text-[10px] text-gray-500 truncate max-w-[180px]">{secondary}</span>
      </button>
    );
  };

  const empty =
    !loadingFiles &&
    attachedMatches.length === 0 &&
    projectMatches.length === 0 &&
    sessionExperiments.length === 0;

  return (
    <div
      ref={containerRef}
      style={{ bottom: anchor.bottom, left: anchor.left }}
      className="fixed z-[80] w-[360px] max-h-[340px] overflow-y-auto bg-black border border-white/[0.08] rounded-xl shadow-2xl animate-scale-in"
    >
      {attachedMatches.length > 0 && (
        <div>
          <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-gray-500 font-semibold border-b border-white/[0.04]">
            Attached in this chat
          </div>
          {attachedMatches.map((f) =>
            renderItem(
              `att-${f.sandboxPath}`,
              <Paperclip className="w-3.5 h-3.5 text-gray-400 shrink-0" />,
              f.name,
              'session',
              () =>
                onPick({
                  kind: 'file',
                  ref: f.sandboxPath,
                  label: f.name,
                  sandbox_path: f.sandboxPath,
                }),
            ),
          )}
        </div>
      )}

      <div>
        <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-gray-500 font-semibold border-b border-white/[0.04] flex items-center gap-2">
          Project files
          {loadingFiles && <Loader2 className="w-3 h-3 animate-spin text-gray-500" />}
        </div>
        {projectMatches.length === 0 && !loadingFiles && (
          <div className="px-3 py-2 text-xs text-gray-600">No files match.</div>
        )}
        {projectMatches.map((f) =>
          renderItem(
            `proj-${f.path}`,
            <FileText className="w-3.5 h-3.5 text-emerald-400 shrink-0" />,
            f.name,
            f.relative_path || f.path,
            () =>
              onPick({
                kind: 'file',
                ref: f.path,
                label: f.name,
                sandbox_path: f.path,
              }),
          ),
        )}
      </div>

      <div>
        <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-gray-500 font-semibold border-b border-white/[0.04]">
          Sessions in this project
        </div>
        {sessionExperiments.length === 0 && (
          <div className="px-3 py-2 text-xs text-gray-600">No sessions match.</div>
        )}
        {sessionExperiments.map((e) =>
          renderItem(
            `sess-${e.id}`,
            <div className="flex items-center gap-1.5 shrink-0">
              <MessageSquare className="w-3.5 h-3.5 text-indigo-400" />
              <span className={`w-1.5 h-1.5 rounded-full ${statusDotClass(e.latest_state)}`} />
            </div>,
            e.name,
            e.latest_state || '',
            () =>
              onPick({
                kind: 'session',
                ref: e.latest_session_id as string,
                label: e.name,
                experiment_id: e.id,
              }),
          ),
        )}
      </div>

      {empty && (
        <div className="px-3 py-4 text-xs text-gray-600 text-center">Nothing to mention yet.</div>
      )}
    </div>
  );
}
