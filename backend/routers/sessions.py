"""Session lifecycle, messages, and stage orchestration routes."""

from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db import async_session, get_db
from models import Artifact, Experiment, Message, Metric
from models import Session as SessionModel
from schemas import MessageCreate, StageStart
from services.agent import abort_agent, run_agent
from services.agent.tasks import _running_tasks
from services.broadcaster import broadcaster

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/experiments/{experiment_id}/sessions")
async def create_session(experiment_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Experiment).where(Experiment.id == experiment_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Experiment not found")

    session = SessionModel(id=str(uuid.uuid4()), experiment_id=experiment_id)
    db.add(session)
    await db.commit()
    return session.to_dict()


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.id == session_id)
        .options(
            selectinload(SessionModel.messages),
            selectinload(SessionModel.artifacts),
            selectinload(SessionModel.experiment).selectinload(Experiment.sessions),
            selectinload(SessionModel.processed_meta),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    exp = session.experiment
    return {
        **session.to_dict(),
        "experiment": exp.to_dict(sessions=exp.sessions) if exp else None,
        "messages": [m.to_dict() for m in sorted(session.messages, key=lambda m: m.id)],
        "artifacts": [a.to_dict() for a in session.artifacts],
        "processed_meta": session.processed_meta.to_dict()
        if session.processed_meta
        else None,
    }


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: MessageCreate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.id == session_id)
        .options(selectinload(SessionModel.experiment))
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    msg = Message(session_id=session_id, role="user", content=body.content)
    db.add(msg)
    await db.commit()

    await broadcaster.publish(
        session_id,
        {
            "type": "user_message",
            "data": {"content": body.content},
        },
    )

    # Optionally trigger the agent to process this message
    if body.run_agent:
        # Silently abort any running agent (no "Agent stopped" noise)
        await abort_agent(session_id, silent=True)

        # Determine the current stage from session state
        state = session.state or "created"
        dataset_ref = session.experiment.dataset_ref or "" if session.experiment else ""
        stage = _infer_stage(state, dataset_ref)

        # Capture experiment context before DB session closes
        experiment_id = session.experiment_id
        instructions = (
            session.experiment.instructions or "" if session.experiment else ""
        )
        user_content = body.content
        selected_model = body.model or session.model
        agent_models = body.agent_models or {}

        async def _run_followup():
            try:
                await run_agent(
                    session_id=session_id,
                    experiment_id=experiment_id,
                    stage=stage,
                    instructions=instructions,
                    dataset_ref=dataset_ref,
                    user_prompt=user_content,
                    model=selected_model,
                    agent_models=agent_models,
                )
                async with async_session() as fresh_db:
                    s = await fresh_db.get(SessionModel, session_id)
                    if s:
                        s.state = f"{stage}_done"
                        await fresh_db.commit()
            except asyncio.CancelledError:
                async with async_session() as fresh_db:
                    s = await fresh_db.get(SessionModel, session_id)
                    if s and s.state != "cancelled":
                        s.state = "cancelled"
                        await fresh_db.commit()
            except Exception:
                async with async_session() as fresh_db:
                    s = await fresh_db.get(SessionModel, session_id)
                    if s:
                        s.state = "failed"
                        await fresh_db.commit()

        task = asyncio.create_task(_run_followup())
        _running_tasks[session_id] = task

    return msg.to_dict()


def _infer_stage(state: str, dataset_ref: str = "") -> str:
    """Infer the agent/stage from session state for follow-up messages.

    Always defaults to 'chat' — the chat agent decides when to delegate
    to orchestrator or specialist agents on its own.
    """
    return "chat"
    return "eda"


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.id)
    )
    return [m.to_dict() for m in result.scalars().all()]


@router.post("/sessions/{session_id}/abort")
async def abort_session(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    cancelled = await abort_agent(session_id)
    if cancelled:
        session.state = "cancelled"
        await db.commit()
        return {"status": "cancelled"}
    return {"status": "not_running"}


@router.post("/sessions/{session_id}/stages/{stage}/start")
async def start_stage(
    session_id: str,
    stage: str,
    body: StageStart = StageStart(),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
):
    if stage not in ("eda", "prep", "train"):
        raise HTTPException(status_code=400, detail=f"Invalid stage: {stage}")

    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.id == session_id)
        .options(selectinload(SessionModel.experiment))
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Guard: don't start if already running
    existing = _running_tasks.get(session_id)
    if existing and not existing.done():
        raise HTTPException(
            status_code=409, detail="Agent already running for this session"
        )

    # Guard: enforce stage prerequisites
    current_state = session.state or "created"
    REQUIRED_PREV = {"prep": "eda_done", "train": "prep_done"}
    ALLOWED_EDA = {"created", "failed", "cancelled"}
    if stage == "eda" and current_state not in ALLOWED_EDA:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start EDA: current state is '{current_state}'",
        )
    required = REQUIRED_PREV.get(stage)
    if required and current_state not in {required, "failed", "cancelled"}:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start {stage}: requires '{required}', current is '{current_state}'",
        )

    # Capture values before db session closes
    experiment_id = session.experiment_id
    exp_instructions = session.experiment.instructions or ""
    dataset_ref = session.experiment.dataset_ref or ""
    stage_instructions = body.instructions or ""
    selected_model = body.model or session.model

    # Merge experiment-level + stage-specific instructions
    instructions = exp_instructions
    if stage_instructions:
        instructions = f"{exp_instructions}\n\nAdditional instructions for this stage:\n{stage_instructions}".strip()

    # Persist model selection if provided
    if body.model:
        session.model = body.model

    # Update state
    session.state = f"{stage}_running"
    await db.commit()

    # Run agent in background
    async def _run_agent():
        try:
            await run_agent(
                session_id=session_id,
                experiment_id=experiment_id,
                stage=stage,
                instructions=instructions,
                dataset_ref=dataset_ref,
                gpu=body.gpu,
                model=selected_model,
            )
            # Agent saves messages directly via _save_and_publish
            # Just update the session state
            async with async_session() as fresh_db:
                s = await fresh_db.get(SessionModel, session_id)
                if s:
                    s.state = f"{stage}_done"
                    await fresh_db.commit()
        except asyncio.CancelledError:
            # Agent handles its own cleanup + SSE events
            async with async_session() as fresh_db:
                s = await fresh_db.get(SessionModel, session_id)
                if s and s.state != "cancelled":
                    s.state = "cancelled"
                    await fresh_db.commit()
        except Exception as e:
            logger.error(
                "Stage '%s' failed for session %s: %s",
                stage,
                session_id,
                e,
            )
            traceback.print_exc()
            async with async_session() as fresh_db:
                s = await fresh_db.get(SessionModel, session_id)
                if s:
                    s.state = "failed"
                    await fresh_db.commit()

    task = asyncio.create_task(_run_agent())
    _running_tasks[session_id] = task

    return {"status": "started", "state": f"{stage}_running"}


@router.get("/sessions/{session_id}/artifacts")
async def get_artifacts(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Artifact).where(Artifact.session_id == session_id))
    return [a.to_dict() for a in result.scalars().all()]


@router.get("/sessions/{session_id}/metrics")
async def get_metrics(
    session_id: str,
    stage: Optional[str] = None,
    name: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Metric).where(Metric.session_id == session_id)
    if stage:
        q = q.where(Metric.stage == stage)
    if name:
        q = q.where(Metric.name == name)
    q = q.order_by(Metric.step)
    result = await db.execute(q)
    return [m.to_dict() for m in result.scalars().all()]
