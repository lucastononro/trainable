/** Hand-off of a suggested first prompt from the sample-dataset gallery to
 * the studio chat input. The gallery stashes the prompt keyed to the freshly
 * created session before navigating; the studio consumes it exactly once
 * when that session becomes active. sessionStorage keeps it tab-local and
 * makes it evaporate with the tab. */

const KEY = 'trainable:suggested-prompt';

export function stashSuggestedPrompt(sessionId: string, prompt: string): void {
  if (!prompt.trim()) return;
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ sessionId, prompt }));
  } catch {
    // Storage unavailable (private mode/quota) — the user just types instead.
  }
}

/** Return the stashed prompt if it belongs to `sessionId`, clearing it. */
export function takeSuggestedPrompt(sessionId: string): string | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { sessionId?: string; prompt?: string };
    if (parsed.sessionId !== sessionId || !parsed.prompt) return null;
    sessionStorage.removeItem(KEY);
    return parsed.prompt;
  } catch {
    return null;
  }
}
