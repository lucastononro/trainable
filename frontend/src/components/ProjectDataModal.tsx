'use client';

import { useEffect, useState } from 'react';
import {
  X,
  Database,
  Loader2,
  FileText,
  FileSpreadsheet,
  File as FileIcon,
  FolderOpen,
} from 'lucide-react';
import { api } from '@/lib/api';

interface ProjectFile {
  path: string;
  name: string;
  size: number | null;
  mtime: number | null;
  experiment_id: string | null;
  experiment_name: string | null;
}

interface Props {
  projectId: string;
  projectName: string;
  isOpen: boolean;
  onClose: () => void;
}

function fileIcon(name: string) {
  const n = name.toLowerCase();
  if (n.endsWith('.csv') || n.endsWith('.tsv')) return FileSpreadsheet;
  if (n.endsWith('.parquet') || n.endsWith('.feather')) return Database;
  if (n.endsWith('.json') || n.endsWith('.md') || n.endsWith('.txt')) return FileText;
  return FileIcon;
}

function fileIconColor(name: string): string {
  const n = name.toLowerCase();
  if (n.endsWith('.csv') || n.endsWith('.tsv')) return 'text-green-400';
  if (n.endsWith('.parquet') || n.endsWith('.feather')) return 'text-amber-400';
  if (n.endsWith('.json')) return 'text-orange-400';
  if (n.endsWith('.md') || n.endsWith('.txt')) return 'text-blue-400';
  return 'text-gray-400';
}

function humanSize(bytes: number | null): string {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export default function ProjectDataModal({ projectId, projectName, isOpen, onClose }: Props) {
  const [loading, setLoading] = useState(false);
  const [files, setFiles] = useState<ProjectFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen || !projectId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .listProjectFiles(projectId)
      .then((res) => {
        if (cancelled) return;
        setFiles(res.files);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message || 'Failed to load project files');
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, projectId]);

  // Group files by experiment for a cleaner listing.
  const groups = (() => {
    if (!files) return [] as Array<{ expId: string | null; expName: string | null; files: ProjectFile[] }>;
    const map = new Map<string, ProjectFile[]>();
    for (const f of files) {
      const key = f.experiment_id ?? '(unknown)';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(f);
    }
    return Array.from(map.entries()).map(([expId, expFiles]) => ({
      expId: expId === '(unknown)' ? null : expId,
      expName: expFiles[0]?.experiment_name ?? null,
      files: expFiles,
    }));
  })();

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-[720px] max-h-[80vh] bg-[#111111] border border-white/[0.08] rounded-2xl shadow-2xl overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-white/[0.06] shrink-0">
          <div className="w-8 h-8 rounded-lg bg-emerald-500/10 flex items-center justify-center">
            <Database className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-sm font-semibold text-white truncate">Project data</h2>
            <p className="text-xs text-gray-500 truncate">{projectName}</p>
          </div>
          <button
            onClick={onClose}
            title="Close"
            className="p-1.5 rounded-lg hover:bg-white/[0.06] text-gray-500 hover:text-gray-300 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-8 justify-center">
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading files…
            </div>
          )}
          {error && !loading && (
            <div className="text-sm text-red-400 py-4">Error: {error}</div>
          )}
          {!loading && !error && files && files.length === 0 && (
            <div className="text-center py-12">
              <FolderOpen className="w-10 h-10 text-gray-700 mx-auto mb-3" />
              <p className="text-sm text-gray-400">No data uploaded in this project yet.</p>
              <p className="text-xs text-gray-600 mt-1">
                Attach files in a chat using the + button to get started.
              </p>
            </div>
          )}
          {!loading && !error && files && files.length > 0 && (
            <div className="space-y-5">
              {groups.map((group) => (
                <div key={group.expId ?? 'unknown'}>
                  <div className="flex items-center gap-2 mb-2 text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
                    <FolderOpen className="w-3 h-3" />
                    <span className="truncate">{group.expName ?? 'Unknown chat'}</span>
                    <span className="text-gray-700 normal-case tracking-normal">
                      · {group.files.length} file{group.files.length === 1 ? '' : 's'}
                    </span>
                  </div>
                  <div className="space-y-0.5">
                    {group.files.map((f) => {
                      const Icon = fileIcon(f.name);
                      const color = fileIconColor(f.name);
                      return (
                        <div
                          key={f.path}
                          className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-white/[0.04] transition-colors"
                          title={f.path}
                        >
                          <Icon className={`w-4 h-4 shrink-0 ${color}`} />
                          <span className="text-sm text-gray-300 flex-1 truncate">{f.name}</span>
                          <span className="text-[11px] text-gray-600 shrink-0 tabular-nums">
                            {humanSize(f.size)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-white/[0.06] text-[11px] text-gray-600 shrink-0">
          Files are mounted inside agent sandboxes at{' '}
          <code className="bg-white/[0.04] text-gray-400 px-1.5 py-0.5 rounded">
            /data/projects/{projectId}/datasets/
          </code>
        </div>
      </div>
    </div>
  );
}
