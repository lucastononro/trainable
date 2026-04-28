"""Session lifecycle and chat routes. Stage orchestration is handled by the
chat agent itself — it delegates to specialists as needed."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db import async_session, get_db
from models import Artifact, Experiment, Message, Metric, Task
from models import Session as SessionModel
from schemas import ClarificationReply, MessageCreate, TaskCreate, TaskUpdate
from services.agent import abort_agent, run_agent
from services.agent.tasks import is_agent_running, register_task
from services.broadcaster import broadcaster
from services.clarifications import list_pending, resolve as resolve_clarification

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
    # No need to seed a visible intro — the chat agent's system prompt
    # already receives the project's data listing via _load_project_context
    # at the start of every run.
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
        # Ephemeral liveness flag: True iff an agent task is actually executing
        # in-process right now. `state` in the DB only transitions at completion,
        # so the frontend uses this to restore spinners after a tab switch.
        "is_running": is_agent_running(session_id),
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
        .options(selectinload(SessionModel.experiment).selectinload(Experiment.project))
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    mention_dicts = [m.model_dump() for m in (body.mentions or [])]
    message_metadata = None
    if mention_dicts:
        message_metadata = {"event_type": "user_message", "mentions": mention_dicts}

    msg = Message(
        session_id=session_id,
        role="user",
        content=body.content,
        metadata_=message_metadata,
    )
    db.add(msg)
    await db.commit()

    await broadcaster.publish(
        session_id,
        {
            "type": "user_message",
            "data": {"content": body.content, "mentions": mention_dicts},
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
        mentions_payload = mention_dicts or None

        # Per-project sandbox config (GPU, timeout per profile)
        sandbox_config = {}
        if session.experiment and session.experiment.project:
            sandbox_config = session.experiment.project.sandbox_config or {}

        # Mark the session "running" eagerly so the sidebar spinner + the
        # restore-on-tab-switch path both see the live state. Completion /
        # failure / cancellation paths below overwrite this.
        session.state = f"{stage}_running"
        await db.commit()

        async def _run_followup():
            try:
                await run_agent(
                    session_id=session_id,
                    experiment_id=experiment_id,
                    stage=stage,
                    instructions=instructions,
                    dataset_ref=dataset_ref,
                    user_prompt=user_content,
                    sandbox_config=sandbox_config,
                    model=selected_model,
                    agent_models=agent_models,
                    mentions=mentions_payload,
                )
                async with async_session() as fresh_db:
                    s = await fresh_db.get(SessionModel, session_id)
                    if s:
                        s.state = "done"
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
        await register_task(session_id, task)

    return msg.to_dict()


def _infer_stage(state: str, dataset_ref: str = "") -> str:
    """Entry-point agent for follow-up user messages.

    The chat agent decides when to delegate to orchestrator or specialist
    agents, so this always returns 'chat' in the multi-agent world.
    """
    return "chat"


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


@router.get("/sessions/{session_id}/clarifications")
async def get_pending_clarifications(session_id: str):
    """Return any clarifications currently waiting for a user reply."""
    return {"pending": list_pending(session_id)}


@router.post("/sessions/{session_id}/clarifications/{question_id}")
async def reply_to_clarification(
    session_id: str,
    question_id: str,
    body: ClarificationReply,
):
    """User reply for an escalated clarification — resumes the paused sub-agent."""
    answered = resolve_clarification(
        session_id,
        question_id,
        {"answer": body.answer, "answered_by": "user", "timeout": False},
    )
    if not answered:
        raise HTTPException(
            status_code=404,
            detail="No pending clarification with that question_id (already answered or expired).",
        )
    await broadcaster.publish(
        session_id,
        {
            "type": "clarification_resolved",
            "data": {
                "question_id": question_id,
                "answered_by": "user",
                "answer": body.answer,
            },
        },
    )
    return {"status": "ok"}


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


@router.get("/sessions/{session_id}/tasks")
async def get_tasks(session_id: str, db: AsyncSession = Depends(get_db)):
    """Return the session's task list. Used by the frontend Tasks tab to
    hydrate on initial page load — SSE picks up new task_created /
    task_updated events from there."""
    result = await db.execute(
        select(Task).where(Task.session_id == session_id).order_by(Task.id)
    )
    return [t.to_dict() for t in result.scalars().all()]


@router.post("/sessions/{session_id}/tasks")
async def create_task(
    session_id: str,
    body: TaskCreate,
    db: AsyncSession = Depends(get_db),
):
    """User-initiated task creation from the Tasks tab UI. Mirrors the
    `tasks` MCP tool's `add` operation but bypasses the agent — this is
    for the user to add their own todos."""
    # Sanity-check the session exists.
    sess = await db.get(SessionModel, session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Session not found")

    t = Task(
        session_id=session_id,
        subject=body.subject.strip(),
        active_form=body.active_form,
        short_description=body.short_description,
        description=body.description,
        status=body.status,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    payload = t.to_dict()
    await broadcaster.publish(session_id, {"type": "task_created", "data": payload})
    return payload


@router.patch("/sessions/{session_id}/tasks/{task_id}")
async def update_task(
    session_id: str,
    task_id: int,
    body: TaskUpdate,
    db: AsyncSession = Depends(get_db),
):
    """User-initiated task edit from the Tasks tab UI."""
    t = await db.get(Task, task_id)
    if t is None or t.session_id != session_id:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.subject is not None:
        t.subject = body.subject.strip()
    if body.short_description is not None:
        t.short_description = body.short_description
    if body.description is not None:
        t.description = body.description
    if body.active_form is not None:
        t.active_form = body.active_form or None
    if body.status is not None:
        t.status = body.status
    from datetime import datetime, timezone
    t.updated_at = datetime.now(timezone.utc).isoformat()
    await db.commit()
    await db.refresh(t)
    payload = t.to_dict()
    await broadcaster.publish(session_id, {"type": "task_updated", "data": payload})
    return payload


@router.delete("/sessions/{session_id}/tasks/{task_id}")
async def delete_task(
    session_id: str,
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    """User-initiated task deletion from the Tasks tab UI."""
    t = await db.get(Task, task_id)
    if t is None or t.session_id != session_id:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.delete(t)
    await db.commit()
    await broadcaster.publish(
        session_id, {"type": "task_deleted", "data": {"id": task_id}}
    )
    return {"status": "deleted", "id": task_id}
