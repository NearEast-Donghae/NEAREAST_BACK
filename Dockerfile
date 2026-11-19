FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NEAREAST_DOCS_ENABLED=false

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install .

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY neareast.seed.gz ./neareast.seed.gz

USER 10001:10001
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "neareast_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
