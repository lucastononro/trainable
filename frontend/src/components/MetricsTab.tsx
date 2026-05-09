'use client';

import { memo, useMemo, useState, useCallback } from 'react';
import {
  LineChart,
  Line,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import {
  BarChart3,
  TrendingUp,
  TrendingDown,
  Minus,
  Activity,
  Layers,
  Hash,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { MetricPoint, ChartConfig } from '@/lib/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MetricsTabProps {
  metricPoints: MetricPoint[];
  chartConfig: ChartConfig | null;
  state: string;
}

interface MetricSummary {
  name: string;
  displayName: string;
  runTag: string | null;
  latest: number;
  best: number;
  delta: number | null;
  improving: boolean | null;
  lowerIsBetter: boolean;
  steps: number;
}

interface ChartGroup {
  title: string;
  type: 'line' | 'bar' | 'area';
  data: Record<string, number>[];
  seriesKeys: string[];
}

// ---------------------------------------------------------------------------
// Color palette — 16 distinct, ordered for max contrast between neighbors
// ---------------------------------------------------------------------------

const PALETTE = [
  '#3B82F6', // blue
  '#F97316', // orange
  '#10B981', // emerald
  '#EF4444', // red
  '#A855F7', // purple
  '#F59E0B', // amber
  '#06B6D4', // cyan
  '#EC4899', // pink
  '#84CC16', // lime
  '#6366F1', // indigo
  '#14B8A6', // teal
  '#F43F5E', // rose
  '#8B5CF6', // violet
  '#22D3EE', // light cyan
  '#FB923C', // light orange
  '#34D399', // light emerald
];

// ---------------------------------------------------------------------------
// Heuristic helpers
// ---------------------------------------------------------------------------

const LOSS_PATTERN = /loss|error|mse|rmse|mae|cross_entropy|log_loss|logloss|hinge/i;
const PERF_PATTERN = /accuracy|acc|f1|precision|recall|auc|roc_auc|r2|r_squared/i;

function inferGroup(name: string): string {
  if (LOSS_PATTERN.test(name)) return 'Loss';
  if (PERF_PATTERN.test(name)) return 'Performance';
  return 'Other';
}

function isLowerBetter(name: string): boolean {
  return LOSS_PATTERN.test(name);
}

function prettyMetricName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function smartFormat(v: number): string {
  if (v === 0) return '0';
  const abs = Math.abs(v);
  if (abs >= 10000) return v.toLocaleString('en-US', { maximumFractionDigits: 0 });
  if (abs >= 100) return v.toFixed(2);
  if (abs >= 1) return v.toFixed(4);
  if (abs >= 0.001) return v.toFixed(5);
  return v.toExponential(2);
}

function compactFormat(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1000000) return (v / 1000000).toFixed(1) + 'M';
  if (abs >= 1000) return (v / 1000).toFixed(1) + 'K';
  if (abs >= 100) return v.toFixed(1);
  if (abs >= 1) return v.toFixed(3);
  if (abs >= 0.01) return v.toFixed(4);
  return v.toExponential(1);
}

