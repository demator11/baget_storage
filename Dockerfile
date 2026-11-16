FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY src /app/src
RUN uv sync --frozen --no-dev --no-editable && useradd --create-home app
COPY alembic.ini /app/
COPY alembic /app/alembic
USER app
EXPOSE 8000
CMD ["uvicorn", "baget_storage.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
