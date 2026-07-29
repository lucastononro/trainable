---
name: edit-file
description: Change part of an existing file in the session workspace — exact-anchor replace, or full overwrite as an escape hatch. Emits file_updated; never creates files and never writes into the scripts/ audit log.
when_to_use: Modifying an existing module (e.g. adding a function to src/loaders.py) instead of re-pasting the whole file. For new files use write-file; for one-shot Python use execute-code.
version: '0.1'
kind: capability
---

# edit-file

Edit an existing file in place. Two modes:

- `mode="replace"` (default): exact-anchor substitution. `old` must match
  the current file content **exactly once** — zero matches or multiple
  matches both return an error, so the edit can never land in the wrong
  place. This is the same anchor model as the Edit tool you already know.
- `mode="overwrite"`: `content` replaces the whole file. Escape hatch for
  when the file is too dirty to anchor — prefer write-file for planned
  rewrites and replace-mode for surgical changes.

Edit, don't repaste: if you are about to write-file a near-duplicate of
an existing module, edit-file the existing one instead. Emits a
`file_updated` event (never `file_created` — the file must already
exist). Does NOT write into `scripts/` — authoring is not an execution
step.

## When to use
Changing part of an existing file — adding/removing/renaming a function
in `src/`, fixing a constant, updating a config.

## Inputs
- `path` (required): file to edit. Same resolution rules as write-file.
- `mode` (optional, default `"replace"`): `"replace"` or `"overwrite"`.
- `old` (required for replace): exact string to find; must occur exactly once.
- `new` (required for replace): replacement string.
- `content` (required for overwrite): full new file content.

## Returns
```json
{ "ok": true, "path": "/sessions/{sid}/src/loaders.py", "bytes_written": 540, "replacements": 1 }
```

## Failure modes
- Returns error when the file does not exist (use write-file to create it).
- Returns error in replace mode when `old` is not found, or matches more
  than once — re-read the file and pick a longer, unique anchor.
- Returns error when `path` escapes the session workspace.
