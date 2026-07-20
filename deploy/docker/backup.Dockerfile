# syntax=docker/dockerfile:1.7
FROM postgres:17.5-alpine
RUN apk add --no-cache bash coreutils openssl tar
COPY scripts/backup-container.sh scripts/verify-backup-container.sh scripts/restore-container.sh /srv/scripts/
RUN chmod 0555 /srv/scripts/*.sh
ENTRYPOINT []
CMD ["/srv/scripts/backup-container.sh"]
