FROM python:3.13-slim

# uv — single static binary, ~10-100× faster than pip. Pulled from the
# official Astral image so we don't need a separate `pip install uv`
# bootstrap step.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Quieter logs, no cache (smaller image), use the system interpreter
# rather than auto-creating a venv inside the container.
ENV UV_NO_CACHE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install Node.js (required for Claude Code CLI)
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user (Claude CLI refuses --dangerously-skip-permissions as root)
RUN useradd -m -s /bin/bash trainable
WORKDIR /app

# Install Python deps via uv. `--system` installs into the container's
# system interpreter (correct for a single-purpose container — no venv
# needed). Same requirements.txt as before.
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Copy source
COPY . .
RUN chown -R trainable:trainable /app

USER trainable

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
