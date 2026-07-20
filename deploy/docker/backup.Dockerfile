# syntax=docker/dockerfile:1.7
FROM postgres:17.5-bookworm
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates openssl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10003 power-monitor-backup \
    && useradd --system --uid 10003 --gid power-monitor-backup --home-dir /nonexistent power-monitor-backup \
    && mkdir -p /srv/scripts /data/backups /data/firmware /data/config /data/reports \
    && chown -R power-monitor-backup:power-monitor-backup /data
COPY scripts/backup-container.sh scripts/verify-backup-container.sh scripts/restore-container.sh \
    scripts/backup-scheduler.sh scripts/container-secrets.sh /srv/scripts/
RUN chmod 0555 /srv/scripts/*.sh
USER 10003:10003
ENTRYPOINT []
CMD ["/srv/scripts/backup-container.sh"]
