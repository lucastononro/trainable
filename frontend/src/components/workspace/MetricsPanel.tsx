'use client';

import MetricsTab from '@/components/MetricsTab';
import type { ChartConfig, LogEvent, MetricPoint } from '@/lib/types';

// ---------------------------------------------------------------------------
// MetricsPanel — the metrics tab body inside the workspace canvas. Thin
// wrapper around MetricsTab that owns the tab's full-height container.
// ---------------------------------------------------------------------------

export default function MetricsPanel({
  metricPoints,
  chartConfig,
  logEvents,
  sessionState,
}: {
  metricPoints: MetricPoint[];
  chartConfig: ChartConfig | null;
  logEvents: LogEvent[];
  sessionState: string;
}) {
  return (
    <div className="h-full overflow-hidden bg-black">
      <MetricsTab
        metricPoints={metricPoints}
        chartConfig={chartConfig}
        logEvents={logEvents}
        state={sessionState}
      />
    </div>
  );
}
