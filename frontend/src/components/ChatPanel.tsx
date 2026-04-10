'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import {
  Send,
  Square,
  Loader2,
  Bot,
  CheckCircle2,
  AlertCircle,
  ChevronRight,
  ArrowUp,
  Sparkles,
  FileSearch,
  Wrench,
  Terminal,
  FileCode,
  Search,
  Globe,
  FolderSearch,
  Pencil,
  FileOutput,
  ListChecks,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Message, SSEEvent } from '@/lib/types';
import MessageBubble from './MessageBubble';

interface ChatPanelProps {
  messages: Message[];
  streamEvents: SSEEvent[];
  streamingText: string;
  onSendMessage: (content: string) => void;
  onStop?: () => void;
  isRunning: boolean;
}

/* ── Tool metadata for color-coded cards (inspired by simp) ── */

const TOOL_META: Record<string, { icon: typeof Terminal; color: string; label: string }> = {
  execute_code: { icon: Terminal, color: '#F59E0B', label: 'Execute' },
  read_file: { icon: FileSearch, color: '#79c0ff', label: 'Read' },
  write_file: { icon: FileOutput, color: '#7ee787', label: 'Write' },
  edit_file: { icon: Pencil, color: '#d2a8ff', label: 'Edit' },
  search: { icon: Search, color: '#56d4dd', label: 'Search' },
  grep: { icon: Search, color: '#56d4dd', label: 'Grep' },
  glob: { icon: FolderSearch, color: '#ffa657', label: 'Glob' },
  web_fetch: { icon: Globe, color: '#f778ba', label: 'Fetch' },
  web_search: { icon: Globe, color: '#f778ba', label: 'Search' },
  list_files: { icon: FolderSearch, color: '#ffa657', label: 'List Files' },
  create_task: { icon: ListChecks, color: '#7ee787', label: 'Task' },
};

const FUN_VERBS = [
  'Analyzing', 'Crunching', 'Processing', 'Investigating', 'Computing',
  'Evaluating', 'Examining', 'Wrangling', 'Parsing', 'Compiling',
];

function useFunVerb(isAnimating: boolean) {
  const [index, setIndex] = useState(() => Math.floor(Math.random() * FUN_VERBS.length));
  useEffect(() => {
    if (!isAnimating) return;
    const id = setInterval(() => {
      setIndex((prev) => (prev + 1) % FUN_VERBS.length);
    }, 8000);
    return () => clearInterval(id);
  }, [isAnimating]);
  return FUN_VERBS[index];
}

/* ── Tool Card ── */

