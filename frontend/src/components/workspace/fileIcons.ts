import {
  Braces,
  Code2,
  Cpu,
  Database,
  File as FileIcon,
  FileText,
  Globe,
  Image,
  Table,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// File icon helper
// ---------------------------------------------------------------------------

export function getFileIconInfo(name: string): { icon: typeof FileText; color: string } {
  if (name.endsWith('.py')) return { icon: Code2, color: 'text-yellow-400' };
  if (name.endsWith('.md')) return { icon: FileText, color: 'text-blue-400' };
  if (/\.html?$/i.test(name)) return { icon: Globe, color: 'text-fuchsia-400' };
  if (/\.(png|jpg|jpeg|svg|gif)$/i.test(name)) return { icon: Image, color: 'text-purple-400' };
  if (name.endsWith('.csv')) return { icon: Table, color: 'text-green-400' };
  if (name.endsWith('.parquet')) return { icon: Database, color: 'text-amber-400' };
  if (name.endsWith('.json')) return { icon: Braces, color: 'text-orange-400' };
  if (name.endsWith('.pkl') || name.endsWith('.joblib'))
    return { icon: Cpu, color: 'text-red-400' };
  return { icon: FileIcon, color: 'text-gray-400' };
}

export const DIR_LABELS: Record<string, string> = {
  eda: 'eda',
  prep: 'prep',
  train: 'train',
};

export const DIR_COLORS: Record<string, string> = {
  eda: 'text-blue-400',
  prep: 'text-amber-400',
  train: 'text-green-400',
};
