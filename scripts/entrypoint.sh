#!/bin/sh
# Lector container entrypoint.
# If PUID/PGID are set and we are root, run the app as that uid/gid (via gosu) after making sure
# the data and model directories are writable by it. Otherwise exec the command unchanged.
set -e

DATA_DIR="${LECTOR_DATA_DIR:-/data}"
MODELS_DIR="$(dirname "${KOKORO_MODEL:-/models/kokoro-v1.0.onnx}")"

mkdir -p "$DATA_DIR" 2>/dev/null || true

if [ "$(id -u)" = "0" ] && [ -n "${PUID:-}" ]; then
    PGID="${PGID:-$PUID}"

    # Re-point the built-in "lector" account at the requested ids (non-unique ids are allowed,
    # e.g. PGID=20 on macOS collides with Debian's "dialout" group, which is harmless).
    if [ "$(id -g lector)" != "$PGID" ]; then
        groupmod -o -g "$PGID" lector
    fi
    if [ "$(id -u lector)" != "$PUID" ]; then
        usermod -o -u "$PUID" lector
    fi
    chown lector:lector /home/lector 2>/dev/null || true

    # Fix ownership only when it is wrong at the top level, so a large audio library is not
    # re-chowned on every start. Failures (e.g. read-only or root-squashed mounts) are not fatal.
    for d in "$DATA_DIR" "$MODELS_DIR"; do
        [ -d "$d" ] || continue
        if [ "$(stat -c %u "$d")" != "$PUID" ] || [ "$(stat -c %g "$d")" != "$PGID" ]; then
            echo "entrypoint: chown -R $PUID:$PGID $d"
            chown -R "$PUID:$PGID" "$d" 2>/dev/null || echo "entrypoint: warning: could not chown $d"
        fi
    done

    exec gosu "$PUID:$PGID" "$@"
fi

exec "$@"
