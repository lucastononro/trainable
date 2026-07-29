---
name: use-skill
description: Load a skill's full body — its instructions, methodology, and a manifest
when_to_use: Load a knowledge skill's full body and file manifest.
version: '0.1'
kind: capability
---

# use-skill

Load a skill's full body — its instructions, methodology, and a manifest
of supporting files (templates, scripts) you can read or execute.

Returns:
  - body: the skill's SKILL.md content (the methodology / playbook)
  - files: list of {path, size, sandbox_path} — every supporting file
    bundled with the skill, mounted read-only in the sandbox at
    /skills/<slug>/. Read or import them directly from execute_code.
  - sandbox_root: /skills/<slug> — the directory all files are under.

Workflow:
  1. Call `list_available_skills` first to discover what's installed.
  2. Pick the slug most relevant to the task.
  3. Call `use_skill(slug)` to read the full body — follow its
     instructions, and use the bundled scripts via execute_code.

## When to use
Load a knowledge skill's full body and file manifest.
