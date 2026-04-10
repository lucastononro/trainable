"""SQLAlchemy ORM models."""

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from db import Base


def utcnow():
    return datetime.now(timezone.utc)


class SessionState(str, enum.Enum):
    CREATED = "created"
    EDA_RUNNING = "eda_running"
    EDA_DONE = "eda_done"
    PREP_RUNNING = "prep_running"
    PREP_DONE = "prep_done"
    TRAIN_RUNNING = "train_running"
    TRAIN_DONE = "train_done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Experiment(Base):
    __tablename__ = "experiments"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    dataset_ref = Column(String(512), nullable=False)
    instructions = Column(Text, default="")
    created_at = Column(String, default=lambda: utcnow().isoformat())

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
            "name": self.name,
            "description": self.description or "",
            "dataset_ref": self.dataset_ref,
            "instructions": self.instructions or "",
            "created_at": self.created_at,
            "latest_session_id": latest_session.id if latest_session else None,
            "latest_state": latest_session.state if latest_session else None,
        }


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True)
    experiment_id = Column(String(36), ForeignKey("experiments.id"), nullable=False)
    state = Column(String(50), default=SessionState.CREATED.value)
    model = Column(String(100), default=None)
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
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
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
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
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
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    experiment_id = Column(String(36), ForeignKey("experiments.id"), nullable=False)

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
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
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
