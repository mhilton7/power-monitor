# syntax=docker/dockerfile:1.7
FROM python:3.13.5-slim-bookworm AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY backend/pyproject.toml ./backend/
COPY backend/requirements.lock ./backend/
COPY backend/app ./backend/app
RUN python -m pip wheel --no-deps --wheel-dir /wheels ./backend

FROM python:3.13.5-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/srv/backend:/srv
RUN groupadd --system --gid 10001 power-monitor \
    && useradd --system --uid 10001 --gid power-monitor --home-dir /nonexistent power-monitor \
    && mkdir -p /data/firmware /data/reports /data/backups /data/config /data/logs /srv/scripts /srv/tools \
    && chown -R power-monitor:power-monitor /data /srv \
    && chmod 2770 /data/logs
COPY --from=builder /wheels /wheels
COPY --from=builder /build/backend/requirements.lock /requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --requirement /requirements.lock \
    && python -m pip install --no-cache-dir --no-deps /wheels/* \
    && rm -rf /wheels /requirements.lock
WORKDIR /srv/backend
COPY --chown=power-monitor:power-monitor backend/alembic.ini ./alembic.ini
COPY --chown=power-monitor:power-monitor backend/alembic ./alembic
COPY --chown=power-monitor:power-monitor backend/app ./app
COPY --chown=power-monitor:power-monitor worker /srv/worker
COPY --chown=power-monitor:power-monitor shared /srv/shared
COPY --chown=power-monitor:power-monitor scripts/worker_health.py /srv/scripts/worker_health.py
COPY --chown=power-monitor:power-monitor tools/recover-admin.py /srv/tools/recover-admin.py
USER power-monitor
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
