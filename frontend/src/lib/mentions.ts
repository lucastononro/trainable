import type { Draft, DraftToken, Mention } from './types';

export const MENTION_START = '\uE000';
export const MENTION_END = '\uE001';

const SENTINEL_RE = new RegExp(`${MENTION_START}(\\d+)${MENTION_END}`, 'g');

export function draftToWire(draft: Draft): { content: string; mentions: Mention[] } {
  const mentions: Mention[] = [];
  let content = '';
  for (const token of draft) {
    if (token.kind === 'text') {
      content += token.value;
    } else {
      const idx = mentions.length;
      mentions.push({ ...token.mention });
      content += `${MENTION_START}${idx}${MENTION_END}`;
    }
  }
  return { content, mentions };
}

export function wireToDraft(content: string, mentions: Mention[] | undefined): Draft {
  const safe = mentions ?? [];
  if (!content) return [];
  const tokens: Draft = [];
  let cursor = 0;
  SENTINEL_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = SENTINEL_RE.exec(content)) !== null) {
    if (m.index > cursor) {
      tokens.push({ kind: 'text', value: content.slice(cursor, m.index) });
    }
    const idx = parseInt(m[1], 10);
    const ref = safe[idx];
    if (ref) {
      tokens.push({ kind: 'mention', mention: ref });
    }
    cursor = m.index + m[0].length;
  }
  if (cursor < content.length) {
    tokens.push({ kind: 'text', value: content.slice(cursor) });
  }
  return tokens;
}

export function draftToPlainText(draft: Draft): string {
  return draft.map((t) => (t.kind === 'text' ? t.value : `@${t.mention.label}`)).join('');
}

export function isDraftEmpty(draft: Draft): boolean {
  return draft.every((t) => t.kind === 'text' && t.value.trim() === '');
}

export function appendTextToDraft(draft: Draft, value: string): Draft {
  if (!value) return draft;
  const last = draft[draft.length - 1];
  if (last && last.kind === 'text') {
    const next = [...draft];
    next[next.length - 1] = { kind: 'text', value: last.value + value };
    return next;
  }
  return [...draft, { kind: 'text', value }];
}

export function emptyDraft(): Draft {
  return [];
}

export function textTokensOnly(draft: Draft): string {
  return draft.map((t) => (t.kind === 'text' ? t.value : '')).join('');
}

export type { DraftToken };
