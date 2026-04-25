"""SQLAlchemy ORM models."""

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from db import Base


def utcnow():
    return datetime.now(timezone.utc)


class SessionState(str, enum.Enum):
    CREATED = "created"
    RUNNING = "running"
    DONE = "done"
    # Legacy stage-specific states — retained so older session rows parse.
    EDA_RUNNING = "eda_running"
    EDA_DONE = "eda_done"
    PREP_RUNNING = "prep_running"
    PREP_DONE = "prep_done"
    TRAIN_RUNNING = "train_running"
    TRAIN_DONE = "train_done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False, default="New project")
    description = Column(Text, default="")
    created_at = Column(String, default=lambda: utcnow().isoformat())
    sandbox_config = Column(JSON, default=dict)
    updated_at = Column(String, default=lambda: utcnow().isoformat())

    experiments = relationship(
        "Experiment",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def to_dict(self, experiment_count: int = 0):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "sandbox_config": self.sandbox_config or {},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "experiment_count": experiment_count,
            # Placeholders for Phase B/C — populated once those tables exist.
            "dataset_count": 0,
            "model_count": 0,
        }


class Experiment(Base):
    __tablename__ = "experiments"

    id = Column(String(36), primary_key=True)
    project_id = Column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    dataset_ref = Column(String(512), nullable=False)
    instructions = Column(Text, default="")
    tags = Column(JSON, default=list)
    pinned = Column(Boolean, default=False)
    archived = Column(Boolean, default=False)
    created_at = Column(String, default=lambda: utcnow().isoformat())
    updated_at = Column(String, default=lambda: utcnow().isoformat())

    project = relationship("Project", back_populates="experiments")
    sessions = relationship(
        "Session",
        back_populates="experiment",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def to_dict(self, sessions=None):
        """Convert to dict. Pass sessions list explicitly to avoid lazy loading."""
        s_list = sessions if sessions is not None else []
        latest = sorted(s_list, key=lambda s: s.created_at or "") if s_list else []
        latest_session = latest[-1] if latest else None
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description or "",
            "dataset_ref": self.dataset_ref,
            "instructions": self.instructions or "",
            "tags": self.tags or [],
            "pinned": bool(self.pinned),
            "archived": bool(self.archived),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "latest_session_id": latest_session.id if latest_session else None,
            "latest_state": latest_session.state if latest_session else None,
        }


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True)
    experiment_id = Column(
        String(36), ForeignKey("experiments.id"), nullable=False, index=True
    )
    state = Column(String(50), default=SessionState.CREATED.value)
    model = Column(String(100), default=None)
    dataset_version_id = Column(Integer, nullable=True, index=True)
    created_at = Column(String, default=lambda: utcnow().isoformat())
    updated_at = Column(String, default=lambda: utcnow().isoformat())

    experiment = relationship("Experiment", back_populates="sessions")
    messages = relationship(
        "Message", back_populates="session", cascade="all, delete-orphan"
    )
    artifacts = relationship(
        "Artifact", back_populates="session", cascade="all, delete-orphan"
    )
    metrics = relationship(
        "Metric", back_populates="session", cascade="all, delete-orphan"
    )
    processed_meta = relationship(
        "ProcessedDatasetMeta",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "experiment_id": self.experiment_id,
            "state": self.state,
            "model": self.model,
            "dataset_version_id": self.dataset_version_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    role = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(String, default=lambda: utcnow().isoformat())

    session = relationship("Session", back_populates="messages")

    def to_dict(self):
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "metadata": self.metadata_ or {},
            "created_at": self.created_at,
        }


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    stage = Column(String(50), nullable=False)
    artifact_type = Column(String(50), nullable=False)
    name = Column(String(255), nullable=False)
    path = Column(String(512), nullable=False)
    s3_path = Column(String(512), default=None)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(String, default=lambda: utcnow().isoformat())

    session = relationship("Session", back_populates="artifacts")

    def to_dict(self):
        return {
            "id": self.id,
            "stage": self.stage,
            "artifact_type": self.artifact_type,
            "name": self.name,
            "path": self.path,
            "s3_path": self.s3_path,
            "metadata": self.metadata_ or {},
            "created_at": self.created_at,
        }


class ProcessedDatasetMeta(Base):
    __tablename__ = "processed_dataset_meta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    experiment_id = Column(
        String(36), ForeignKey("experiments.id"), nullable=False, index=True
    )

    columns = Column(JSON, nullable=False)
    feature_columns = Column(JSON, default=list)
    target_column = Column(String(255), default=None)

    total_rows = Column(Integer, nullable=False)
    train_rows = Column(Integer, default=0)
    val_rows = Column(Integer, default=0)
    test_rows = Column(Integer, default=0)

    quality_stats = Column(JSON, default=dict)
    source_files = Column(JSON, default=list)
    output_files = Column(JSON, default=list)

    s3_synced = Column(String(10), default="pending")
    s3_prefix = Column(String(512), default=None)
    created_at = Column(String, default=lambda: utcnow().isoformat())

    session = relationship("Session", back_populates="processed_meta")

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "experiment_id": self.experiment_id,
            "columns": self.columns or [],
            "feature_columns": self.feature_columns or [],
            "target_column": self.target_column,
            "total_rows": self.total_rows,
            "train_rows": self.train_rows,
            "val_rows": self.val_rows,
            "test_rows": self.test_rows,
            "quality_stats": self.quality_stats or {},
            "source_files": self.source_files or [],
            "output_files": self.output_files or [],
            "s3_synced": self.s3_synced,
            "s3_prefix": self.s3_prefix,
            "created_at": self.created_at,
        }


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    stage = Column(String(50), nullable=False, default="train")
    step = Column(Integer, nullable=False)
    name = Column(String(100), nullable=False)
    value = Column(Float, nullable=False)
    run_tag = Column(String(100), nullable=True)
    created_at = Column(String, default=lambda: utcnow().isoformat())

    session = relationship("Session", back_populates="metrics")

    def to_dict(self):
        return {
            "step": self.step,
            "name": self.name,
            "value": self.value,
            "stage": self.stage,
            "run_tag": self.run_tag,
            "created_at": self.created_at,
        }


