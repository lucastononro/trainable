---
name: run-file
description: Execute an existing file from the session workspace in an isolated sandbox (runpy run_path, __main__ semantics). Records a one-line runpy call in the scripts/ audit log — the log says what ran, the body stays on disk.
when_to_use: Running a module you already wrote (e.g. run-file src/training.py) instead of re-pasting its body or its call site into execute-code. For one-shot throwaway Python use execute-code.
version: '0.1'
kind: capability
---

# run-file

Execute a file that already exists in the session workspace. The file
runs in a fresh sandbox under the same preamble as execute-code —
`src/` is on `sys.path`, the session workspace is the cwd — via
`runpy.run_path(path, run_name="__main__")`, so `if __name__ == "__main__":`
blocks fire.

Audit-log behavior: run-file auto-saves a ONE-LINE `runpy.run_path(...)`
call to `scripts/step_NN_run_<name>.py`. The audit log records "we ran
X", not X's body — the body already lives on disk as `src/X.py`. This is
what keeps `scripts/` a meaningful replay log.

## When to use
Running a module you authored with write-file / edit-file. Reuse looks
like `run-file`, authoring looks like `write-file`/`edit-file`, one-shot
exploration looks like `execute-code` — pick the verb that matches the
intent.

## Inputs
- `path` (required): file to execute. Same resolution rules as
  write-file (e.g. `src/training.py` or `/sessions/{sid}/src/training.py`).
- `heavy` (optional, default `false`): set true for heavy ML workloads —
  uses the project's training sandbox profile (GPU + extended timeout),
  same as execute-code's heavy flag.

## Returns
Same shape as execute-code: stdout text (plus exit code / stderr when
the run fails).

## Failure modes
- Returns error when the file does not exist (author it with write-file first).
- Returns error when `path` escapes the session workspace.
- Returns error output (exit code + stderr) when the module itself raises.
