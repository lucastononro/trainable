'use client';

import { ChevronRight, Folder, FolderOpen } from 'lucide-react';
import type { FileTreeNode } from '@/lib/types';
import { DIR_COLORS, DIR_LABELS, getFileIconInfo } from '@/components/workspace/fileIcons';

// ---------------------------------------------------------------------------
// FileTreeRow -- recursive, github.dev style
// ---------------------------------------------------------------------------

export default function FileTreeRow({
  node,
  depth,
  expandedDirs,
  toggleDir,
  selectedFile,
  onSelectFile,
}: {
  node: FileTreeNode;
  depth: number;
  expandedDirs: Set<string>;
  toggleDir: (path: string) => void;
  selectedFile: string | null;
  onSelectFile: (path: string) => void;
}) {
  const isDir = node.type === 'directory';
  const isExpanded = expandedDirs.has(node.path);
  const isSelected = !isDir && selectedFile === node.path;
  const pl = 12 + depth * 16;

  if (isDir) {
    const color = DIR_COLORS[node.name] || 'text-gray-400';
    return (
      <>
        <button
          onClick={() => toggleDir(node.path)}
          className="w-full flex items-center gap-1.5 h-[26px] text-[13px] transition-colors hover:bg-white/[0.04] text-gray-300 group"
          style={{ paddingLeft: `${pl}px`, paddingRight: '10px' }}
        >
          <ChevronRight
            className={`w-3 h-3 shrink-0 text-gray-500 transition-transform duration-150 ${isExpanded ? 'rotate-90' : ''}`}
          />
          {isExpanded ? (
            <FolderOpen className={`w-4 h-4 shrink-0 ${color}`} />
          ) : (
            <Folder className={`w-4 h-4 shrink-0 ${color}`} />
          )}
          <span className="flex-1 text-left truncate">{DIR_LABELS[node.name] || node.name}</span>
        </button>
        {isExpanded &&
          node.children &&
          node.children.map((child) => (
            <FileTreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              expandedDirs={expandedDirs}
              toggleDir={toggleDir}
              selectedFile={selectedFile}
              onSelectFile={onSelectFile}
            />
          ))}
      </>
    );
  }

  const { icon: FIcon, color } = getFileIconInfo(node.name);
  return (
    <button
      onClick={() => onSelectFile(node.path)}
      className={`w-full flex items-center gap-1.5 h-[26px] text-[13px] transition-colors ${
        isSelected
          ? 'bg-primary-500/10 text-primary-300'
          : 'text-gray-400 hover:bg-white/[0.04] hover:text-gray-200'
      }`}
      style={{ paddingLeft: `${pl + 15}px`, paddingRight: '10px' }}
    >
      <FIcon className={`w-4 h-4 shrink-0 ${color}`} />
      <span className="flex-1 text-left truncate">{node.name}</span>
    </button>
  );
}