// ---------------------------------------------------------------------------
// Custom Tooltip
// ---------------------------------------------------------------------------

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-black border border-white/10 rounded-lg px-3 py-2.5 shadow-xl shadow-black/50 min-w-[180px]">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2 font-medium">
        Step {label}
      </div>
      <div className="space-y-1.5">
        {payload.map((entry: any, i: number) => (
          <div key={i} className="flex items-center justify-between gap-4 text-xs">
            <div className="flex items-center gap-2 min-w-0">
              <div
                className="w-2.5 h-[3px] rounded-full shrink-0"
                style={{ backgroundColor: entry.color }}
              />
              <span className="text-gray-400 truncate">{entry.name}</span>
            </div>
            <span className="text-white font-mono font-medium tabular-nums">
              {smartFormat(entry.value)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

function MetricsTabImpl({ metricPoints, chartConfig, state }: MetricsTabProps) {
  const [hiddenSeries, setHiddenSeries] = useState<Set<string>>(new Set());
  const [summaryCollapsed, setSummaryCollapsed] = useState(false);

  const toggleSeries = useCallback((key: string) => {
    setHiddenSeries((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  // ── Memo 1: parse series, summaries, colors, group defs (stable across legend toggles) ──
  const { seriesMap, summaries, colorMap, groupDefs, totalSteps, totalMetrics, uniqueRuns } =
    useMemo(() => {
      if (metricPoints.length === 0) {
        return {
          seriesMap: new Map<string, MetricPoint[]>(),
          summaries: [] as MetricSummary[],
          colorMap: new Map<string, string>(),
          groupDefs: [] as { title: string; type: 'line' | 'bar' | 'area'; seriesKeys: string[] }[],
          totalSteps: 0,
          totalMetrics: 0,
          uniqueRuns: 0,
        };
      }

      const sMap = new Map<string, MetricPoint[]>();
      let maxStep = 0;
      const metricNames = new Set<string>();
      const runTagSet = new Set<string>();

      for (const p of metricPoints) {
        const tag = p.run_tag || null;
        if (tag) runTagSet.add(tag);
        metricNames.add(p.name);
        if (p.step > maxStep) maxStep = p.step;
        const seriesKey = tag ? `${p.name} (${tag})` : p.name;
        if (!sMap.has(seriesKey)) sMap.set(seriesKey, []);
        sMap.get(seriesKey)!.push(p);
      }

      const allSeriesKeys = Array.from(sMap.keys());
      const cMap = new Map<string, string>();
      allSeriesKeys.forEach((key, i) => cMap.set(key, PALETTE[i % PALETTE.length]));

      const sums: MetricSummary[] = [];
      Array.from(sMap.entries()).forEach(([seriesKey, points]) => {
        const sorted = [...points].sort((a, b) => a.step - b.step);
        const latest = sorted[sorted.length - 1];
        const prev = sorted.length > 1 ? sorted[sorted.length - 2] : null;
        const delta = prev ? latest.value - prev.value : null;
        const lowerBetter = isLowerBetter(latest.name);
        const improving = delta !== null ? (lowerBetter ? delta < 0 : delta > 0) : null;
        const best = sorted.reduce(
          (b, p) => (lowerBetter ? Math.min(b, p.value) : Math.max(b, p.value)),
          lowerBetter ? Infinity : -Infinity,
        );
        sums.push({
          name: seriesKey,
          displayName: prettyMetricName(latest.name),
          runTag: latest.run_tag || null,
          latest: latest.value,
          best,
          delta,
          improving,
          lowerIsBetter: lowerBetter,
          steps: sorted.length,
        });
      });

      let gDefs: { title: string; type: 'line' | 'bar' | 'area'; seriesKeys: string[] }[];
      if (chartConfig && chartConfig.charts.length > 0) {
        gDefs = chartConfig.charts.map((c) => {
          const matchedKeys: string[] = [];
          for (const metricName of c.metrics) {
            for (const sk of allSeriesKeys) {
              if (sk === metricName || sk.startsWith(metricName + ' (')) {
                if (!matchedKeys.includes(sk)) matchedKeys.push(sk);
              }
            }
          }
          return { title: c.title, type: c.type || 'line', seriesKeys: matchedKeys };
        });
        const coveredKeys = new Set(gDefs.flatMap((g) => g.seriesKeys));
        const uncovered = allSeriesKeys.filter((k) => !coveredKeys.has(k));
        if (uncovered.length > 0)
          gDefs.push({ title: 'Other Metrics', type: 'line', seriesKeys: uncovered });
      } else {
        const groupMap = new Map<string, string[]>();
        for (const sk of allSeriesKeys) {
          const baseName = sMap.get(sk)?.[0]?.name || sk;
          const group = inferGroup(baseName);
          if (!groupMap.has(group)) groupMap.set(group, []);
          groupMap.get(group)!.push(sk);
        }
        const order = ['Loss', 'Performance', 'Other'];
        gDefs = Array.from(groupMap.entries())
          .sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
          .map(([title, keys]) => ({ title, type: 'line' as const, seriesKeys: keys }));
      }

      return {
        seriesMap: sMap,
        summaries: sums,
        colorMap: cMap,
        groupDefs: gDefs,
        totalSteps: maxStep,
        totalMetrics: metricNames.size,
        uniqueRuns: runTagSet.size || 1,
      };
    }, [metricPoints, chartConfig]);

  // ── Memo 2: build chart data (depends on hiddenSeries — cheap rebuild on legend toggle) ──
  const charts = useMemo(() => {
    return groupDefs
      .filter((g) => g.seriesKeys.length > 0)
      .map((g) => {
        const stepMap = new Map<number, Record<string, number>>();
        for (const key of g.seriesKeys) {
          if (hiddenSeries.has(key)) continue;
          const pts = seriesMap.get(key) || [];
          for (const p of pts) {
            if (!stepMap.has(p.step)) stepMap.set(p.step, { step: p.step });
            stepMap.get(p.step)![key] = p.value;
          }
        }
        return {
          title: g.title,
          type: g.type,
          data: Array.from(stepMap.values()).sort((a, b) => a.step - b.step),
          seriesKeys: g.seriesKeys,
        };
      });
  }, [groupDefs, seriesMap, hiddenSeries]);

  const color = useCallback((key: string) => colorMap.get(key) || PALETTE[0], [colorMap]);

  // ── Empty state ──
  if (metricPoints.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full px-8">
        <div className="w-16 h-16 rounded-2xl bg-white/[0.03] border border-white/[0.06] flex items-center justify-center mb-4">
          <Activity className="w-7 h-7 text-gray-600" />
        </div>
        <p className="text-sm font-medium text-gray-400 mb-1">Metrics Dashboard</p>
        <p className="text-xs text-gray-600 text-center max-w-[260px]">
          {state.includes('running')
            ? 'An agent is running. Metrics will stream here as it logs them via trainable.log(...).'
            : 'Metrics appear here when an agent calls trainable.log(step=..., metrics={...}) during training.'}
        </p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-black">
      {/* ── Top bar ── */}
      <div className="sticky top-0 z-10 bg-black/90 backdrop-blur-sm border-b border-white/[0.06] px-4 py-2.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Activity className="w-4 h-4 text-emerald-400" />
          <span className="text-xs font-semibold text-gray-300">Metrics</span>
          {state.includes('running') && (
            <span className="flex items-center gap-1 text-[10px] text-emerald-400 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Live
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-[10px] text-gray-600">
          <span className="flex items-center gap-1">
            <Hash className="w-3 h-3" />
            {totalSteps} steps
          </span>
          <span className="flex items-center gap-1">
            <Layers className="w-3 h-3" />
            {totalMetrics} metrics
          </span>
          <span className="flex items-center gap-1">
            <BarChart3 className="w-3 h-3" />
            {uniqueRuns} run{uniqueRuns !== 1 ? 's' : ''}
          </span>
        </div>
      </div>

      <div className="p-4 space-y-3">
        {/* ── Summary table ── */}
        <div className="bg-white/[0.02] border border-white/[0.06] rounded-lg overflow-hidden">
          <button
            onClick={() => setSummaryCollapsed((prev) => !prev)}
            className="w-full flex items-center justify-between px-3 py-2 hover:bg-white/[0.02] transition-colors"
          >
            <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
              Summary
            </span>
            {summaryCollapsed ? (
              <ChevronRight className="w-3.5 h-3.5 text-gray-600" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5 text-gray-600" />
            )}
          </button>
          {!summaryCollapsed && (
            <div className="border-t border-white/[0.04]">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-gray-600 text-[10px] uppercase tracking-wider">
                    <th className="text-left py-1.5 px-3 font-medium">Metric</th>
                    {uniqueRuns > 1 && <th className="text-left py-1.5 px-3 font-medium">Run</th>}
                    <th className="text-right py-1.5 px-3 font-medium">Latest</th>
                    <th className="text-right py-1.5 px-3 font-medium">Best</th>
                    <th className="text-right py-1.5 px-3 font-medium">Delta</th>
                    <th className="text-right py-1.5 px-3 font-medium">Steps</th>
                  </tr>
                </thead>
                <tbody>
                  {summaries.map((s) => {
                    const c = color(s.name);
                    const isHidden = hiddenSeries.has(s.name);
                    return (
                      <tr
                        key={s.name}
                        onClick={() => toggleSeries(s.name)}
                        className={`border-t border-white/[0.03] cursor-pointer transition-colors ${
                          isHidden ? 'opacity-30' : 'hover:bg-white/[0.03]'
                        }`}
                      >
                        <td className="py-1.5 px-3">
                          <div className="flex items-center gap-2">
                            <div
                              className="w-2.5 h-[3px] rounded-full shrink-0"
                              style={{ backgroundColor: c }}
                            />
                            <span className="text-gray-300 font-medium truncate">
                              {s.displayName}
                            </span>
                          </div>
                        </td>
                        {uniqueRuns > 1 && (
                          <td className="py-1.5 px-3">
                            {s.runTag && (
                              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-white/[0.05] text-gray-400">
                                {s.runTag}
                              </span>
                            )}
                          </td>
                        )}
                        <td className="py-1.5 px-3 text-right font-mono text-gray-200 tabular-nums">
                          {smartFormat(s.latest)}
                        </td>
                        <td className="py-1.5 px-3 text-right font-mono tabular-nums">
                          <span
                            className={s.latest === s.best ? 'text-emerald-400' : 'text-gray-500'}
                          >
                            {smartFormat(s.best)}
                          </span>
                        </td>
                        <td className="py-1.5 px-3 text-right">
                          {s.delta !== null ? (
                            <span
                              className={`inline-flex items-center gap-0.5 font-mono tabular-nums ${
                                s.improving === true
                                  ? 'text-emerald-400'
                                  : s.improving === false
                                    ? 'text-red-400'
                                    : 'text-gray-600'
                              }`}
                            >
                              {s.improving === true ? (
                                <TrendingUp className="w-3 h-3" />
                              ) : s.improving === false ? (
                                <TrendingDown className="w-3 h-3" />
                              ) : (
                                <Minus className="w-3 h-3" />
                              )}
                              {s.delta > 0 ? '+' : ''}
                              {smartFormat(s.delta)}
                            </span>
                          ) : (
                            <span className="text-gray-700">&mdash;</span>
                          )}
                        </td>
                        <td className="py-1.5 px-3 text-right text-gray-600 tabular-nums">
                          {s.steps}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ── Charts ── */}
        <div className="grid grid-cols-1 2xl:grid-cols-2 gap-3">
          {charts.map((chart) => {
            const visibleKeys = chart.seriesKeys.filter((k) => !hiddenSeries.has(k));
            return (
              <div
                key={chart.title}
                className="bg-white/[0.02] border border-white/[0.06] rounded-lg overflow-hidden"
              >
                {/* Header */}
                <div className="flex items-center justify-between px-3 py-2 border-b border-white/[0.04]">
                  <span className="text-[11px] font-semibold text-gray-400">{chart.title}</span>
                  <span className="text-[10px] text-gray-600 tabular-nums">
                    {chart.data.length} pts
                  </span>
                </div>

                {/* Chart */}
                <div className="px-2 pt-2 pb-0">
                  <div className="h-[200px]">
                    <ResponsiveContainer width="100%" height="100%">
                      {renderChart(chart, visibleKeys, color)}
                    </ResponsiveContainer>
                  </div>
                </div>

                {/* Legend — always visible */}
                <div className="flex flex-wrap gap-x-4 gap-y-1.5 px-3 py-2.5 border-t border-white/[0.04]">
                  {chart.seriesKeys.map((key) => {
                    const hidden = hiddenSeries.has(key);
                    const c = color(key);
                    // Find latest value for this series
                    const lastPt = chart.data[chart.data.length - 1];
                    const val = lastPt?.[key];
                    return (
                      <button
                        key={key}
                        onClick={() => toggleSeries(key)}
                        className={`flex items-center gap-1.5 transition-opacity group ${
                          hidden ? 'opacity-25 hover:opacity-50' : 'opacity-100'
                        }`}
                      >
                        <svg width="14" height="8" viewBox="0 0 14 8" className="shrink-0">
                          <line
                            x1="0"
                            y1="4"
                            x2="14"
                            y2="4"
                            stroke={c}
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeDasharray={hidden ? '2 2' : 'none'}
                          />
                          <circle
                            cx="7"
                            cy="4"
                            r="2.5"
                            fill={hidden ? 'transparent' : c}
                            stroke={c}
                            strokeWidth="1"
                          />
                        </svg>
                        <span className="text-[11px] text-gray-400 group-hover:text-gray-300 transition-colors">
                          {key}
                        </span>
                        {val !== undefined && !hidden && (
                          <span className="text-[10px] font-mono text-gray-600 tabular-nums">
                            {compactFormat(val)}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// memo: parent re-renders on every chat / SSE / task update, but our props
// (metricPoints, chartConfig, state) only change when new metrics arrive or
// the session transitions. Skipping reconciliation when refs are stable
// avoids re-running the chart memos + Recharts ResponsiveContainer measure.
const MetricsTab = memo(MetricsTabImpl);
export default MetricsTab;

// ---------------------------------------------------------------------------
// Chart renderer
// ---------------------------------------------------------------------------

function renderChart(chart: ChartGroup, visibleKeys: string[], getColor: (key: string) => string) {
  if (visibleKeys.length === 0 || chart.data.length === 0) {
    return (
      <LineChart data={[]}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1a1a1a" />
      </LineChart>
    );
  }

  const sharedElements = (
    <>
      <CartesianGrid strokeDasharray="4 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
      <XAxis
        dataKey="step"
        stroke="transparent"
        tick={{ fill: '#555', fontSize: 10 }}
        tickLine={false}
        axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
      />
      <YAxis
        stroke="transparent"
        tick={{ fill: '#555', fontSize: 10 }}
        tickLine={false}
        axisLine={false}
        width={55}
        tickFormatter={compactFormat}
      />
      <Tooltip
        content={<ChartTooltip />}
        cursor={{ stroke: 'rgba(255,255,255,0.08)', strokeWidth: 1 }}
      />
    </>
  );

  // Use area for 1 series, line for multi
  if (visibleKeys.length === 1) {
    const key = visibleKeys[0];
    const c = getColor(key);
    const gradId = `grad-${key.replace(/\W/g, '_')}`;
    return (
      <AreaChart data={chart.data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={c} stopOpacity={0.2} />
            <stop offset="100%" stopColor={c} stopOpacity={0} />
          </linearGradient>
        </defs>
        {sharedElements}
        <Area
          type="monotone"
          dataKey={key}
          stroke={c}
          fill={`url(#${gradId})`}
          strokeWidth={1.5}
          dot={false}
          animationDuration={300}
          connectNulls
        />
      </AreaChart>
    );
  }

  return (
    <LineChart data={chart.data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
      {sharedElements}
      {visibleKeys.map((key) => (
        <Line
          key={key}
          type="monotone"
          dataKey={key}
          stroke={getColor(key)}
          strokeWidth={1.5}
          dot={false}
          animationDuration={300}
          connectNulls
        />
      ))}
    </LineChart>
  );
}
