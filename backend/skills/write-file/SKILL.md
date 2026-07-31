---
name: write-file
description: Create or replace a file in the session workspace (e.g. a reusable module under src/). Emits file_created / file_updated for the UI; does NOT write into the scripts/ audit log — authoring is not an execution step.
when_to_use: Authoring a new file or replacing a whole file — reusable modules under src/, config files, reports. For changing part of an existing file use edit-file; for one-shot throwaway Python use execute-code.
version: '0.1'
kind: capability
---

# write-file

Write `content` to `path` inside the session workspace. This is the
authoring verb: use it to create reusable modules (e.g. `src/loaders.py`)
instead of running `Path(...).write_text(...)` inside execute-code.

Files written under `src/` are importable from later execute-code calls
and notebook cells (`src/` is on `sys.path`, the session workspace is the
cwd). Module names must be plain Python identifiers — no hyphens, no
step prefixes.

Unlike execute-code, write-file does NOT auto-save anything into
`scripts/` — that directory is the audit log of what *ran*, and
authoring a file is not an execution step. The UI is notified via a
`file_created` event (new file) or `file_updated` event (overwrite).

## When to use
Authoring a new file or wholesale replacing one. To change part of an
existing file, use edit-file. To execute a file, use run-file.

## Inputs
- `path` (required): target file. Absolute under `/sessions/{sid}/` (or
  the sandbox spelling `/data/sessions/{sid}/`), or relative to the
  session workspace (e.g. `src/loaders.py`). Paths outside the session
  workspace are rejected.
- `content` (required): full file content (text).
- `overwrite` (optional, default `true`): when false, writing over an
  existing file returns an error instead of clobbering it.

## Returns
```json
{ "ok": true, "path": "/sessions/{sid}/src/loaders.py", "bytes_written": 512, "created": true }
```

## Failure modes
- Returns error when `path` is empty, escapes the session workspace, or
  points outside `/sessions/{sid}/`.
- Returns error when the file exists and `overwrite` is false.
