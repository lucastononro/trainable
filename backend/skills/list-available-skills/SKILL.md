---
name: list-available-skills
description: List skills available to this agent. A skill is a packaged capability
when_to_use: Discover installed skills before deciding which to load.
version: '0.1'
kind: capability
---

# list-available-skills

List skills available to this agent. A skill is a packaged capability
(markdown + scripts/templates) maintained by domain experts — load one
via `use_skill(slug)` when its description matches the task at hand.

Each entry includes:
  - slug: the identifier you pass to `use_skill`
  - name: human label
  - description: one-line summary
  - when_to_use: hint for when this skill helps
  - files: count of supporting files (templates, scripts) bundled

Skills are loaded lazily — call this first, pick the most relevant
skill, then call `use_skill` to read its instructions and file manifest.

## When to use
Discover installed skills before deciding which to load.