class RegisteredModel(Base):
    """A model promoted out of a session into the project-level registry.

    The pickle/artifact stays on the volume — we copy it to a stable path
    and pin (project_id, name, version) so the artifact survives session
    cleanup and is addressable from the deployment layer.
    """

    __tablename__ = "registered_models"

    id = Column(String(36), primary_key=True)
    project_id = Column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    name = Column(String(255), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    source_session_id = Column(String(36), nullable=False, index=True)
    artifact_uri = Column(String(512), nullable=False)
    artifact_size_bytes = Column(Integer, default=0)
    metrics_summary = Column(JSON, default=dict)
    framework = Column(String(50), nullable=True)
    status = Column(String(20), default="ready")
    created_at = Column(String, default=lambda: utcnow().isoformat())

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "version": self.version,
            "source_session_id": self.source_session_id,
            "artifact_uri": self.artifact_uri,
            "artifact_size_bytes": self.artifact_size_bytes or 0,
            "metrics_summary": self.metrics_summary or {},
            "framework": self.framework,
            "status": self.status,
            "created_at": self.created_at,
        }


class Deployment(Base):
    """A live or attempted Modal endpoint serving a registered model."""

    __tablename__ = "deployments"

    id = Column(String(36), primary_key=True)
    model_id = Column(
        String(36), ForeignKey("registered_models.id"), nullable=False, index=True
    )
    endpoint_url = Column(String(512), nullable=True)
    status = Column(String(20), default="pending")
    error = Column(Text, nullable=True)
    modal_app = Column(String(255), nullable=True)
    modal_function = Column(String(255), nullable=True)
    created_at = Column(String, default=lambda: utcnow().isoformat())
    updated_at = Column(String, default=lambda: utcnow().isoformat())

    def to_dict(self):
        return {
            "id": self.id,
            "model_id": self.model_id,
            "endpoint_url": self.endpoint_url,
            "status": self.status,
            "error": self.error,
            "modal_app": self.modal_app,
            "modal_function": self.modal_function,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class RunSnapshot(Base):
    """Reproducibility manifest captured after a training session completes.

    Hashes splits + script files, captures pip freeze, and freezes the
    final hyperparams used. The manifest_uri points to a .json file on the
    volume that mirrors the in-DB summary.
    """

    __tablename__ = "run_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True, unique=True
    )
    dataset_hash = Column(String(64), nullable=True)
    code_hash = Column(String(64), nullable=True)
    hyperparams = Column(JSON, default=dict)
    env_lockfile = Column(Text, nullable=True)
    manifest_uri = Column(String(512), nullable=True)
    created_at = Column(String, default=lambda: utcnow().isoformat())

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "dataset_hash": self.dataset_hash,
            "code_hash": self.code_hash,
            "hyperparams": self.hyperparams or {},
            "env_lockfile_size": len(self.env_lockfile) if self.env_lockfile else 0,
            "manifest_uri": self.manifest_uri,
            "created_at": self.created_at,
        }


class DatasetVersion(Base):
    """Content-addressed snapshot of an uploaded dataset file.

    Multiple uploads of the same content collapse onto a single hash; an
    edited re-upload becomes a new version with `parent_hash` linking back.
    """

    __tablename__ = "dataset_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    hash = Column(String(64), nullable=False, index=True)
    path = Column(String(512), nullable=False)
    size_bytes = Column(Integer, default=0)
    parent_hash = Column(String(64), nullable=True)
    created_at = Column(String, default=lambda: utcnow().isoformat())

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "hash": self.hash,
            "path": self.path,
            "size_bytes": self.size_bytes or 0,
            "parent_hash": self.parent_hash,
            "created_at": self.created_at,
        }


class UsageEvent(Base):
    """One row per LLM call or sandbox execution. The unit of cost accounting.

    `kind` discriminates: 'llm' rows have token fields populated, 'sandbox'
    rows have wall-time + gpu fields. Cost is precomputed at insert time so
    rollups don't need to know per-model pricing tables.
    """

    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    project_id = Column(String(36), index=True, nullable=True)
    kind = Column(String(20), nullable=False, default="llm")
    agent_type = Column(String(50), nullable=True)
    agent_id = Column(String(100), nullable=True)

    provider = Column(String(50), nullable=True)
    model = Column(String(100), nullable=True)

    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cache_read_input_tokens = Column(Integer, default=0)
    cache_creation_input_tokens = Column(Integer, default=0)

    sandbox_seconds = Column(Float, default=0.0)
    gpu_type = Column(String(50), nullable=True)

    cost_usd = Column(Float, default=0.0)
    is_error = Column(Boolean, default=False)
    extra = Column(JSON, default=dict)
    created_at = Column(String, default=lambda: utcnow().isoformat())

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "kind": self.kind,
            "agent_type": self.agent_type,
            "agent_id": self.agent_id,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens or 0,
            "output_tokens": self.output_tokens or 0,
            "cache_read_input_tokens": self.cache_read_input_tokens or 0,
            "cache_creation_input_tokens": self.cache_creation_input_tokens or 0,
            "sandbox_seconds": self.sandbox_seconds or 0.0,
            "gpu_type": self.gpu_type,
            "cost_usd": self.cost_usd or 0.0,
            "is_error": bool(self.is_error),
            "extra": self.extra or {},
            "created_at": self.created_at,
        }
