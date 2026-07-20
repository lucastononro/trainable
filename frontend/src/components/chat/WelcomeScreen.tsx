'use client';

import { useRef, type Dispatch, type RefObject, type SetStateAction } from 'react';
import {
  ArrowUp,
  BarChart3,
  Cpu,
  Database,
  FolderUp,
  HardDrive,
  Loader2,
  Plus,
  Terminal,
  Upload,
} from 'lucide-react';
import { isDraftEmpty } from '@/lib/mentions';
import type { Draft, Experiment } from '@/lib/types';
import MentionInput, { MentionInputHandle } from '@/components/MentionInput';
import AttachedFilesPreview from '@/components/chat/AttachedFilesPreview';

// ---------------------------------------------------------------------------
// Welcome screen suggestions
// ---------------------------------------------------------------------------

const SUGGESTIONS = [
  {
    icon: BarChart3,
    label: 'Explore a dataset',
    prompt: 'Analyze this dataset — perform a full EDA.',
  },
  { icon: Cpu, label: 'Train a model', prompt: 'Train a model on this dataset.' },
  {
    icon: Database,
    label: 'Clean & prep data',
    prompt: 'Clean and prepare this dataset for modeling.',
  },
  {
    icon: Terminal,
    label: 'Write a script',
    prompt: 'Write a Python script to process this data.',
  },
];

// ---------------------------------------------------------------------------
// WelcomeScreen — shown whenever the chat has no user turn yet: logo,
// centered input bar with the attach menu, and suggestion chips.
// ---------------------------------------------------------------------------

export default function WelcomeScreen({
  activeProjectId,
  experiments,
  draft,
  onDraftChange,
  onSend,
  attachedFiles,
  onRemoveAttachedFile,
  onClearAttachedFiles,
  attachingFiles,
  showAttachMenu,
  setShowAttachMenu,
  attachMenuRef,
  fileInputRef,
  folderInputRef,
  onFilesSelected,
  onOpenS3Browser,
  sessionAttachedFiles,
}: {
  // Passed as props (not read from AppContext) so the component stays
  // reusable and testable outside the studio page.
  activeProjectId: string | null;
  experiments: Experiment[];
  draft: Draft;
  onDraftChange: (draft: Draft) => void;
  onSend: () => void;
  attachedFiles: File[];
  onRemoveAttachedFile: (index: number) => void;
  onClearAttachedFiles: () => void;
  attachingFiles: boolean;
  showAttachMenu: boolean;
  setShowAttachMenu: Dispatch<SetStateAction<boolean>>;
  /** Shared with ChatPane — only one of the two inputs is mounted at a
   *  time, and the outside-click close handler lives in the page. */
  attachMenuRef: RefObject<HTMLDivElement>;
  fileInputRef: RefObject<HTMLInputElement>;
  folderInputRef: RefObject<HTMLInputElement>;
  onFilesSelected: (files: FileList | File[]) => void;
  onOpenS3Browser: () => void;
  sessionAttachedFiles: { name: string; sandboxPath: string }[];
}) {
  const inputRef = useRef<MentionInputHandle | null>(null);

  // Handle welcome-screen suggestion click
  const handleSuggestion = (prompt: string) => {
    onDraftChange([{ kind: 'text', value: prompt }]);
    inputRef.current?.focus();
  };

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-4 animate-fade-in">
      <div className="w-full max-w-2xl space-y-8">
        {/* Logo + title */}
        <div className="text-center space-y-3">
          <div className="flex items-center justify-center gap-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/logo-brain-transparent.png" alt="Trainable" className="h-10 w-auto" />
          </div>
          <h1 className="text-2xl font-semibold text-white">What would you like to explore?</h1>
          <p className="text-sm text-gray-500">
            Upload a dataset or describe what you want to build. Trainable will handle the rest.
          </p>
        </div>

        {/* File previews */}
        <AttachedFilesPreview
          files={attachedFiles}
          onRemove={onRemoveAttachedFile}
          onClearAll={onClearAttachedFiles}
          variant="home"
        />

        {/* Input bar */}
        <div className="relative">
          <div className="flex items-center gap-2 bg-[#1e1f22] rounded-2xl px-3 py-3 transition-colors">
            {/* Attach button */}
            <div className="relative" ref={attachMenuRef}>
              <button
                type="button"
                onClick={() => setShowAttachMenu(!showAttachMenu)}
                className={`p-1.5 rounded-xl transition-colors shrink-0 ${
                  showAttachMenu
                    ? 'bg-white/[0.1] text-white'
                    : 'hover:bg-white/[0.08] text-gray-400 hover:text-gray-300'
                }`}
                title="Attach files or data"
              >
                <Plus className="w-5 h-5" />
              </button>
              {showAttachMenu && (
                <div className="absolute bottom-full left-0 mb-2 w-52 bg-black border border-white/[0.08] rounded-xl shadow-xl z-50 overflow-hidden animate-scale-in">
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(e) => e.target.files && onFilesSelected(e.target.files)}
                  />
                  <input
                    ref={folderInputRef}
                    type="file"
                    // @ts-ignore
                    webkitdirectory=""
                    directory=""
                    multiple
                    className="hidden"
                    onChange={(e) => e.target.files && onFilesSelected(e.target.files)}
                  />
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    title="Upload files from your computer"
                    className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                  >
                    <Upload className="w-4 h-4 text-gray-500" />
                    Upload files
                  </button>
                  <button
                    onClick={() => folderInputRef.current?.click()}
                    title="Upload an entire folder"
                    className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                  >
                    <FolderUp className="w-4 h-4 text-gray-500" />
                    Upload folder
                  </button>
                  <div className="border-t border-white/[0.06]" />
                  <button
                    onClick={() => {
                      setShowAttachMenu(false);
                      onOpenS3Browser();
                    }}
                    title="Browse existing S3 datasets"
                    className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                  >
                    <HardDrive className="w-4 h-4 text-gray-500" />
                    Browse S3 data
                  </button>
                </div>
              )}
            </div>

            <MentionInput
              ref={inputRef}
              draft={draft}
              onChange={onDraftChange}
              onSubmit={onSend}
              placeholder="Describe your task, ask a question, or upload data..."
              className="flex-1 py-1"
              projectId={activeProjectId}
              experiments={experiments}
              attachedFilesInSession={sessionAttachedFiles}
            />
            <button
              onClick={onSend}
              disabled={isDraftEmpty(draft) && attachedFiles.length === 0}
              title="Send message"
              className="p-2 bg-primary-600 hover:bg-primary-700 disabled:opacity-30 rounded-xl transition-colors shrink-0"
            >
              {attachingFiles ? (
                <Loader2 className="w-4 h-4 text-white animate-spin" />
              ) : (
                <ArrowUp className="w-4 h-4 text-white" />
              )}
            </button>
          </div>
        </div>

        {/* Suggestion chips */}
        <div className="grid grid-cols-2 gap-3">
          {SUGGESTIONS.map((s, i) => {
            const SIcon = s.icon;
            return (
              <button
                key={i}
                onClick={() => handleSuggestion(s.prompt)}
                title={s.prompt}
                className="flex items-center gap-3 px-4 py-3 rounded-xl bg-surface-elevated border border-surface-border hover:border-gray-600 hover:bg-surface-hover transition-all text-left group"
              >
                <SIcon className="w-5 h-5 text-gray-500 group-hover:text-primary-400 transition-colors shrink-0" />
                <span className="text-sm text-gray-400 group-hover:text-gray-300 transition-colors">
                  {s.label}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
