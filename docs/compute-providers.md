# Compute providers

Trainable runs all agent compute — `execute-code` sandboxes, notebook
kernels, workspace storage and model-serving deployments — on a pluggable
compute provider. Two are supported:

| | Modal (default) | RunPod |
|---|---|---|
| Selection | `COMPUTE_PROVIDER=modal` | `COMPUTE_PROVIDER=runpod` |
| Code execution | Modal Sandboxes | Serverless code-runner endpoints (one per GPU tier, scale-to-zero) |
| Notebook kernels | Long-lived Modal Sandbox (stdin/stdout proxy) | Long-lived pod + HTTP kernel gateway |
| Workspace storage | Modal Volume | RunPod network volume (live mount + S3-compatible API) |
| Model serving | `modal deploy` web endpoints | One serverless endpoint per model (pure REST, no CLI) |
| Endpoint auth | public URL + `X-API-Key` header | RunPod API key (Bearer) + `input.api_key` |

The provider is a **global** choice per deployment, made in `.env` (the
`trainable init` wizard asks). Modal behavior is unchanged when
`COMPUTE_PROVIDER=modal` (the default).

## RunPod setup

1. **API key** — [console.runpod.io](https://console.runpod.io) →
   Settings → API Keys. Goes in `RUNPOD_API_KEY`.
2. **S3 API key** — Settings → S3 API Keys (a *separate* key pair used
   for network-volume file access). Goes in `RUNPOD_S3_ACCESS_KEY_ID` /
   `RUNPOD_S3_SECRET_ACCESS_KEY`.
3. **Datacenter** — `RUNPOD_DATACENTER_ID` (default `US-KS-2`). The
   network volume, all sandboxes, kernels and serving endpoints live in
   this one datacenter, and it must support the S3 API — e.g. `US-KS-2`,
   `EU-RO-1`, `EU-CZ-1`, `EUR-IS-1`. Check GPU availability for your
   preferred tiers in that DC before committing.
4. **Worker image** — RunPod runs prebuilt Docker images. Build and push
   the bundled worker (code runner + kernel gateway + serving launcher):

   ```bash
   RUNPOD_IMAGE=ghcr.io/<you>/trainable-runpod-worker:latest make runpod-image
   ```

   and point `RUNPOD_WORKER_IMAGE` at it. The image mirrors the Modal
   sandbox's pip stack so agent code behaves identically.
5. **Network volume** — auto-created (`trainable-data`,
   `RUNPOD_NETWORK_VOLUME_SIZE_GB`, default 100 GB) on first use. The id
   is logged; pin it via `RUNPOD_NETWORK_VOLUME_ID` to skip the lookup.

Then set `COMPUTE_PROVIDER=runpod` and restart (`trainable up`).

## GPU label mapping

Canonical GPU labels are provider-neutral; on RunPod each maps to an
ordered fallback pool (first available wins):

| Label | RunPod GPU pool | Note |
|---|---|---|
| `cpu` | CPU worker (2 vCPU) | |
| `T4` | RTX A4000, RTX 2000 Ada | no T4 SKU on RunPod — 16 GB class |
| `L4` | L4, RTX A4500 | |
| `A10G` | RTX A5000, A40 | no A10G SKU — 24 GB class |
| `A100-40GB` | A100 80GB | **schedules and bills as 80 GB** |
| `A100-80GB` | A100 80GB PCIe / SXM | |
| `H100` | H100 HBM3 / PCIe / NVL | |

## Behavior differences vs Modal

- **Cold starts.** First execution per GPU tier creates the runner
  endpoint and pulls the worker image — expect minutes once per tier per
  datacenter machine. After that, FlashBoot + a 60 s idle window keep
  workers warm across consecutive agent calls. Kernel pods similarly take
  minutes on first boot (ready timeout is 600 s on RunPod vs 120 s on
  Modal).
- **Serving request shape.** RunPod endpoints are invoked through the
  RunPod API, never anonymously:

  ```bash
  curl -X POST "https://api.runpod.ai/v2/<endpoint_id>/runsync" \
    -H "Authorization: Bearer $RUNPOD_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"input": {"records": [{"feature_a": 1.0}], "api_key": "<model key>"}}'
  ```

  The response wraps the prediction in `{"output": {...}}`. For clients
  you distribute, create a **restricted, endpoint-scoped** `rpa_` key in
  the RunPod console instead of sharing your admin key. `runsync` holds
  ~90 s; use `/run` + `/status/{id}` for slow models.
- **Key rotation** updates the endpoint's template env — running workers
  keep the old key until their next cold start (same caveat as Modal
  secret rotation).
- **Kernel pods bill while alive** (Modal sandboxes bill per execution).
  The idle reaper shuts kernels down after 15 idle minutes.
- **Storage quirks.** The S3 API has no presigned URLs, a 500 MB
  single-PUT cap (multipart is used automatically above 256 MB) and slow
  listings on directories with >10k files.

## Troubleshooting

- `COMPUTE_PROVIDER=runpod but required settings are missing` — fill the
  `RUNPOD_*` keys in `.env` (both the API key and the S3 key pair).
- Endpoint creation fails / jobs queue forever — the requested GPU pool
  has no capacity in your volume's datacenter. Pick another datacenter
  (this moves the volume: create a new one there) or another GPU label.
- Serving returns `{"error": "invalid or missing api_key"}` — pass the
  model's key inside `input.api_key` (see the model card for a working
  curl).
- Worker image pull errors — make sure `RUNPOD_WORKER_IMAGE` is public or
  registry credentials are configured on your RunPod account.
