# Override with e.g. mirror.gcr.io/library/python:3.12-slim when Docker Hub rate-limits (429).
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
COPY docs ./docs
RUN pip install --no-cache-dir .
# Sherlock CLI (MIT) for the username_lookup provider; provides the `sherlock` binary on PATH.
RUN pip install --no-cache-dir sherlock-project
EXPOSE 8000
# Apply migrations (PostgreSQL) then serve. The worker overrides this command in docker-compose.yml.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
