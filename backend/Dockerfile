FROM python:3.13-slim

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

# Install Python deps from the hashed lockfile (reproducible builds, #128).
# requirements.txt stays the human-edited input; regenerate the lock with:
#   uv pip compile requirements.txt -o requirements.lock --generate-hashes --universal
COPY requirements.txt requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Copy source
COPY . .
RUN chown -R trainable:trainable /app

USER trainable

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
