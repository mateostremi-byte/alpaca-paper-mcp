FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY paper_runner.py trading_agents.py ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

# Render entry point adds GitHub authentication plus encrypted persistent OAuth
# state while preserving the server's paper-trading risk controls.
CMD ["python", "-m", "alpaca_mcp_server.render_server"]
