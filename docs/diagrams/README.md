# Trainable — Architecture Diagrams (C4 model) - ALL AI GENERATED

Top-down architecture diagrams for Trainable, organised by the four levels of the [C4 model](https://c4model.com/): Context → Containers → Components → Code.

Each `.excalidraw` file is plain JSON. Open it by any of:

- Drag-and-drop onto [excalidraw.com](https://excalidraw.com/)
- VS Code + [Excalidraw extension](https://marketplace.visualstudio.com/items?itemName=pomdtr.excalidraw-editor)
- JetBrains + Excalidraw plugin

## Index

### C1 — System Context (highest level)
| File | Shows |
|------|-------|
| [`c1-context/c1-system-context.excalidraw`](c1-context/c1-system-context.excalidraw) | Trainable as one box. Who uses it and which external systems it talks to (Claude API, Modal, S3/MinIO). |

### C2 — Containers
| File | Shows |
|------|-------|
| [`c2-containers/c2-containers.excalidraw`](c2-containers/c2-containers.excalidraw) | Deployable units from `docker-compose.yml`: frontend, backend, PostgreSQL, MinIO, plus the external Modal sandbox/volume and Claude API. Arrows labeled with protocol. |

### C3 — Components
| File | Shows |
|------|-------|
| [`c3-components/c3-backend-components.excalidraw`](c3-components/c3-backend-components.excalidraw) | Inside the backend container: routers → services → persistence. Every file in `backend/routers/` and `backend/services/`. |
| [`c3-components/c3-frontend-components.excalidraw`](c3-components/c3-frontend-components.excalidraw) | Inside the frontend container: pages under `src/app/`, shared lib in `src/lib/`, and components in `src/components/`. |
| [`c3-components/c3-multi-agent-subsystem.excalidraw`](c3-components/c3-multi-agent-subsystem.excalidraw) | The `feat/multi-agent-system` headline: orchestrator + specialists (EDA, Prep, Feature Eng, Trainer, Reviewer, Chat), per-agent tools, and shared runtime. |

### C4 — Code (lowest level)
| File | Shows |
|------|-------|
| [`c4-code/c4-agent-runner-loop.excalidraw`](c4-code/c4-agent-runner-loop.excalidraw) | `backend/services/agent/runner.py` internals — how one `run_agent()` call drives the Claude Agent SDK `query()` loop, MCP server, event bus and post-stage hooks. |
| [`c4-code/c4-sandbox-execution.excalidraw`](c4-code/c4-sandbox-execution.excalidraw) | `sandbox.py` + `volume.py` + `mcp_tools.py` — how `execute_code` spawns a Modal sandbox, mounts the volume, streams stdout and turns it into SSE. |
| [`c4-code/c4-sse-broadcaster.excalidraw`](c4-code/c4-sse-broadcaster.excalidraw) | `services/broadcaster.py` + `routers/stream.py` — per-session pub/sub, the SSE endpoint, and the full event-type catalog the frontend listens for. |
| [`c4-code/c4-inter-agent-clarifications.excalidraw`](c4-code/c4-inter-agent-clarifications.excalidraw) | `services/clarifications.py` + `request_clarification` / `inspect_agent_context` tools — async futures, per-session semaphore, and how a child agent pauses for a parent (or user) answer. |

## Reading order

If you're new to the codebase, read top-down: **C1 → C2 → C3 → C4**. Each level reveals one more layer of detail; nothing on a lower-level diagram contradicts what's on a higher one.

If you're investigating a specific feature, jump to the relevant C3/C4 diagram:

- Want to understand how agents run? → `c3-multi-agent-subsystem` then `c4-agent-runner-loop`.
- Want to trace a stdout chunk to the browser? → `c4-sandbox-execution` then `c4-sse-broadcaster`.
- Want to see how agents ask each other questions? → `c4-inter-agent-clarifications`.

## Editing tips

- Open in Excalidraw.com (or VS Code extension), move things around, File → Save back to the same path. The JSON format round-trips cleanly.
- Labels on arrows are bound to their endpoints, so moving boxes keeps the graph connected.
- Styling uses a C4-inspired palette: **purple** = person, **blue** = in-scope system/component, **green** = database/volume, **gray** = external system, **pink** = agent, **orange** = MCP tool, **yellow** = sticky note / reference.

## Keeping diagrams in sync

These diagrams describe the state of the code on branch `feat/multi-agent-system`. When you add/remove a router, service, or agent, update the corresponding C3 diagram. The C1/C2 diagrams should rarely change.
