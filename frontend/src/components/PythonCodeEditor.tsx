'use client';

/**
 * Lightweight Python editor with syntax highlighting.
 *
 * Built on `react-simple-code-editor` + `prismjs` (~30 KB gzipped) —
 * we keep this focused on the /models app.py editor: small font,
 * monospace, dark prism theme, single Python language. If we ever
 * need a heavier editor (multi-file, autocomplete, lint) we can swap
 * to Monaco at that point. For "look at the file, tweak a line, save"
 * this is plenty.
 */

import Editor from 'react-simple-code-editor';
// Import Prism core + the Python grammar. Side-effect imports register
// the language with the global Prism instance the editor reads from.
import Prism from 'prismjs';
import 'prismjs/components/prism-python';
import 'prismjs/themes/prism-tomorrow.css';

interface Props {
  value: string;
  onChange: (next: string) => void;
  /** Minimum height of the textarea region; resizes via CSS. */
  minHeight?: number;
  disabled?: boolean;
}

export default function PythonCodeEditor({
  value,
  onChange,
  minHeight = 320,
  disabled = false,
}: Props) {
  return (
    <div className="rounded-md bg-black/40 overflow-auto" style={{ minHeight }}>
      <Editor
        value={value}
        onValueChange={(code) => !disabled && onChange(code)}
        highlight={(code) => Prism.highlight(code, Prism.languages.python, 'python')}
        padding={10}
        textareaId="serving-app-editor"
        // Tab inserts spaces (Python convention). 4 spaces matches the
        // codegen's default indent so user edits don't drift.
        tabSize={4}
        insertSpaces
        readOnly={disabled}
        spellCheck={false}
        // The editor renders both a hidden <textarea> for input and a
        // <pre> overlay for highlighting; keep them visually identical.
        style={{
          fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
          fontSize: 12,
          lineHeight: '1.5',
          minHeight,
        }}
      />
    </div>
  );
}
