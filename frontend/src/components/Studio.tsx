'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { ArrowLeft, PanelRightOpen, PanelRightClose } from 'lucide-react';
import { ExperimentDetail, Message, SSEEvent, MetricPoint, Stage, Artifact } from '@/lib/types';
import StageNav from './StageNav';
import ChatPanel from './ChatPanel';
import CanvasPanel from './CanvasPanel';

interface StudioProps {
  experiment: ExperimentDetail;
  sessionId: string;
  state: string;
  messages: Message[];
  artifacts: Artifact[];
  streamEvents: SSEEvent[];
  metricPoints: MetricPoint[];
  streamingText: string;
  report: string;
  onStartStage: (stage: Stage) => void;
  onSendMessage: (content: string) => void;
  onStop?: () => void;
}

export default function Studio({
  experiment,
  sessionId,
  state,
  messages,
  artifacts,
  streamEvents,
  metricPoints,
  streamingText,
  report,
  onStartStage,
  onSendMessage,
  onStop,
}: StudioProps) {
  const isRunning = state.includes('running');
  const [canvasOpen, setCanvasOpen] = useState(false);
  const autoOpenedRef = useRef(false);

  // Auto-open canvas when meaningful content arrives
  const hasCanvasContent = report.length > 0 || artifacts.length > 0 || metricPoints.length > 0;

  useEffect(() => {
    if (hasCanvasContent && !autoOpenedRef.current) {
      setCanvasOpen(true);
      autoOpenedRef.current = true;
    }
  }, [hasCanvasContent]);

  const toggleCanvas = useCallback(() => {
    setCanvasOpen((prev) => !prev);
  }, []);

  const closeCanvas = useCallback(() => {
    setCanvasOpen(false);
  }, []);

  return (
    <div className="h-screen flex flex-col bg-[#0a0a0a]">
      {/* Top bar */}
      <header className="flex items-center gap-3 px-4 py-2 border-b border-white/[0.06] shrink-0 bg-[#0a0a0a]">
        <button
          onClick={() => (window.location.href = '/')}
          className="p-1.5 hover:bg-white/[0.06] rounded-lg transition-colors"
        >
          <ArrowLeft className="w-4 h-4 text-gray-500" />
        </button>
        <a href="/" className="flex items-center shrink-0">
          <img src="/logo-brain.png" alt="Trainable" className="h-6 w-auto" />
        </a>
        <div className="w-px h-4 bg-white/[0.08]" />
        <h1 className="text-sm font-medium text-gray-300 truncate">{experiment.name}</h1>
        <div className="flex-1" />
        <StageNav state={state} onStartStage={onStartStage} onStop={onStop} isRunning={isRunning} />
        <div className="w-px h-4 bg-white/[0.08]" />
        <button
          onClick={toggleCanvas}
          className={`p-1.5 rounded-lg transition-colors ${
            canvasOpen
              ? 'bg-primary-500/10 text-primary-400 hover:bg-primary-500/20'
              : 'text-gray-500 hover:bg-white/[0.06] hover:text-gray-300'
          }`}
          title={canvasOpen ? 'Close canvas' : 'Open canvas'}
        >
          {canvasOpen ? (
            <PanelRightClose className="w-4 h-4" />
          ) : (
            <PanelRightOpen className="w-4 h-4" />
          )}
          {hasCanvasContent && !canvasOpen && (
            <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-primary-500" />
          )}
        </button>
      </header>

      {/* Split pane */}
      <PanelGroup direction="horizontal" className="flex-1">
        <Panel defaultSize={canvasOpen ? 40 : 100} minSize={30}>
          <ChatPanel
            messages={messages}
            streamEvents={streamEvents}
            streamingText={streamingText}
            onSendMessage={onSendMessage}
            onStop={onStop}
            isRunning={isRunning}
          />
        </Panel>
        {canvasOpen && (
          <>
            <PanelResizeHandle className="w-px bg-white/[0.06] hover:bg-primary-500/50 transition-colors" />
            <Panel defaultSize={60} minSize={30}>
              <CanvasPanel
                report={report}
                artifacts={artifacts}
                metricPoints={metricPoints}
                chartConfig={null}
                state={state}
                onClose={closeCanvas}
              />
            </Panel>
          </>
        )}
      </PanelGroup>
    </div>
  );
}
