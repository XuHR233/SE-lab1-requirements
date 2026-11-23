#!/usr/bin/env bash
set -euo pipefail
umask 077

archive=${1:?Usage: bash ops/restore-drill.sh /path/to/trusted.dump}
[[ -f $archive && -r $archive ]] || exit 2
pg_restore --list "$archive" >/dev/null
started=$SECONDS
temporary=$(mktemp -d)
trap 'rm -f -- "$temporary/archive.sql" "$temporary/restored.sql"; rmdir -- "$temporary"' EXIT
target="selab3_drill_$(date -u +%Y%m%d%H%M%S)_$$"

createdb --no-password --maintenance-db=postgres "$target"
printf 'New drill database: %s\n' "$target"
pg_restore --no-password --exit-on-error --single-transaction --no-owner --no-privileges \
    --dbname="$target" "$archive"

normalize() {
    awk '
        copying && $0 == "\\." { copying = 0; next }
        copying { print "ROW\t" header "\t" $0; next }
        /^COPY / { header = $0; copying = 1; print header; next }
        /^SELECT pg_catalog.setval\(/ { print }
        END { if (copying) exit 1 }
    ' | LC_ALL=C sort -T "$temporary"
}
pg_restore --data-only --no-owner --no-privileges --file=- "$archive" | normalize > "$temporary/archive.sql"
pg_dump --no-password --data-only --no-owner --no-privileges --dbname="$target" | normalize > "$temporary/restored.sql"
if ! cmp -s "$temporary/archive.sql" "$temporary/restored.sql"; then
    printf 'Restored data does not match the archive; inspect %s\n' "$target" >&2
    exit 1
fi
printf 'Restored data matches archive; elapsed %s seconds; retained drill database: %s\n' \
    "$((SECONDS - started))" "$target"
