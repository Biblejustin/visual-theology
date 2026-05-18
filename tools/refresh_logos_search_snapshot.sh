#!/bin/bash
# Refresh the LogosSearcher index snapshot.
#
# The live LibraryIndex/ is held in WAL write mode by Logos.app, so we cannot
# open it concurrently. We hardlink the immutable bulk files (index.idx etc.)
# and copy the small SQLite + WAL files. Hardlinks cost ~zero disk space.
#
# Run this when you add books to Logos (which rebuilds the index) or when
# the search starts returning stale results. Logos can be running during
# the refresh — the small files are SQLite-safe to copy mid-write.

set -euo pipefail

SRC="/Volumes/External/Logos4/Data/e3txalek.5iq/LibraryIndex"
DST="/Volumes/External/Logos4/Data/e3txalek.5iq/LibraryIndex-snapshot"

if [ ! -d "$SRC" ]; then
  echo "ERROR: live index not found at $SRC" >&2
  exit 1
fi

mkdir -p "$DST"

# Hardlink the big immutable files (zero space).
for f in index.idx index.lxn index.tdi index.dcm index.fld index.met; do
  if [ -f "$SRC/$f" ]; then
    ln -f "$SRC/$f" "$DST/$f"
    echo "  linked $f"
  fi
done

# Copy the small writable SQLite files (~1 MB total). SQLite WAL allows
# concurrent readers, so this is safe even with Logos running.
for f in LibraryIndex.db LibraryIndex.db-shm LibraryIndex.db-wal LibraryIndex.db.flock index.lck; do
  if [ -f "$SRC/$f" ]; then
    cp "$SRC/$f" "$DST/$f"
    echo "  copied $f"
  fi
done

# Truncate the new snapshot's WAL & flock to avoid carrying over Logos' state.
: > "$DST/LibraryIndex.db-wal"
: > "$DST/LibraryIndex.db.flock" 2>/dev/null || true
: > "$DST/index.lck" 2>/dev/null || true

echo "Snapshot refreshed at $DST"
du -sh "$DST"
