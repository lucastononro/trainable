# AGENTS.md — backend/services/llm

The multi-provider LLM abstraction. Lets the runner talk to Claude, OpenAI, Gemini, or LiteLLM-wrapped providers through a single interface.

## Layout

```
base.py             LLMProvider Protocol, LLMEvent, ProviderInfo dataclasses
factory.py          get_provider(name) — returns a configured provider
models.yml          Model registry: name, provider, pricing, context window, thinking support
claude_provider.py  Anthropic SDK + Claude Agent SDK (MCP-aware tool loop)
openai_provider.py  OpenAI Responses API (NOT Completions — see below)
gemini_provider.py  google-genai SDK (Gemini 3, function calling)
litellm_provider.py LiteLLM wrapper for everything else (Groq, etc.)
thinking.py         Reasoning-mode helpers (extended thinking)
auth/               OAuth flows (Claude subscription tokens)
```

## The contract

Every provider implements `LLMProvider` (Protocol, runtime-checkable) in `base.py`:

```python
class LLMProvider(Protocol):
    info: ProviderInfo                 # supports_subagents / supports_mcp / pricing / etc.

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict] | None,
        thinking: str | None,
    ) -> AsyncIterator[LLMEvent]:
        ...
```

That's the entire surface. Providers emit a stream of `LLMEvent`s; the runner consumes them.

## LLMEvent — the universal event type

```python
EventKind = Literal["text", "tool_call", "tool_result", "usage", "error", "done"]
```

| kind | data shape | who emits |
| --- | --- | --- |
| `text` | `{"text": str}` | provider streams assistant text |
| `tool_call` | `{"tool_name", "tool_call_id", "arguments", "provider_metadata"?}` | provider when LLM wants to call a tool |
| `tool_result` | `{"tool_call_id", "result", "is_error"}` | **the runner** posts this back into the next turn (claude provider posts it internally) |
| `usage` | `{"input_tokens", "output_tokens", "cache_*", "cost_usd"}` | provider at end of turn |
| `error` | `{"message", "code"}` | provider on failure |
| `done` | `{}` | provider when stream is complete |

**The runner relies on this normalization.** Every provider must emit the same shape — different providers, same events.

## The `provider_metadata` escape hatch

`tool_call` events have an optional `provider_metadata` bag. It's how providers thread their own continuation tokens through the runner without polluting the abstraction.

**Real use:** Gemini 3 requires a `thought_signature` to flow through every tool_call → tool_result → next_turn cycle. If we drop it, multi-turn function calls fail with `INVALID_ARGUMENT`. The runner stores `provider_metadata` on the assistant message and passes it back on the next turn.

**Rule:** if you add a new provider and it needs *anything* opaque (continuation tokens, thinking signatures, conversation state), put it in `provider_metadata`. Don't add it to the public event shape.

## `supports_mcp` and the two execution loops

The runner has **two modes**, switched on `provider.info.supports_mcp`:

1. **MCP mode (`supports_mcp=True`, Claude only):** The Claude Agent SDK runs its own tool loop. The provider hands a server (`services/skills/mcp_bridge.py`) to the SDK; tools dispatch internally; the runner just consumes the resulting event stream.

2. **Runner-owned loop (everyone else):** The runner owns the loop:
   - Provider emits `tool_call`.
   - Runner dispatches to the skill handler.
   - Runner constructs the next-turn `messages` with the `tool_result`.
   - Runner calls `provider.stream(...)` again.

This is why **adding a new provider doesn't require touching the runner** — you just set `supports_mcp=False` in `ProviderInfo` and emit `tool_call` events. The runner does the rest.

## models.yml

The source of truth for what models exist, what they cost, who provides them, and what features they support.

```yaml
- id: claude-sonnet-4-6
  provider: claude
  display_name: Claude Sonnet 4.6
  context_window: 200000
  supports_thinking: true
  pricing:
    input_per_1m: 3.00
    output_per_1m: 15.00
    cache_read_per_1m: 0.30
    cache_write_per_1m: 3.75
```

Rules:
- **YAML, not Python constants.** The frontend fetches this list; constants would force a build to update.
- **Pricing is source-of-truth for cost computation.** `services/usage.py` looks up here.
- **`supports_thinking` gates the thinking-level toggle in the UI.** If a model doesn't support extended reasoning, mark it false; the UI hides the control.
- **Don't list a model that isn't wired in a provider.** If `claude-opus-4-9` is in the YAML but `claude_provider.py` doesn't accept it, users will see it in the picker and get errors.

## Adding a new provider

1. **Implement the `LLMProvider` Protocol** in `<name>_provider.py`.
2. **Use the current API surface.** OpenAI: Responses API, not Completions. Google: google-genai SDK, not legacy PaLM. Anthropic: Messages API + Claude Agent SDK when available.
3. **Normalize events.** Convert provider-native events into `LLMEvent`s. Don't leak provider-specific shapes upward.
4. **Add a `ProviderInfo`** that reports honest capabilities (`supports_mcp`, `supports_subagents`, `supports_streaming`, `supports_prompt_cache`).
5. **Register in `factory.py`** under a stable string name.
6. **List its models in `models.yml`** with pricing.
7. **End-to-end smoke test with a real API key** before merging. Use a test in `backend/tests/llm/`. The smoke must exercise: text streaming, a tool call, a tool result, the next turn.

## OAuth (Claude subscription tokens)

`auth/` holds the OAuth flow for users on Claude Pro/Max who provide a `CLAUDE_CODE_OAUTH_TOKEN` instead of an API key. The token literal is `"oauth"` in `models.yml.provider_auth`. Don't refactor this out unless you have a replacement — subscription auth is a deliberate path.

## Common pitfalls

- **Mocking the LLM in tests that exercise the provider abstraction.** Use a recorded transcript or a real key. Mocks have masked broken tool-call serialization before.
- **Adding a provider that uses Completions instead of Responses (OpenAI).** Completions is legacy; tool calling there has different shapes. We had to redo this once already.
- **Forgetting to thread `provider_metadata`.** Gemini 3 breaks silently — its second turn returns INVALID_ARGUMENT. Always test multi-turn tool flows.
- **Hardcoding prices in code.** They go in YAML.
- **Treating cost from the LLM API as the source of truth.** Some providers under-report. We compute cost from token counts × pricing in `services/usage.py`. Trust ours.
- **Shipping a provider that only does text.** If it can't emit `tool_call`s, it can't drive an agent. The model picker should gray it out for agent contexts.

## Before you ship

- [ ] Provider implements `LLMProvider` Protocol (runtime check passes)
- [ ] `ProviderInfo` reports accurate `supports_*` flags
- [ ] Events normalized — no provider-native shapes leak
- [ ] Registered in `factory.py`
- [ ] Models listed in `models.yml` with pricing
- [ ] End-to-end smoke: text + tool_call + tool_result + next turn
- [ ] Cost computation produces non-zero values
