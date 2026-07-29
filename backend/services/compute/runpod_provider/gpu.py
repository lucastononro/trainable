"""Canonical GPU label → RunPod GPU type ids.

The canonical labels (cpu, T4, L4, A10G, A100-40GB, A100-80GB, H100) are
the ones used across schemas.py, skill schema.yaml files, the deploy
compute options and sandbox.yml — they stay provider-neutral. RunPod has
no T4 / A10G / A100-40GB SKUs, so those labels map to the closest memory
class. Each label maps to an ORDERED fallback list: RunPod accepts
multiple gpuTypeIds and schedules on the first available, which cushions
per-datacenter availability gaps.
"""

from __future__ import annotations

# NB: A100-40GB schedules (and bills) on 80GB silicon — RunPod has no
# 40GB SKU. Documented in docs/compute-providers.md and sandbox.yml.
RUNPOD_GPU_TYPES: dict[str, list[str]] = {
    "cpu": [],  # runs on a CPU-only serverless worker (computeType=CPU)
    "T4": ["NVIDIA RTX A4000", "NVIDIA RTX 2000 Ada Generation"],
    "L4": ["NVIDIA L4", "NVIDIA RTX A4500"],
    "A10G": ["NVIDIA RTX A5000", "NVIDIA A40"],
    "A100-40GB": ["NVIDIA A100 80GB PCIe", "NVIDIA A100-SXM4-80GB"],
    "A100-80GB": ["NVIDIA A100 80GB PCIe", "NVIDIA A100-SXM4-80GB"],
    "H100": ["NVIDIA H100 80GB HBM3", "NVIDIA H100 PCIe", "NVIDIA H100 NVL"],
}


def gpu_type_ids(label: str | None) -> list[str]:
    """RunPod gpuTypeIds for a canonical label. Unknown labels fall back
    to CPU (empty list) so a typo never lights up an H100."""
    if not label:
        return []
    return RUNPOD_GPU_TYPES.get(label, [])


def endpoint_slug(label: str | None) -> str:
    """Endpoint-name-safe slug for a canonical label ("cpu", "a100-80gb")."""
    return (label or "cpu").lower().replace("_", "-")
