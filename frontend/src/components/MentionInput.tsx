'use client';

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import type { Draft, DraftToken, Experiment, Mention } from '@/lib/types';
import { appendTextToDraft, isDraftEmpty } from '@/lib/mentions';
import MentionPicker from './MentionPicker';

export interface MentionInputHandle {
  focus: () => void;
  clear: () => void;
}

interface Props {
  draft: Draft;
  onChange: (next: Draft) => void;
  onSubmit: () => void;
  placeholder?: string;
  className?: string;
  projectId: string | null;
  experiments: Experiment[];
  attachedFilesInSession: { name: string; sandboxPath: string }[];
}

const MENTION_CLASS =
  'inline-flex items-center gap-1 px-1.5 py-0.5 mx-0.5 rounded-md border text-[12px] font-medium align-baseline select-none';

function pillColorClasses(kind: Mention['kind']): string {
  return kind === 'session'
    ? 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30'
    : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30';
}

// Serialize a Draft into the DOM nodes that live inside the contentEditable.
// Pill nodes carry `contenteditable="false"` + a data-mention attribute with the
// JSON payload so we can read them back on input.
function draftToDOM(root: HTMLDivElement, draft: Draft) {
  root.innerHTML = '';
  const frag = document.createDocumentFragment();
  for (const token of draft) {
    if (token.kind === 'text') {
      if (token.value) frag.appendChild(document.createTextNode(token.value));
    } else {
      const m = token.mention;
      const span = document.createElement('span');
      span.contentEditable = 'false';
      span.setAttribute('data-mention', 'true');
      span.setAttribute('data-payload', JSON.stringify(m));
      span.className = `${MENTION_CLASS} ${pillColorClasses(m.kind)}`;
      const iconSpan = document.createElement('span');
      iconSpan.className = 'w-2 h-2 rounded-full bg-current opacity-70';
      span.appendChild(iconSpan);
      const labelSpan = document.createElement('span');
      labelSpan.className = 'truncate max-w-[180px]';
      labelSpan.textContent = m.label;
      span.appendChild(labelSpan);
      frag.appendChild(span);
    }
  }
  root.appendChild(frag);
}

function readDraftFromDOM(root: HTMLDivElement): Draft {
  const tokens: Draft = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ALL, null);
  let node: Node | null = walker.currentNode;
  // Skip the root itself.
  node = walker.nextNode();
  while (node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const value = node.textContent ?? '';
      if (value) {
        const last = tokens[tokens.length - 1];
        if (last && last.kind === 'text') {
          (last as any).value += value;
        } else {
          tokens.push({ kind: 'text', value });
        }
      }
      node = walker.nextNode();
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const el = node as HTMLElement;
      if (el.getAttribute('data-mention') === 'true') {
        try {
          const payload = JSON.parse(el.getAttribute('data-payload') || '{}') as Mention;
          if (payload.kind && payload.ref && payload.label) {
            tokens.push({ kind: 'mention', mention: payload });
          }
        } catch {
          /* ignore malformed pill */
        }
        // Skip pill subtree.
        const next =
          walker.nextSibling() ||
          (() => {
            // Move past the whole subtree.
            let cur: Node | null = el;
            while (cur && !cur.nextSibling) cur = cur.parentNode;
            return cur ? cur.nextSibling : null;
          })();
        if (next) {
          walker.currentNode = next;
          node = next;
        } else {
          node = null;
        }
      } else if (el.tagName === 'BR') {
        // Collapse <br> to newline — we treat Enter as submit anyway, but paste
        // may inject them.
        const last = tokens[tokens.length - 1];
        if (last && last.kind === 'text') (last as any).value += '\n';
        else tokens.push({ kind: 'text', value: '\n' });
        node = walker.nextNode();
      } else {
        node = walker.nextNode();
      }
    } else {
      node = walker.nextNode();
    }
  }
  return tokens;
}

function getCaretRect(): DOMRect | null {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return null;
  const range = sel.getRangeAt(0).cloneRange();
  const rects = range.getClientRects();
  if (rects.length > 0) return rects[rects.length - 1];
  // Empty line fallback: insert a zero-width space, measure, remove.
  const marker = document.createElement('span');
  marker.appendChild(document.createTextNode('\u200b'));
  range.insertNode(marker);
  const rect = marker.getBoundingClientRect();
  marker.remove();
  return rect;
}

// Given the DOM and current caret, return the `@query` active at caret (if any).
// Returns null if there's no `@` in the current text-run before caret.
function detectActiveMention(
  root: HTMLDivElement,
): { query: string; textNode: Text; atOffset: number } | null {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return null;
  const range = sel.getRangeAt(0);
  if (!root.contains(range.startContainer)) return null;
  if (range.startContainer.nodeType !== Node.TEXT_NODE) return null;
  const textNode = range.startContainer as Text;
  const offset = range.startOffset;
  const text = textNode.textContent ?? '';
  // Find the last @ in this text node before the caret that starts a word.
  const before = text.slice(0, offset);
  const at = before.lastIndexOf('@');
  if (at < 0) return null;
  // @ must be at start of node or preceded by whitespace.
  if (at > 0 && !/\s/.test(before[at - 1])) return null;
  const query = before.slice(at + 1);
  if (/\s/.test(query)) return null; // space closes the mention query
  return { query, textNode, atOffset: at };
}

