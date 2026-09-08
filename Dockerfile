FROM python:3.13-slim

# Install git — needed by Coder agent to clone repos, commit and push changes
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* ./

RUN uv sync --frozen --no-dev

COPY app/ ./app/

CMD ["uv", "run", "python", "-m", "app.main"]
