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


class ExperimentState(str, enum.Enum):
    """Lifecycle state for an agent-declared experiment.

    The agent transitions states by calling skill tools:
      create-experiment → CREATED
      register-dataset (role='input') → PREPPING (or stays CREATED if no prep)
      start-training → TRAINING
      register-model → TRAINED
    Validators flip TRAINING → ABANDONED if the agent's turn ends without a
    register-model call. FAILED is set explicitly when training raises.
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
    """An agent-declared attempt at a problem inside a session.

    Cardinality flipped in the agent-declared-experiments redesign: a session
    is the workbench (chat + workspace), and it holds N experiments — one per
    declared (processed_dataset, model, metrics) bundle the agent built. The
    legacy 1:1 mapping (one experiment with N child sessions) is preserved
    for back-compat by keeping `Session.experiment_id` nullable; new code
    always reads via `Experiment.session_id`.
    """

    __tablename__ = "experiments"

    id = Column(String(36), primary_key=True)
    project_id = Column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    # New canonical link in the flipped schema. Nullable while we backfill
    # legacy rows; new code never writes NULL here.
    #
    # Deliberately NOT a SQL-level ForeignKey: there's a circular relationship
    # with the legacy `Session.experiment_id` FK that SQLite can't resolve
    # (no ALTER TABLE ADD CONSTRAINT). The ORM still treats this as a
    # relationship via the explicit `foreign_keys=[session_id]` on the
    # `session` relationship below; referential integrity is enforced at
    # the application layer (transition_state etc.) and a CASCADE on the
    # legacy direction handles cleanup. Tradeoff documented here so we
    # don't accidentally re-add the FK and break tests.
    session_id = Column(String(36), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    # 1-3 sentence statement of what the agent is trying. AI-written.
    hypothesis = Column(Text, default="")
    # Lifecycle state — see ExperimentState enum. Drives validator gates and
    # the lineage/sidebar chips. Defaults to "trained" for legacy backfilled
    # rows so they don't show up as in-progress.
    state = Column(String(20), default=ExperimentState.CREATED.value, index=True)
    started_at = Column(String, nullable=True)
    completed_at = Column(String, nullable=True)
    # Deprecated: in the new model, the dataset attaches via experiment_datasets.
    # Kept for legacy rows; new code reads via the M2M relation.
    dataset_ref = Column(String(512), nullable=True)
    instructions = Column(Text, default="")
    tags = Column(JSON, default=list)
    pinned = Column(Boolean, default=False)
    archived = Column(Boolean, default=False)
    created_at = Column(String, default=lambda: utcnow().isoformat())
    updated_at = Column(String, default=lambda: utcnow().isoformat())

    project = relationship("Project", back_populates="experiments")
    # Legacy back-relation for the 1:N (Experiment→Sessions) shape. Empty for
    # new agent-declared experiments since the new direction is Session→N
    # Experiments via Experiment.session_id.
    sessions = relationship(
        "Session",
        back_populates="experiment",
        cascade="all, delete-orphan",
        lazy="raise",
        foreign_keys="Session.experiment_id",
    )
    # New canonical relation: this experiment lives inside one session.
    # Both this and `Session.experiments` are `viewonly=True` so they
    # stay off SQLAlchemy's unit-of-work dependency graph; otherwise the
    # legacy `Session.experiment_id` FK conflicts with this back-pointer
    # and SQLAlchemy can't resolve INSERT ordering. Code that needs to
    # set the link writes directly to `Experiment.session_id`.
    session = relationship(
        "Session",
        primaryjoin="Experiment.session_id == Session.id",
        foreign_keys=[session_id],
        viewonly=True,
    )
    dataset_links = relationship(
        "ExperimentDataset",
        back_populates="experiment",
        cascade="all, delete-orphan",
    )
    # Registered models cascade-delete with the experiment so a single
    # `db.delete(experiment)` succeeds. Without the cascade, the FK from
    # registered_models.experiment_id blocks the delete and the user has
    # to retry — exactly the "needs to be deleted twice" symptom.
    registered_models = relationship(
        "RegisteredModel",
        back_populates="experiment",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    # Run snapshots are 1:1 with the experiment (one snapshot per
    # training run). Cascade for the same reason as registered_models.
    run_snapshots = relationship(
        "RunSnapshot",
        primaryjoin="Experiment.id == RunSnapshot.experiment_id",
        foreign_keys="RunSnapshot.experiment_id",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def to_dict(self, sessions=None, datasets=None, model=None):
        """Convert to dict. Pass sessions/datasets/model explicitly to avoid lazy loading."""
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
            # Legacy fields — kept for the existing sidebar that reads them.
            "latest_session_id": (
                self.session_id or (latest_session.id if latest_session else None)
            ),
            "latest_state": latest_session.state if latest_session else None,
        }


class Session(Base):
    """The user's chat workbench.

    In the agent-declared-experiments redesign, a session is the unit of
    conversation, workspace files, and cost tracking — and it holds N
    declared experiments. `experiment_id` is kept nullable for back-compat
    with legacy 1:1 rows; new code attaches via Experiment.session_id and
    reads `session.experiments` instead.
    """

    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True)
    # New canonical project link (used by the new sidebar tree). Nullable
    # while migration backfills it from experiments.project_id; new code
    # writes it directly on session creation.
    project_id = Column(
        String(36), ForeignKey("projects.id"), nullable=True, index=True
    )
    name = Column(String(255), nullable=True)
    # Legacy: in the old schema a session was always nested under one
    # experiment. Kept nullable so flipped-schema sessions (which attach
    # experiments themselves) don't have to lie about a parent.
    experiment_id = Column(
        String(36), ForeignKey("experiments.id"), nullable=True, index=True
    )
    state = Column(String(50), default=SessionState.CREATED.value)
    model = Column(String(100), default=None)
    dataset_version_id = Column(Integer, nullable=True, index=True)
    created_at = Column(String, default=lambda: utcnow().isoformat())
    updated_at = Column(String, default=lambda: utcnow().isoformat())

    project = relationship("Project", foreign_keys=[project_id])
    # Legacy back-pop (one experiment, this session is a child of it).
    experiment = relationship(
        "Experiment", back_populates="sessions", foreign_keys=[experiment_id]
    )
    # New canonical relation: a session has N declared experiments.
    # Explicit primaryjoin because Experiment.session_id is a plain column
    # (no SQL FK — see the column comment on Experiment.session_id).
    # `viewonly=True` keeps this off the unit-of-work dependency graph so
    # the legacy `Session.experiment_id` FK can drive insert ordering
    # without conflict; deletes cascade via the legacy relation.
    experiments = relationship(
        "Experiment",
        primaryjoin="Session.id == Experiment.session_id",
        foreign_keys="Experiment.session_id",
        viewonly=True,
        lazy="raise",
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
    processed_meta = relationship(
        "ProcessedDatasetMeta",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )
    # passive_deletes lets the DB-level ON DELETE CASCADE handle the cleanup
    # without SQLAlchemy first SELECTing every usage_events row into memory
    # — relevant because long-running chats can accumulate hundreds of them.
    usage_events = relationship(
        "UsageEvent",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
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
    """A model declared by the agent via register-model.

    The pickle/artifact stays on the volume — we copy it to a stable
    `/projects/{pid}/models/{name}/v{N}/...` path and pin (project_id, name,
    version) so the artifact survives session cleanup and is addressable
    from the deployment layer. Each row corresponds to exactly one
    Experiment (which represents the (data, model, metrics) bundle the
    agent declared); `experiment_id` is the canonical link in the new
    schema.
    """

    __tablename__ = "registered_models"

    id = Column(String(36), primary_key=True)
    project_id = Column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    # New canonical link — one model per experiment. Nullable while we
    # backfill legacy rows from session ↔ experiment join.
    experiment_id = Column(
        String(36), ForeignKey("experiments.id"), nullable=True, index=True
    )
    name = Column(String(255), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    source_session_id = Column(String(36), nullable=True, index=True)
    artifact_uri = Column(String(512), nullable=False)
    artifact_size_bytes = Column(Integer, default=0)
    metrics_summary = Column(JSON, default=dict)
    # AI-generated 1-2 sentence summary of what makes this model unique
    # (e.g. "XGBoost depth=8 tuned via 30-trial Optuna sweep"). Mandatory
    # at registration time; legacy rows backfill to "".
    description = Column(Text, default="")
    # Final hyperparams the agent passed to start-training, frozen here so
    # the registry view doesn't have to re-walk the snapshot manifest.
    hyperparams = Column(JSON, default=dict)
    framework = Column(String(50), nullable=True)
    status = Column(String(20), default="ready")
    created_at = Column(String, default=lambda: utcnow().isoformat())

    experiment = relationship(
        "Experiment", back_populates="registered_models", foreign_keys=[experiment_id]
    )

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
    """Reproducibility manifest captured after a training experiment completes.

    Hashes splits + script files, captures pip freeze, and freezes the
    final hyperparams used. The manifest_uri points to a .json file on the
    volume that mirrors the in-DB summary. Tied to an Experiment in the
    flipped schema (one snapshot per experiment); `session_id` kept
    nullable for legacy rows.
    """

    __tablename__ = "run_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # New canonical link — one snapshot per experiment.
    experiment_id = Column(
        String(36), ForeignKey("experiments.id"), nullable=True, index=True
    )
    # Legacy: pre-flip snapshots were keyed on session. Kept nullable +
    # non-unique because a single session now hosts N experiments, each
    # with its own snapshot.
    session_id = Column(
        String(36), ForeignKey("sessions.id"), nullable=True, index=True
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
            "experiment_id": self.experiment_id,
            "session_id": self.session_id,
            "dataset_hash": self.dataset_hash,
            "code_hash": self.code_hash,
            "hyperparams": self.hyperparams or {},
            "env_lockfile_size": len(self.env_lockfile) if self.env_lockfile else 0,
            "manifest_uri": self.manifest_uri,
            "created_at": self.created_at,
        }


class DatasetVersion(Base):
    """A versioned dataset on the volume (raw upload or agent-processed).

    Two flavors:
      - kind='raw': uploaded by the user directly to the project (or
        imported from S3). Auto-registered.
      - kind='processed': written by an agent during prep. Declared via
        the register-dataset skill so the platform never has to guess
        what's a dataset vs an intermediate artifact.

    Versions form a graph via `parent_id` (self FK). The lineage view
    walks that graph to render Raw → Processed-v1, v2, ... → Models.
    """

    __tablename__ = "dataset_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    # raw | processed. Defaults to 'raw' for legacy rows (the only kind
    # that existed pre-flip).
    kind = Column(String(20), nullable=False, default="raw", index=True)
    # Human-readable label. For raw uploads this is the file name; for
    # processed the agent supplies it.
    name = Column(String(255), nullable=True)
    # AI-generated 1-2 sentence description (mandatory for processed).
    description = Column(Text, default="")
    hash = Column(String(64), nullable=False, index=True)
    path = Column(String(512), nullable=False)
    size_bytes = Column(Integer, default=0)
    # Self-FK for the graph edge: which DatasetVersion was this derived
    # from? Replaces the legacy `parent_hash` (still kept for back-compat).
    parent_id = Column(
        Integer, ForeignKey("dataset_versions.id"), nullable=True, index=True
    )
    parent_hash = Column(String(64), nullable=True)
    # The session whose agent produced this version (only populated for
    # processed datasets).
    source_session_id = Column(String(36), nullable=True, index=True)
    # The experiment whose agent produced this version (only for processed).
    source_experiment_id = Column(
        String(36), ForeignKey("experiments.id"), nullable=True, index=True
    )
    # Free-form structured metadata: columns, target, splits, quality
    # stats, etc. Replaces the per-session ProcessedDatasetMeta table
    # going forward; old rows are backfilled by migration.
    dataset_metadata = Column("metadata", JSON, default=dict)
    created_at = Column(String, default=lambda: utcnow().isoformat())

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
    """M2M join: which DatasetVersions feed an Experiment, and in what role.

    Typical case: 1 row per experiment with role='input' pointing at the
    processed dataset that was used. Multi-input experiments (e.g.
    feature-store joins) get multiple rows. role='output' is reserved for
    cases where an experiment also produces a derived dataset.
    """

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


class UsageEvent(Base):
    """One row per LLM call or sandbox execution. The unit of cost accounting.

    `kind` discriminates: 'llm' rows have token fields populated, 'sandbox'
    rows have wall-time + gpu fields. Cost is precomputed at insert time so
    rollups don't need to know per-model pricing tables.
    """

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
