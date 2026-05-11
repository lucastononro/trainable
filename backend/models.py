"""SQLAlchemy ORM models.

Schema notes
------------
The agent-declared experiments redesign keeps Project as the project root,
but flips the cardinality below it: a *Session* is the user's chat workbench
(which holds files, conversation history, and cost tracking), and one session
holds N *Experiments* — each experiment is an agent-declared
(processed_dataset, model, metrics, hypothesis) bundle.

To avoid the circular-FK bookkeeping that the bidirectional Session ⇄
Experiment relation produced in earlier drafts, this file uses **forward-only**
relationships:

  - Project.experiments / Project.sessions are listed via ORM relationships.
  - Experiment.session is a forward-only ManyToOne; there is no
    Session.experiments back-pop. Code that needs the inverse runs an
    explicit `select(Experiment).where(Experiment.session_id == sid)`.
  - The legacy Session.experiment_id column is preserved (read-only) so
    existing dev DBs migrate cleanly. New code never writes it.
"""

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
    EDA_RUNNING = "eda_running"
    EDA_DONE = "eda_done"
    PREP_RUNNING = "prep_running"
    PREP_DONE = "prep_done"
    TRAIN_RUNNING = "train_running"
    TRAIN_DONE = "train_done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExperimentState(str, enum.Enum):
    """Lifecycle for an agent-declared experiment.

    Transitions (driven by skill calls):
        create-experiment            → CREATED
        register-dataset(input)      → CREATED  (no transition; multi-step prep)
        start-training               → TRAINING
        register-model               → TRAINED
        validator post-stage hook    → ABANDONED  (training opened, no model)
        explicit failure path        → FAILED
    """

    CREATED = "created"
    PREPPING = "prepping"
    TRAINING = "training"
    TRAINED = "trained"
    FAILED = "failed"
    ABANDONED = "abandoned"


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
    sessions = relationship(
        "Session",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise",
        foreign_keys="Session.project_id",
    )
    dataset_versions = relationship(
        "DatasetVersion",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    registered_models = relationship(
        "RegisteredModel",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def to_dict(
        self,
        experiment_count: int = 0,
        dataset_count: int = 0,
        model_count: int = 0,
    ):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "sandbox_config": self.sandbox_config or {},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "experiment_count": experiment_count,
            "dataset_count": dataset_count,
            "model_count": model_count,
        }


class Session(Base):
    """User's chat workbench. Holds N agent-declared Experiments."""

    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True)
    project_id = Column(
        String(36), ForeignKey("projects.id"), nullable=True, index=True
    )
    name = Column(String(255), nullable=True)
    # Legacy back-pointer kept for old dev DBs only — new code reads
    # experiments via `Experiment.session_id`. Nullable; new sessions
    # leave it NULL.
    experiment_id = Column(
        String(36), ForeignKey("experiments.id"), nullable=True, index=True
    )
    state = Column(String(50), default=SessionState.CREATED.value)
    model = Column(String(100), default=None)
    created_at = Column(String, default=lambda: utcnow().isoformat())
    updated_at = Column(String, default=lambda: utcnow().isoformat())

    project = relationship(
        "Project", back_populates="sessions", foreign_keys=[project_id]
    )
    # Legacy back-pop — pairs with Experiment.sessions for code that
    # walks session → parent_experiment via the pre-flip FK. Forward-only
    # for code that wants the *new* link uses
    # `select(Experiment).where(Experiment.session_id == sid)`.
    experiment = relationship(
        "Experiment",
        back_populates="sessions",
        foreign_keys=[experiment_id],
    )
    messages = relationship(
        "Message", back_populates="session", cascade="all, delete-orphan"
    )
    artifacts = relationship(
        "Artifact", back_populates="session", cascade="all, delete-orphan"
    )
    metrics = relationship(
        "Metric", back_populates="session", cascade="all, delete-orphan"
    )
    tasks = relationship("Task", back_populates="session", cascade="all, delete-orphan")
    processed_meta = relationship(
        "ProcessedDatasetMeta",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )
    usage_events = relationship(
        "UsageEvent",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "experiment_id": self.experiment_id,
            "state": self.state,
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Experiment(Base):
    """Agent-declared (processed_dataset, model, metrics) bundle inside a session."""

    __tablename__ = "experiments"

    id = Column(String(36), primary_key=True)
    project_id = Column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    # Canonical link from the flipped schema. Nullable to allow legacy
    # rows to exist before backfill; new code writes a value here on
    # create-experiment.
    # Plain Column (no SQL-level FK) — there's a circular reference with
    # the legacy `Session.experiment_id` FK that SQLAlchemy can't sort,
    # and SQLite has no ALTER TABLE ADD CONSTRAINT to defer it. The ORM
    # still resolves the relationship via the explicit `foreign_keys=`
    # on the `session` relationship below, and integrity is enforced in
    # the app layer (transition_state etc.).
    session_id = Column(String(36), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    hypothesis = Column(Text, default="")
    state = Column(String(20), default=ExperimentState.CREATED.value, index=True)
    started_at = Column(String, nullable=True)
    completed_at = Column(String, nullable=True)
    # Legacy field — pre-flip schema referenced uploaded raw data via a
    # path string. Still populated by the old POST /api/experiments
    # multipart upload route for back-compat.
    dataset_ref = Column(String(512), nullable=True)
    instructions = Column(Text, default="")
    tags = Column(JSON, default=list)
    pinned = Column(Boolean, default=False)
    archived = Column(Boolean, default=False)
    created_at = Column(String, default=lambda: utcnow().isoformat())
    updated_at = Column(String, default=lambda: utcnow().isoformat())

    project = relationship("Project", back_populates="experiments")
    # NEW canonical link: this experiment lives inside one session.
    # Plain Column above has no SQL FK (circular dep with Session); the
    # ORM-only relationship needs an explicit primaryjoin.
    session = relationship(
        "Session",
        primaryjoin="Experiment.session_id == Session.id",
        foreign_keys=[session_id],
        viewonly=True,
    )
    # Legacy 1:N — pre-flip schema had child sessions. Empty for new
    # agent-declared experiments. `cascade="all, delete-orphan"` keeps
    # `db.delete(experiment)` from FK-tripping on legacy children.
    sessions = relationship(
        "Session",
        back_populates="experiment",
        foreign_keys="Session.experiment_id",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    dataset_links = relationship(
        "ExperimentDataset",
        back_populates="experiment",
        cascade="all, delete-orphan",
    )
    registered_models = relationship(
        "RegisteredModel",
        back_populates="experiment",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    run_snapshots = relationship(
        "RunSnapshot",
        back_populates="experiment",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def to_dict(self, sessions=None, datasets=None, model=None):
        """Convert to dict. Pass `sessions` to surface the legacy 1:N
        children, `datasets` for the new M2M dataset_links payload, and
        `model` for the experiment's RegisteredModel summary."""
        s_list = sessions if sessions is not None else []
        latest = sorted(s_list, key=lambda s: s.created_at or "") if s_list else []
        latest_session = latest[-1] if latest else None
        return {
            "id": self.id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "name": self.name,
            "description": self.description or "",
            "hypothesis": self.hypothesis or "",
            "state": self.state or ExperimentState.CREATED.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "dataset_ref": self.dataset_ref or "",
            "instructions": self.instructions or "",
            "tags": self.tags or [],
            "pinned": bool(self.pinned),
            "archived": bool(self.archived),
            "datasets": datasets or [],
            "model": model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "latest_session_id": (
                self.session_id or (latest_session.id if latest_session else None)
            ),
            "latest_state": latest_session.state if latest_session else None,
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
    """Legacy table — superseded by DatasetVersion(kind='processed') + metadata.

    Kept for read back-compat. New code writes to DatasetVersion.metadata.
    """

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


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    subject = Column(String(255), nullable=False)
    active_form = Column(String(255), default=None)
    short_description = Column(Text, default="")
    description = Column(Text, default="")
    status = Column(String(20), default="pending")
    created_at = Column(String, default=lambda: utcnow().isoformat())
    updated_at = Column(String, default=lambda: utcnow().isoformat())

    session = relationship("Session", back_populates="tasks")

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "subject": self.subject,
            "active_form": self.active_form,
            "short_description": self.short_description or "",
            "description": self.description or "",
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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


class LogEvent(Base):
    """Non-scalar dashboard payload — image grid, table, confusion matrix,
    histogram, ROC/PR, text samples, custom plotly figure.

    Scalars stay in `metrics` for fast queries; this table stores anything
    the agent logs via the rich `trainable.log_*` helpers. The `payload`
    JSON shape is per-`type` (see `services/metrics.parse_stdout_line`).
    """

    __tablename__ = "log_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36), ForeignKey("sessions.id"), nullable=False, index=True
    )
    stage = Column(String(50), nullable=False, default="train")
    step = Column(Integer, nullable=False)
    key = Column(String(255), nullable=False)
    type = Column(String(30), nullable=False)
    run_tag = Column(String(100), nullable=True)
    payload = Column(JSON, default=dict)
    created_at = Column(String, default=lambda: utcnow().isoformat())

    def to_dict(self):
        return {
            "id": self.id,
            "step": self.step,
            "key": self.key,
            "type": self.type,
            "stage": self.stage,
            "run_tag": self.run_tag,
            "payload": self.payload or {},
            "created_at": self.created_at,
        }


class DatasetVersion(Base):
    """A versioned dataset on the volume — raw upload or agent-processed.

    Forms a graph via `parent_id` (self-FK): raw → processed-v1 → processed-v2 …
    Lineage views walk this graph to render Raw → Processed → Models.
    """

    __tablename__ = "dataset_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    kind = Column(String(20), nullable=False, default="raw", index=True)
    name = Column(String(255), nullable=True)
    description = Column(Text, default="")
    hash = Column(String(64), nullable=False, index=True)
    path = Column(String(512), nullable=False)
    size_bytes = Column(Integer, default=0)
    parent_id = Column(
        Integer, ForeignKey("dataset_versions.id"), nullable=True, index=True
    )
    # Hash of the parent version — redundant with parent_id but kept as a
    # secondary link so dedup-by-hash queries don't need to join.
    parent_hash = Column(String(64), nullable=True)
    source_session_id = Column(String(36), nullable=True, index=True)
    source_experiment_id = Column(
        String(36), ForeignKey("experiments.id"), nullable=True, index=True
    )
    dataset_metadata = Column("metadata", JSON, default=dict)
    created_at = Column(String, default=lambda: utcnow().isoformat())

    project = relationship("Project", back_populates="dataset_versions")
    parent = relationship("DatasetVersion", remote_side=[id])

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "kind": self.kind or "raw",
            "name": self.name or "",
            "description": self.description or "",
            "hash": self.hash,
            "path": self.path,
            "size_bytes": self.size_bytes or 0,
            "parent_id": self.parent_id,
            "parent_hash": self.parent_hash,
            "source_session_id": self.source_session_id,
            "source_experiment_id": self.source_experiment_id,
            "metadata": self.dataset_metadata or {},
            "created_at": self.created_at,
        }


class ExperimentDataset(Base):
    """M2M link: which DatasetVersions feed an Experiment, and in what role."""

    __tablename__ = "experiment_datasets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    experiment_id = Column(
        String(36),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_version_id = Column(
        Integer,
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(20), nullable=False, default="input")
    created_at = Column(String, default=lambda: utcnow().isoformat())

    experiment = relationship("Experiment", back_populates="dataset_links")
    dataset_version = relationship("DatasetVersion")

    def to_dict(self):
        return {
            "id": self.id,
            "experiment_id": self.experiment_id,
            "dataset_version_id": self.dataset_version_id,
            "role": self.role,
            "created_at": self.created_at,
        }


class RegisteredModel(Base):
    """Agent-declared model artifact pinned to an experiment."""

    __tablename__ = "registered_models"

    id = Column(String(36), primary_key=True)
    project_id = Column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    experiment_id = Column(
        String(36), ForeignKey("experiments.id"), nullable=True, index=True
    )
    name = Column(String(255), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    source_session_id = Column(String(36), nullable=True, index=True)
    artifact_uri = Column(String(512), nullable=False)
    artifact_size_bytes = Column(Integer, default=0)
    metrics_summary = Column(JSON, default=dict)
    description = Column(Text, default="")
    hyperparams = Column(JSON, default=dict)
    # Per-split dataset references with the metrics that go with each
    # split. Keys are split roles ("train", "val", "test", "holdout",
    # "calibration", …); values are {"dataset_id": int, "metrics":
    # {…}}. Mandatory at register-model time so a future reader can
    # always answer "what data did this model see, and what scores did
    # it get on which split?" The lineage canvas uses this — NOT the
    # experiment_datasets join — to draw model←dataset edges, so the
    # graph is "Raw → Processed → Model" rather than collapsing every
    # experiment input into a direct line into the model.
    dataset_refs = Column(JSON, default=dict)
    # Snapshot of the session's Metric rows at register-model time —
    # frozen here so the model's training curves survive even after the
    # session is deleted. List of {step, name, value, stage, run_tag}.
    # Rendered in the /models page as inline charts so the user can
    # compare runs without spelunking back to the original session.
    metrics_history = Column(JSON, default=list)
    # Volume path to the Modal serving app (Python file with @app.cls /
    # @modal.fastapi_endpoint). Written by the `create-serving-app`
    # skill. Until this is set, the Deploy button on /models is
    # disabled because there's nothing to ship — an artifact pickle by
    # itself isn't a deployable thing on Modal.
    serving_app_path = Column(String(512), nullable=True)
    # Auto-generated random token used for the X-API-Key header check on
    # the deployed endpoint. Created at first deploy, stored as a Modal
    # secret named `trainable-key-{model_id[:12]}`. Persists across
    # redeploys (incl. compute changes) so clients don't have to update
    # every time. Rotate via POST /api/models/{id}/rotate-key.
    api_key = Column(String(64), nullable=True)
    framework = Column(String(50), nullable=True)
    status = Column(String(20), default="ready")
    created_at = Column(String, default=lambda: utcnow().isoformat())

    project = relationship("Project", back_populates="registered_models")
    experiment = relationship("Experiment", back_populates="registered_models")

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "experiment_id": self.experiment_id,
            "name": self.name,
            "version": self.version,
            "source_session_id": self.source_session_id,
            "artifact_uri": self.artifact_uri,
            "artifact_size_bytes": self.artifact_size_bytes or 0,
            "metrics_summary": self.metrics_summary or {},
            "description": self.description or "",
            "hyperparams": self.hyperparams or {},
            "dataset_refs": self.dataset_refs or {},
            "metrics_history": self.metrics_history or [],
            "serving_app_path": self.serving_app_path,
            "api_key": self.api_key,
            "framework": self.framework,
            "status": self.status,
            "created_at": self.created_at,
        }


class RunSnapshot(Base):
    """Reproducibility manifest captured per experiment training run."""

    __tablename__ = "run_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    experiment_id = Column(
        String(36), ForeignKey("experiments.id"), nullable=True, index=True
    )
    session_id = Column(
        String(36), ForeignKey("sessions.id"), nullable=True, index=True
    )
    dataset_hash = Column(String(64), nullable=True)
    code_hash = Column(String(64), nullable=True)
    hyperparams = Column(JSON, default=dict)
    env_lockfile = Column(Text, nullable=True)
    manifest_uri = Column(String(512), nullable=True)
    created_at = Column(String, default=lambda: utcnow().isoformat())

    experiment = relationship("Experiment", back_populates="run_snapshots")

    def to_dict(self):
        return {
            "id": self.id,
            "experiment_id": self.experiment_id,
            "session_id": self.session_id,
            "dataset_hash": self.dataset_hash,
            "code_hash": self.code_hash,
            "hyperparams": self.hyperparams or {},
            "env_lockfile_size": len(self.env_lockfile) if self.env_lockfile else 0,
            "manifest_uri": self.manifest_uri,
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
    # Compute target requested at deploy time — "cpu" | "T4" | "L4" |
    # "A10G" | "A100-40GB" | "A100-80GB" | "H100". Stored so the UI
    # badge on /models can show "DEPLOYED ON T4" without re-parsing
    # the serving app source.
    compute = Column(String(20), default="cpu")
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
            "compute": self.compute or "cpu",
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class UsageEvent(Base):
    """Per-LLM-call / per-sandbox-execution cost row."""

    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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

    session = relationship("Session", back_populates="usage_events")

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
