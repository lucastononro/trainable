'use client';

import { useState } from 'react';
import { FileText, FolderOpen, BarChart3, X } from 'lucide-react';
import { Artifact, MetricPoint, ChartConfig } from '@/lib/types';
import ReportTab from './ReportTab';
import FilesTab from './FilesTab';
import MetricsTab from './MetricsTab';

interface CanvasPanelProps {
  report: string;
  artifacts: Artifact[];
  metricPoints: MetricPoint[];
  chartConfig: ChartConfig | null;
  state: string;
  onClose?: () => void;
}

type Tab = 'report' | 'files' | 'metrics';

const TABS: { key: Tab; label: string; icon: typeof FileText }[] = [
  { key: 'report', label: 'Report', icon: FileText },
  { key: 'files', label: 'Files', icon: FolderOpen },
  { key: 'metrics', label: 'Metrics', icon: BarChart3 },
];

export default function CanvasPanel({
  report,
  artifacts,
  metricPoints,
  chartConfig,
  state,
  onClose,
}: CanvasPanelProps) {
  const [activeTab, setActiveTab] = useState<Tab>('report');

  return (
    <div className="h-full flex flex-col bg-[#0f0f0f] border-l border-white/[0.06]">
      {/* Tab bar */}
      <div className="flex items-center border-b border-white/[0.06] px-1 shrink-0">
        <div className="flex items-center flex-1">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.key;
            const hasContent =
              (tab.key === 'report' && report) ||
              (tab.key === 'files' && artifacts.length > 0) ||
              (tab.key === 'metrics' && metricPoints.length > 0);
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`relative flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium transition-colors ${
                  active
                    ? 'text-white'
                    : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {tab.label}
                {hasContent && !active && (
                  <span className="w-1.5 h-1.5 rounded-full bg-primary-500" />
                )}
                {active && (
                  <span className="absolute bottom-0 left-3 right-3 h-0.5 bg-primary-500 rounded-full" />
                )}
              </button>
            );
          })}
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="p-1.5 mr-1 rounded-lg hover:bg-white/[0.06] transition-colors text-gray-500 hover:text-gray-300"
            title="Close canvas"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === 'report' && <ReportTab report={report} />}
        {activeTab === 'files' && <FilesTab artifacts={artifacts} />}
        {activeTab === 'metrics' && (
          <MetricsTab metricPoints={metricPoints} chartConfig={chartConfig} state={state} />
        )}
      </div>
    </div>
  );
}
