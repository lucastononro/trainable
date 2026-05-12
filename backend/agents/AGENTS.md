# AGENTS.md — backend/agents

YAML declarations for every agent the runner can spawn. The runner reads these; you don't write Python here.

## File layout

One YAML per agent. Filename = agent name. Currently:

```
orchestrator.yaml   Root planner — delegates to specialists, owns the session
chat.yaml           Free-conversation assistant — no dataset required
eda.yaml            Exploratory data analysis specialist
data_prep.yaml      Cleaning, encoding, splitting
feature_eng.yaml    Feature engineering
trainer.yaml        Model fitting + evaluation
reviewer.yaml       Reads experiments, suggests next steps
deploy.yaml         Modal serving app generation + validation
researcher.yaml     Web/papers search + summarization
```

## The contract

Every agent YAML has these fields:

```yaml
name: <kebab-case>           # required, must match filename
description: >               # required, shown in the UI agent picker
  Two-sentence summary of what this agent does and when to call it.
default_model: <model-id>    # required, one of services/llm/models.yml
max_depth: <int>             # required, max recursion depth for delegate-task
provider: <claude|openai|gemini|litellm>   # optional, defaults to Claude
opener: |                    # optional, what the agent says on first turn
  Hi! I'm here to {do thing}. ...
skills:                      # required, the whitelist of tools this agent can call
  - name: execute-code
  - name: read-session-file
  - ...
subagents:                   # optional, the specialists this agent can delegate to
  - eda
  - trainer
```

## When to add a new agent

A new agent is justified when:
- It has a **distinct system prompt** that wouldn't dilute an existing agent's behavior.
- It needs a **different skill subset** — e.g., a `reviewer` shouldn't have `execute-code`.
- It owns a **lifecycle stage** that the orchestrator can hand off to.

A new agent is **not** justified when:
- You want a different default model — that's a per-user setting (`agentModels` in AppContext), not a new agent.
- You want a slightly different prompt — extend the existing agent's prompt template, don't fork.
- You want to A/B test — use a feature flag, not a YAML file.

## System prompt rules

The system prompt is the YAML's `prompt` field (or a `prompt_template` referencing a file under `agents/prompts/` if it gets long). Rules:

1. **State the agent's identity in one line.** *"You are the EDA specialist."*
2. **Tell it when to ask vs. when to act.** Chat-first agents (orchestrator, chat) must clarify before executing if intent/data is ambiguous. Specialist agents called via `delegate-task` should act — they've already been routed.
3. **List the allowed-output shape.** If the agent is expected to produce a notebook, say so. If it should always end with a `register-model` call, say so.
4. **Don't reproduce skill descriptions in the prompt.** The runner injects skill descriptions automatically from `SKILL.md`. Repeating them dilutes the prompt and drifts.
5. **Hard caps go in the prompt, soft preferences in the skill description.** *"Never call `execute-code` with `heavy=true` unless the user explicitly approves a GPU run"* belongs in the agent prompt — it's a behavioral guarantee, not a skill hint.

## Skill scoping

The `skills:` list is a **whitelist**. The runner refuses any tool call not in this list.

- Default to **least-privilege**: include only what this agent needs.
- The `chat` agent gets a broad set because it's a generalist.
- Specialists (eda, trainer, deploy) get a narrow set scoped to their job. A `reviewer` agent should not have `register-model`; it reads, it doesn't write.
- **Every agent that takes any user-visible action gets `request-clarification`.** That's the escape hatch when intent is unclear.
- **Every agent that delegates gets `delegate-task` and `inspect-agent-context`.**

When adding a new skill to the codebase, decide which agents get it as part of the same PR. Don't add a skill and quietly leave it unused.

## `max_depth` and delegation

- `max_depth` is how many levels of `delegate-task` chaining are allowed from this agent down.
- Orchestrator: `max_depth: 2` — orchestrator → specialist → (optional) sub-specialist.
- Specialists: `max_depth: 1` — they can call helpers but not chain further.
- Going beyond `2` has caused runaway agent loops. Don't.

## `subagents` and the delegation graph

The `subagents:` list determines what `delegate-task` can target from this agent. If you don't list it, you can't delegate to it.

Visualize the graph before adding an edge:
- `orchestrator → {eda, data_prep, feature_eng, trainer, reviewer, deploy, researcher}` (fan-out)
- Specialists usually have no `subagents` (leaves).
- **Specialists never delegate back to the orchestrator.** That's a loop.

## Common pitfalls

- **Adding a skill to the YAML but not implementing it** — runner crashes on first dispatch. Implement first, list second.
- **Listing a subagent that doesn't exist** — same crash. Spelling matters; agent names are case-sensitive.
- **Changing `default_model` without checking `services/llm/models.yml`** — the model must be in the registry or the runner falls back silently.
- **Long preambles in the system prompt** — every token costs latency. Keep prompts <500 words. Skill descriptions are injected; you don't need to repeat them.
- **Putting examples in the prompt that don't reflect current schema** — when a skill changes shape, search the agent YAMLs for stale examples and update them in the same PR.

## Before you ship

- [ ] YAML loads without error (`python -c "from agents.agents import load_all; load_all()"`)
- [ ] Every `skills:` entry exists under `backend/skills/`
- [ ] Every `subagents:` entry has a matching YAML file
- [ ] `default_model` is in `services/llm/models.yml`
- [ ] System prompt is under ~500 words
- [ ] If the agent can write data (register-*, create-*), it has `request-clarification` in its skill list