function ToolCard({ event }: { event: SSEEvent }) {
  const data = event.data as any;
  const isStart = event.type === 'tool_start';
  const [collapsed, setCollapsed] = useState(true);
  const funVerb = useFunVerb(isStart);
  const [startedAt] = useState(() => Date.now());
  const [elapsed, setElapsed] = useState(0);

  const toolName = data.tool || 'execute_code';
  const meta = TOOL_META[toolName] || { icon: Wrench, color: '#9ca3af', label: toolName };
  const Icon = meta.icon;

  const code = data.input?.code;
  const output = data.output;

  // Summarize what the tool is doing
  const summary = data.input?.file_path
    ? data.input.file_path.split('/').slice(-2).join('/')
    : data.input?.command
      ? data.input.command.length > 50
        ? data.input.command.slice(0, 47) + '...'
        : data.input.command
      : data.input?.pattern
        ? `/${data.input.pattern}/`
        : '';

  useEffect(() => {
    if (!isStart) return;
    const id = setInterval(() => setElapsed(Math.round((Date.now() - startedAt) / 1000)), 1000);
    return () => clearInterval(id);
  }, [isStart, startedAt]);

  return (
    <div className="animate-fade-in my-1">
      <button
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/[0.03] hover:bg-white/[0.06] border border-white/[0.06] transition-colors w-full text-left group"
        onClick={() => setCollapsed((prev) => !prev)}
      >
        {isStart ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" style={{ color: meta.color }} />
        ) : (
          <CheckCircle2 className="w-3.5 h-3.5 text-green-400 shrink-0" />
        )}
        <Icon className="w-3.5 h-3.5 shrink-0" style={{ color: meta.color }} />
        <span className="text-xs font-medium text-gray-400 truncate flex-1">
          {isStart ? funVerb : meta.label}
          {summary && (
            <span className="text-gray-600 ml-1.5 font-normal">{summary}</span>
          )}
        </span>
        {elapsed > 0 && (
          <span className="text-[10px] text-gray-600 tabular-nums shrink-0">{elapsed}s</span>
        )}
        <ChevronRight
          className={`w-3 h-3 text-gray-600 transition-transform duration-150 shrink-0 ${
            !collapsed ? 'rotate-90' : ''
          }`}
        />
      </button>
      {!collapsed && (
        <div className="mt-1 ml-3 border-l-2 border-white/[0.06] pl-3 space-y-1">
          {code && (
            <pre className="text-[11px] text-gray-500 font-mono max-h-32 overflow-y-auto whitespace-pre-wrap bg-black/30 rounded-md p-2">
              {code.length > 500 ? code.slice(0, 500) + '\n...' : code}
            </pre>
          )}
          {output && (
            <pre className="text-[11px] text-green-400/70 font-mono max-h-32 overflow-y-auto whitespace-pre-wrap bg-black/30 rounded-md p-2">
              {output.length > 600 ? output.slice(0, 600) + '\n...' : output}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Code Output ── */

function CodeOutput({ text, stream }: { text: string; stream: string }) {
  return (
    <div className="ml-1 my-0.5">
      <pre
        className={`text-[11px] font-mono whitespace-pre-wrap break-all px-3 py-1 rounded ${
          stream === 'stderr'
            ? 'text-red-400/60 bg-red-500/5'
            : 'text-gray-600 bg-white/[0.02]'
        }`}
      >
        {text}
      </pre>
    </div>
  );
}

/* ── Welcome Screen ── */

function WelcomeScreen({ onSuggestion }: { onSuggestion: (text: string) => void }) {
  const suggestions = [
    { icon: FileSearch, text: 'Explore the dataset and summarize key stats' },
    { icon: Sparkles, text: 'Clean the data and handle missing values' },
    { icon: FileCode, text: 'Train a model and show me the results' },
  ];

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 pb-20">
      <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary-500/20 to-emerald-500/20 border border-white/[0.08] flex items-center justify-center mb-5">
        <Sparkles className="w-6 h-6 text-primary-400" />
      </div>
      <h2 className="text-lg font-semibold text-white mb-1">What can I help with?</h2>
      <p className="text-sm text-gray-500 mb-8 text-center max-w-sm">
        Start a conversation or pick a suggestion below to begin working with your data.
      </p>
      <div className="flex flex-col gap-2 w-full max-w-md">
        {suggestions.map((s, i) => {
          const SIcon = s.icon;
          return (
            <button
              key={i}
              onClick={() => onSuggestion(s.text)}
              className="flex items-center gap-3 px-4 py-3 rounded-xl bg-white/[0.03] hover:bg-white/[0.06] border border-white/[0.06] hover:border-white/[0.1] transition-all text-left group"
            >
              <SIcon className="w-4 h-4 text-gray-500 group-hover:text-primary-400 transition-colors shrink-0" />
              <span className="text-sm text-gray-400 group-hover:text-gray-300 transition-colors">
                {s.text}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ── Auto-growing Textarea ── */

function AutoGrowTextarea({
  value,
  onChange,
  onSubmit,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  placeholder: string;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (ref.current) {
      ref.current.style.height = 'auto';
      ref.current.style.height = Math.min(ref.current.scrollHeight, 200) + 'px';
    }
  }, [value]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    }
  };

  return (
    <textarea
      ref={ref}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={handleKeyDown}
      placeholder={placeholder}
      disabled={disabled}
      rows={1}
      className="flex-1 bg-transparent text-white text-sm placeholder-gray-500 focus:outline-none resize-none py-2.5 px-1 leading-relaxed"
    />
  );
}

/* ── Main ChatPanel ── */

export default function ChatPanel({
  messages,
  streamEvents,
  streamingText,
  onSendMessage,
  onStop,
  isRunning,
}: ChatPanelProps) {
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamEvents, streamingText]);

  const handleSubmit = useCallback(() => {
    if (!input.trim()) return;
    onSendMessage(input.trim());
    setInput('');
  }, [input, onSendMessage]);

  const isEmpty = messages.length === 0 && streamEvents.length === 0 && !streamingText && !isRunning;

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a]">
      {isEmpty ? (
        <WelcomeScreen onSuggestion={(text) => { setInput(text); }} />
      ) : (
        /* Messages area */
        <div ref={scrollContainerRef} className="flex-1 overflow-y-auto">
          <div className="max-w-2xl mx-auto px-4 py-6 space-y-4">
            {/* Persisted messages */}
            {messages.map((msg) => (
              <MessageBubble key={msg.id} role={msg.role} content={msg.content} />
            ))}

            {/* Live stream events */}
            {streamEvents.map((event, i) => {
              switch (event.type) {
                case 'tool_start':
                case 'tool_end':
                  return <ToolCard key={`ev-${i}`} event={event} />;
                case 'code_output': {
                  const d = event.data as any;
                  return <CodeOutput key={`ev-${i}`} text={d.text} stream={d.stream} />;
                }
                case 'agent_message': {
                  const d = event.data as any;
                  return (
                    <div key={`ev-${i}`} className="animate-fade-in">
                      <div className="max-w-[85%] text-sm leading-relaxed text-gray-200 markdown-chat">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{d.text}</ReactMarkdown>
                      </div>
                    </div>
                  );
                }
                case 'agent_error': {
                  const d = event.data as any;
                  return (
                    <div
                      key={`ev-${i}`}
                      className="flex items-center gap-2 px-3 py-2 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400"
                    >
                      <AlertCircle className="w-4 h-4 shrink-0" />
                      <span className="truncate">{d.error}</span>
                    </div>
                  );
                }
                case 'state_change': {
                  const d = event.data as any;
                  return (
                    <div key={`ev-${i}`} className="flex items-center justify-center py-2">
                      <span className="text-[10px] text-gray-600 bg-white/[0.03] px-3 py-1 rounded-full border border-white/[0.06]">
                        {d.state}
                      </span>
                    </div>
                  );
                }
                default:
                  return null;
              }
            })}

            {/* Streaming text cursor */}
            {streamingText && (
              <div className="animate-fade-in">
                <div className="max-w-[85%] text-sm leading-relaxed text-gray-200 markdown-chat">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingText}</ReactMarkdown>
                  <span className="inline-block w-1.5 h-4 bg-primary-500 rounded-sm ml-0.5 animate-blink align-text-bottom" />
                </div>
              </div>
            )}

            {/* Thinking indicator */}
            {isRunning && streamEvents.length === 0 && !streamingText && (
              <div className="flex items-center gap-2.5 py-3">
                <div className="flex gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-typing" style={{ animationDelay: '0ms' }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-typing" style={{ animationDelay: '150ms' }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-typing" style={{ animationDelay: '300ms' }} />
                </div>
                <span className="text-xs text-gray-600">Thinking...</span>
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        </div>
      )}

      {/* Input bar — always at bottom */}
      <div className="px-4 py-3 shrink-0">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-end gap-2 bg-white/[0.05] border border-white/[0.08] rounded-2xl px-4 py-1 focus-within:border-white/[0.15] transition-colors">
            <AutoGrowTextarea
              value={input}
              onChange={setInput}
              onSubmit={handleSubmit}
              placeholder="Message trainable..."
            />
            {isRunning && !input.trim() && onStop ? (
              <button
                onClick={onStop}
                className="p-2 mb-1 bg-white/10 hover:bg-red-500/20 rounded-xl transition-colors shrink-0 group"
                title="Stop"
              >
                <Square className="w-4 h-4 text-gray-400 group-hover:text-red-400 transition-colors" />
              </button>
            ) : (
              <button
                onClick={handleSubmit}
                disabled={!input.trim()}
                className="p-2 mb-1 bg-white disabled:bg-white/10 hover:bg-gray-200 disabled:hover:bg-white/10 rounded-xl transition-colors shrink-0 disabled:cursor-not-allowed"
              >
                <ArrowUp className="w-4 h-4 text-black disabled:text-gray-600" />
              </button>
            )}
          </div>
          <p className="text-[10px] text-gray-600 text-center mt-2">
            Trainable can make mistakes. Verify important results.
          </p>
        </div>
      </div>
    </div>
  );
}