const MentionInput = forwardRef<MentionInputHandle, Props>(function MentionInput(
  {
    draft,
    onChange,
    onSubmit,
    placeholder,
    className,
    projectId,
    experiments,
    attachedFilesInSession,
  },
  ref,
) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [pickerAnchor, setPickerAnchor] = useState<{ bottom: number; left: number } | null>(null);
  const [pickerQuery, setPickerQuery] = useState('');
  const activeMentionRef = useRef<{ textNode: Text; atOffset: number } | null>(null);
  const draftRef = useRef<Draft>(draft);
  draftRef.current = draft;

  useImperativeHandle(ref, () => ({
    focus: () => rootRef.current?.focus(),
    clear: () => {
      onChange([]);
    },
  }));

  // Sync draft → DOM when draft changes from outside (e.g. after send).
  useLayoutEffect(() => {
    if (!rootRef.current) return;
    const current = readDraftFromDOM(rootRef.current);
    // Cheap structural equality check — avoid tearing caret when the change
    // came from our own input handler (DOM already matches).
    if (JSON.stringify(current) === JSON.stringify(draft)) return;
    draftToDOM(rootRef.current, draft);
  }, [draft]);

  const closePicker = useCallback(() => {
    setPickerAnchor(null);
    setPickerQuery('');
    activeMentionRef.current = null;
  }, []);

  const handleInput = useCallback(() => {
    if (!rootRef.current) return;
    const next = readDraftFromDOM(rootRef.current);
    onChange(next);

    const active = detectActiveMention(rootRef.current);
    if (active) {
      activeMentionRef.current = { textNode: active.textNode, atOffset: active.atOffset };
      setPickerQuery(active.query);
      const rect = getCaretRect();
      if (rect) {
        setPickerAnchor({
          bottom: window.innerHeight - rect.top + 6,
          left: rect.left,
        });
      }
    } else {
      closePicker();
    }
  }, [onChange, closePicker]);

  const handlePick = useCallback(
    (mention: Mention) => {
      const root = rootRef.current;
      const active = activeMentionRef.current;
      if (!root || !active) {
        closePicker();
        return;
      }
      // Replace the `@query` text run with a pill + trailing space.
      const textNode = active.textNode;
      const fullText = textNode.textContent ?? '';
      const before = fullText.slice(0, active.atOffset);
      // Find end of the query run (up to next whitespace or end).
      const afterAt = fullText.slice(active.atOffset + 1);
      const endMatch = afterAt.search(/\s/);
      const queryEnd = active.atOffset + 1 + (endMatch === -1 ? afterAt.length : endMatch);
      const after = fullText.slice(queryEnd);

      const parent = textNode.parentNode;
      if (!parent) {
        closePicker();
        return;
      }
      const beforeNode = document.createTextNode(before);
      parent.insertBefore(beforeNode, textNode);

      const pill = document.createElement('span');
      pill.contentEditable = 'false';
      pill.setAttribute('data-mention', 'true');
      pill.setAttribute('data-payload', JSON.stringify(mention));
      pill.className = `${MENTION_CLASS} ${pillColorClasses(mention.kind)}`;
      const dot = document.createElement('span');
      dot.className = 'w-2 h-2 rounded-full bg-current opacity-70';
      pill.appendChild(dot);
      const labelSpan = document.createElement('span');
      labelSpan.className = 'truncate max-w-[180px]';
      labelSpan.textContent = mention.label;
      pill.appendChild(labelSpan);
      parent.insertBefore(pill, textNode);

      const trailing = document.createTextNode('\u00a0' + after);
      parent.insertBefore(trailing, textNode);
      parent.removeChild(textNode);

      // Place caret just after the pill.
      const sel = window.getSelection();
      if (sel) {
        const range = document.createRange();
        range.setStart(trailing, 1);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
      }

      closePicker();
      if (rootRef.current) onChange(readDraftFromDOM(rootRef.current));
    },
    [closePicker, onChange],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (pickerAnchor) {
        // Let the picker handle Arrow/Enter/Esc/Tab.
        if (['ArrowUp', 'ArrowDown', 'Enter', 'Escape', 'Tab'].includes(e.key)) return;
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!isDraftEmpty(draftRef.current)) onSubmit();
      }
    },
    [pickerAnchor, onSubmit],
  );

  const handlePaste = useCallback((e: React.ClipboardEvent<HTMLDivElement>) => {
    e.preventDefault();
    const text = e.clipboardData.getData('text/plain');
    if (!text) return;
    document.execCommand('insertText', false, text);
  }, []);

  // When editing the DOM directly via insertNode etc we must trigger a
  // synthetic input so consumers re-render.
  useEffect(() => {
    // First mount: paint the initial draft.
    if (rootRef.current) draftToDOM(rootRef.current, draft);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const showPlaceholder = isDraftEmpty(draft) && !pickerAnchor;

  return (
    <>
      <div className="relative flex-1 min-w-0">
        <div
          ref={rootRef}
          role="textbox"
          contentEditable
          suppressContentEditableWarning
          onInput={handleInput}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onBlur={() => {
            // Let picker click events finish before closing.
            setTimeout(() => {
              if (!document.activeElement?.closest('[data-mention-picker="true"]')) {
                closePicker();
              }
            }, 150);
          }}
          className={`${
            className ?? ''
          } min-h-[24px] text-sm text-white placeholder-gray-500 focus:outline-none leading-6 whitespace-pre-wrap break-words`}
        />
        {showPlaceholder && (
          <span className="pointer-events-none absolute left-0 top-0 text-sm text-gray-500 leading-6">
            {placeholder}
          </span>
        )}
      </div>
      {pickerAnchor &&
        typeof document !== 'undefined' &&
        createPortal(
          <div data-mention-picker="true">
            <MentionPicker
              projectId={projectId}
              experiments={experiments}
              attachedFilesInSession={attachedFilesInSession}
              query={pickerQuery}
              anchor={pickerAnchor}
              onPick={handlePick}
              onClose={closePicker}
            />
          </div>,
          document.body,
        )}
    </>
  );
});

export default MentionInput;
