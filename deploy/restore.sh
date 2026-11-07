#!/usr/bin/env bash
# Restore Seva's persistent data directory from a backup archive.
# Run as root. This stops the service, moves the current directory aside,
# extracts the archive, fixes ownership, and starts the service again.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 /path/to/seva-data-YYYYMMDDTHHMMSSZ.tar.gz" >&2
    exit 2
fi

archive="$(readlink -f "$1")"
: "${SEVA_DATA_DIR:?Set SEVA_DATA_DIR to the absolute persistent data directory}"
service_name="${SEVA_SERVICE_NAME:-seva}"
service_user="${SEVA_SERVICE_USER:-seva}"

if [[ ! -f "$archive" ]]; then
    echo "Backup archive not found: $archive" >&2
    exit 1
fi

# Validate the archive before stopping the service.
tar -tzf "$archive" >/dev/null

systemctl stop "$service_name"

if [[ -e "$SEVA_DATA_DIR" ]]; then
    moved="$SEVA_DATA_DIR.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
    mv "$SEVA_DATA_DIR" "$moved"
    echo "Previous data moved to $moved"
fi

install -d -m 0700 -o "$service_user" -g "$service_user" "$SEVA_DATA_DIR"
tar -xzf "$archive" -C "$SEVA_DATA_DIR"
chown -R "$service_user:$service_user" "$SEVA_DATA_DIR"
chmod -R u=rwX,g=,o= "$SEVA_DATA_DIR"

systemctl start "$service_name"
echo "Restore complete. Check: systemctl status $service_name"
