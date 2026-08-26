# KnowledgeOS - application image
#
# Runs the migration, ingestion, and verification commands. The API and web
# apps will build on this base once they exist.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

# Source is bind-mounted in compose for local development, so this COPY only
# matters for a standalone `docker build`.
COPY . .

# No default process: this image is driven by explicit commands, e.g.
#   docker compose run --rm app python -m knowledgeos.db.migrate
CMD ["python", "-c", "print('KnowledgeOS app image. Pass a command, e.g. python -m knowledgeos.db.migrate')"]
