#!/usr/bin/env bash
set -euo pipefail
umask 077

directory=${1:?Usage: bash ops/backup-db.sh /absolute/private/backup-directory}
: "${PGDATABASE:?Set PGDATABASE and standard PostgreSQL connection variables}"
[[ $directory = /* && $PGDATABASE =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || exit 2
install -d -m 700 -- "$directory"
[[ -d $directory && ! -L $directory && -O $directory && $(stat -c '%a' -- "$directory") = 700 ]] || exit 2
exec 9>"$directory/.backup.lock"
flock -n 9

day=$(date +%F)
archive="$directory/$PGDATABASE-$day.dump"
temporary=$(mktemp "$directory/.dump-XXXXXXXX")
trap 'rm -f -- "$temporary"' EXIT

saved=false
for attempt in 1 2; do
    if pg_dump --no-password --format=custom --file="$temporary" && pg_restore --list "$temporary" >/dev/null; then
        saved=true
        break
    fi
    printf 'Backup failed for %s (attempt %s)\n' "$PGDATABASE" "$attempt" >&2
    if (( attempt == 1 )); then sleep "${BACKUP_RETRY_SECONDS:-60}"; fi
done
[[ $saved == true ]] || exit 1
mv -f -- "$temporary" "$archive"
printf 'Backup saved: %s\n' "$archive"

mapfile -t archives < <(find "$directory" -maxdepth 1 -type f -name "$PGDATABASE-????-??-??.dump" | sort -r)
for outdated in "${archives[@]:7}"; do
    rm -f -- "$outdated"
done
