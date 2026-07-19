"""Idempotent RunPod resource bootstrap — network volume, worker template,
per-GPU-tier code-runner endpoints.

Everything here is lazy + cached in-process (mirrors Modal's
`create_if_missing=True` lookups): nothing is stored in the DB, and a
fresh backend process re-discovers existing resources by name.
"""

from __future__ import annotations

import asyncio
import logging

from config import settings
from services.compute.runpod_provider.client import get_client
from services.compute.runpod_provider.gpu import endpoint_slug, gpu_type_ids

logger = logging.getLogger(__name__)

NETWORK_VOLUME_NAME = "trainable-data"
RUNNER_TEMPLATE_NAME = "trainable-runner"
RUNNER_ENDPOINT_PREFIX = "trainable-runner"

_lock = asyncio.Lock()
_volume_id: str | None = None
_runner_template_id: str | None = None
_runner_endpoints: dict[str, str] = {}  # gpu label slug -> endpoint id


def _worker_image() -> str:
    return settings.runpod_worker_image


async def ensure_network_volume() -> str:
    """Return the network volume id, creating the volume on first use.

    Prefers the pinned settings.runpod_network_volume_id; otherwise looks
    up by name, then creates in the configured datacenter. The id is
    logged loudly so the user can pin it in .env and skip the lookup.
    """
    global _volume_id
    if settings.runpod_network_volume_id:
        return settings.runpod_network_volume_id
    if _volume_id:
        return _volume_id
    async with _lock:
        if _volume_id:
            return _volume_id
        client = get_client()
        for vol in await client.list_network_volumes():
            if vol.get("name") == NETWORK_VOLUME_NAME:
                _volume_id = vol.get("id")
                break
        else:
            created = await client.create_network_volume(
                {
                    "name": NETWORK_VOLUME_NAME,
                    "size": settings.runpod_network_volume_size_gb,
                    "dataCenterId": settings.runpod_datacenter_id,
                }
            )
            _volume_id = created.get("id")
            logger.warning(
                "[runpod] Created network volume %s (%s, %dGB in %s). "
                "Pin it via RUNPOD_NETWORK_VOLUME_ID=%s in .env to skip "
                "this lookup on future boots.",
                NETWORK_VOLUME_NAME,
                _volume_id,
                settings.runpod_network_volume_size_gb,
                settings.runpod_datacenter_id,
                _volume_id,
            )
        if not _volume_id:
            raise RuntimeError("RunPod network volume lookup/create returned no id")
        return _volume_id


async def ensure_runner_template() -> str:
    """Return the shared serverless code-runner template id."""
    global _runner_template_id
    if _runner_template_id:
        return _runner_template_id
    async with _lock:
        if _runner_template_id:
            return _runner_template_id
        client = get_client()
        for tpl in await client.list_templates():
            if (
                tpl.get("name") == RUNNER_TEMPLATE_NAME
                and tpl.get("imageName") == _worker_image()
            ):
                _runner_template_id = tpl.get("id")
                break
        else:
            created = await client.create_template(
                {
                    "name": RUNNER_TEMPLATE_NAME,
                    "imageName": _worker_image(),
                    "isServerless": True,
                    "containerDiskInGb": 20,
                    "env": {"TRAINABLE_ROLE": "runner"},
                }
            )
            _runner_template_id = created.get("id")
            logger.info(
                "[runpod] Created runner template %s (%s)",
                RUNNER_TEMPLATE_NAME,
                _runner_template_id,
            )
        if not _runner_template_id:
            raise RuntimeError("RunPod runner template lookup/create returned no id")
        return _runner_template_id


def runner_endpoint_name(gpu_label: str | None) -> str:
    return f"{RUNNER_ENDPOINT_PREFIX}-{endpoint_slug(gpu_label)}"


async def ensure_runner_endpoint(gpu_label: str | None) -> str:
    """Return the endpoint id of the code-runner for a GPU tier, creating
    it on first use.

    One endpoint per canonical GPU label (≤8 incl. cpu), each scale-to-
    zero with a 60s idle window so consecutive agent tool calls reuse a
    warm worker — that idle minute is the big latency win over cold pods.
    """
    slug = endpoint_slug(gpu_label)
    if slug in _runner_endpoints:
        return _runner_endpoints[slug]
    async with _lock:
        if slug in _runner_endpoints:
            return _runner_endpoints[slug]
        client = get_client()
        name = runner_endpoint_name(gpu_label)
        for ep in await client.list_endpoints():
            if ep.get("name") == name:
                _runner_endpoints[slug] = ep.get("id")
                return _runner_endpoints[slug]

        volume_id = await ensure_network_volume()
        template_id = await ensure_runner_template()
        payload: dict = {
            "name": name,
            "templateId": template_id,
            "workersMin": 0,
            "workersMax": settings.runpod_max_workers,
            "idleTimeout": 60,
            "scalerType": "QUEUE_DELAY",
            "scalerValue": 1,
            "networkVolumeId": volume_id,
            "dataCenterIds": [settings.runpod_datacenter_id],
            "flashboot": True,
        }
        type_ids = gpu_type_ids(gpu_label)
        if type_ids:
            payload["gpuTypeIds"] = type_ids
            payload["gpuCount"] = 1
        else:
            payload["computeType"] = "CPU"
            payload["instanceIds"] = ["cpu3c-2-8"]
        created = await client.create_endpoint(payload)
        endpoint_id = created.get("id")
        if not endpoint_id:
            raise RuntimeError(
                f"RunPod endpoint create for {name} returned no id: {created}"
            )
        logger.info("[runpod] Created runner endpoint %s (%s)", name, endpoint_id)
        _runner_endpoints[slug] = endpoint_id
        return endpoint_id


def reset_cache() -> None:
    """Test hook — forget cached ids."""
    global _volume_id, _runner_template_id
    _volume_id = None
    _runner_template_id = None
    _runner_endpoints.clear()
