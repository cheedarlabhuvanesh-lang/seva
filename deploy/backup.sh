#!/usr/bin/env bash
# Back up Seva's persistent data directory.
# Run as root on the host, or as a user that can read SEVA_DATA_DIR.
# For a fully consistent copy, stop seva.service first; filesystem snapshots
# are preferable when the service must stay online.

set -euo pipefail

: "${SEVA_DATA_DIR:?Set SEVA_DATA_DIR to the absolute persistent data directory}"
backup_dir="${SEVA_BACKUP_DIR:-/var/backups/seva}"
retention_days="${SEVA_BACKUP_RETENTION_DAYS:-14}"

if [[ ! -d "$SEVA_DATA_DIR" ]]; then
    echo "SEVA_DATA_DIR does not exist: $SEVA_DATA_DIR" >&2
    exit 1
fi

umask 077
mkdir -p "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$backup_dir/seva-data-$stamp.tar.gz"

tar -C "$SEVA_DATA_DIR" -czf "$archive" .
sha256sum "$archive" > "$archive.sha256"

find "$backup_dir" -type f -name 'seva-data-*.tar.gz' \
    -mtime +"$retention_days" -delete
find "$backup_dir" -type f -name 'seva-data-*.tar.gz.sha256' \
    -mtime +"$retention_days" -delete

echo "Backup written to $archive"
