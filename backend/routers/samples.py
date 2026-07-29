"""Bundled sample-dataset gallery + one-click "project from sample" creation.

Thin router — the catalog scan and the whole creation flow live in
`services/samples.py`; here we only validate input and shape HTTP errors.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from schemas import ProjectFromSample, ProjectFromSampleResponse, SampleDatasetEntry
from services import samples as samples_service

router = APIRouter()


@router.get("/samples", response_model=list[SampleDatasetEntry])
async def list_samples():
    """Sample-dataset catalog for the first-run gallery tiles."""
    # build_catalog stats every declared file — keep it off the event loop.
    return await asyncio.to_thread(samples_service.build_catalog)


@router.post("/projects/from-sample", response_model=ProjectFromSampleResponse)
async def create_project_from_sample(
    body: ProjectFromSample,
    db: AsyncSession = Depends(get_db),
):
    """Create a project pre-loaded with a bundled sample dataset."""
    sample = samples_service.get_sample(body.sample_id)
    if not sample:
        raise HTTPException(
            status_code=404, detail=f"Sample '{body.sample_id}' not found"
        )

    files = await asyncio.to_thread(
        samples_service.sample_files, samples_service.sample_data_root(), sample
    )
    if len(files) != len(sample.get("files", [])):
        # Sample data isn't shipped in this deployment — operational, not a bug.
        raise HTTPException(
            status_code=503,
            detail=(
                f"Sample '{body.sample_id}' data files are not available on "
                "this server (sample-data/ is missing)"
            ),
        )

    return await samples_service.create_project_from_sample(
        sample, files, body.name, db
    )
