# AGENTS.md — backend/skills

Skills are the tools agents call. **One folder per skill.** Each skill is a self-contained unit with a contract, a prompt, and a handler.

## The three files

```
backend/skills/<skill-name>/
├── SKILL.md       Markdown — what the skill does, when to use, examples (LLM-facing)
├── schema.yaml    YAML — name, description, input/output JSON schema (machine-facing)
└── handler.py     Python — async function that executes the skill
```

That's it. No `__init__.py` boilerplate beyond what's auto-generated. The skill registry (`services/skills/registry.py`) discovers folders by walking this directory.

## SKILL.md contract

```markdown
---
name: my-skill
description: One sentence the LLM sees in tool listings.
when_to_use: One sentence telling the LLM the trigger.
version: '0.1'
kind: capability        # or 'read', 'write', 'meta'
---

# my-skill

Two-to-five paragraph explanation of what the skill does, what files it touches, what to expect back.

## When to use
{Same as the frontmatter `when_to_use`, can be expanded.}

## Inputs
- `arg1` (required): description.
- `arg2` (optional, default `X`): description.

## Returns
```json
{ "id": "...", "field": "..." }
```

## Failure modes
- Returns error when …
- Returns error when …
```

**The LLM reads SKILL.md verbatim.** Write it as if you're briefing a new contractor: be specific about what data they get, what they return, and what happens on failure. Vague SKILL.md → vague tool calls.

## schema.yaml contract

```yaml
name: my-skill
description: Same one-liner from SKILL.md frontmatter.
input_schema:
  type: object
  required: [arg1]
  properties:
    arg1:
      type: string
      description: ...
    arg2:
      type: integer
      default: 5
output_schema:
  type: object
  properties:
    id: {type: string}
```

The schema is what providers see as the tool definition. **The `description` here is what gets surfaced in OpenAI/Gemini tool listings** (Claude reads the full SKILL.md via the MCP bridge; other providers see only the schema). Write a clear one-liner.

## handler.py contract

```python
"""my-skill handler."""

from __future__ import annotations

from services.skills.state import SkillContext


async def handle(ctx: SkillContext, *, arg1: str, arg2: int = 5) -> dict:
    """Execute the skill. Always async."""
    # 1. Validate beyond what JSON schema can express
    # 2. Load any state via ctx.session, ctx.project, ctx.experiment
    # 3. Do the work — call services, never raw DB
    # 4. Return a dict that matches output_schema
    return {"id": "...", "field": "..."}
```

Rules:
- **`handle` is the entry point**, always named `handle`, always async.
- **`ctx` provides everything the skill needs**: session, project, experiment, agent name, message id. Don't import `async_session` directly — go through services.
- **Return a dict, not a Pydantic model.** The runner serializes the result; Pydantic adds a layer that's hard to inspect.
- **Errors raise.** Don't return `{"error": "..."}` — raise `SkillError("...")` and let the runner format it. The runner knows how to wrap errors as tool results without polluting the success shape.

## What makes a good skill

1. **Idempotent where possible.** Calling the same skill twice with the same inputs should not corrupt state. `register-dataset` checks for an existing row before inserting.
2. **Small surface.** A skill does one thing. `create-experiment` creates an experiment; it does not also register a dataset. If you find a skill doing multiple things, split it.
3. **Returns structured IDs the next skill needs.** `create-experiment` returns `{"id": ...}`, `start-training` takes `experiment_id`. The handoff is explicit.
4. **The SKILL.md teaches the LLM the lineage.** See `create-experiment/SKILL.md` for the canonical example — it explains the `Raw → Processed → Model` graph so the agent knows which IDs to thread where.
5. **Failure is informative.** *"experiment 12 not found"* beats *"NoneType has no attribute id"*.

## How to add a new skill

1. **Create the folder** under `backend/skills/<kebab-name>/`.
2. **Write `SKILL.md`** — start with this; if you can't explain the skill in markdown, you don't yet know what you're building.
3. **Write `schema.yaml`** — match the SKILL.md interface.
4. **Write `handler.py`** — keep it under 200 lines; offload to services.
5. **Add the skill name to every agent YAML that should use it.** A skill not listed in any agent is dead code.
6. **Add a test** under `backend/tests/skills/test_<skill_name>.py`. At minimum: one happy-path and one failure-path test.
7. **Restart the backend.** The registry walks the directory at startup.

## Common pitfalls

- **Skill folder name doesn't match `SKILL.md` `name:` field.** The registry uses the folder name. Mismatch → silent skip.
- **`schema.yaml` describes inputs the handler doesn't accept.** Causes Pydantic validation errors at dispatch.
- **Handler does its own DB session management.** Use services. Direct DB access in handlers has caused leaked sessions.
- **Skill writes to a path outside `/data/sessions/{session_id}/` or `/data/datasets/{experiment_id}/`.** The volume layout is a contract; breaking it means downstream skills can't find the file.
- **Returning megabytes of inline data.** Tool results are persisted with the message. Big blobs go to the volume; return the path, not the contents.
- **Mutating state without publishing.** If your skill changes something the user should see, the broadcaster must publish a `lineage_updated` / `experiment_updated` / `artifact_published` event. See `services/agent/events.py:save_and_publish`.

## Naming conventions

- **Verb-noun, kebab-case**: `register-dataset`, `start-training`, `read-session-file`.
- **`list-*`** for read-many, **`read-*`** for read-one, **`register-*`** for create with declared metadata, **`create-*`** for create without metadata declaration, **`run-*`** for execute.
- **No abbreviations.** `eda-report`, not `eda-rpt`. Reading is more frequent than typing.

## Before you ship

- [ ] `SKILL.md` frontmatter matches `schema.yaml`
- [ ] Folder name matches `name:` field
- [ ] Handler is async and named `handle`
- [ ] Added to relevant agent YAMLs
- [ ] Happy-path + failure-path tests
- [ ] Result respects the volume layout
- [ ] Backend restarts without registry errors
