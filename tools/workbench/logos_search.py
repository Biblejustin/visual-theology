"""Persistent client for the LogosSearcher subprocess (Logos full-text search).

LogosSearcher is a .NET 9 host that wraps Libronix.DigitalLibrary.LibraryIndex +
Libronix.SearchEngine.Searcher. It speaks JSON-line protocol on stdin/stdout:

    request:  {"op":"search","q":"patronage","limit":10,"order":"Relevance"}
    response: {"ok":true,"hits":[{"resource_id":"LLS:...","section_offset":N,...}]}

This wrapper:
- Manages the subprocess lifecycle as a thread-safe singleton.
- Enriches hits with catalog metadata (title, abbrev, type, file path) so the
  LLM gets human-readable resource names, not raw IDs.
- Stays decoupled from snippet extraction — see logos_search_snippets.py (future)
  if/when we wire SinaiInterop position-to-article translation.

Snapshot strategy: the subprocess reads from a hardlink snapshot of LibraryIndex/
so it doesn't fight Logos.app for the SQLite write lock. Refresh the snapshot
with tools/refresh_logos_search_snapshot.sh.
"""

from __future__ import annotations
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SEARCHER_DLL = _PROJECT_ROOT / "tools/LogosSearcher/bin/Release/net9.0/LogosSearcher.dll"
_INDEX_SNAPSHOT = _PROJECT_ROOT / "Data/e3txalek.5iq/LibraryIndex-snapshot"
_CATALOG_DB = _PROJECT_ROOT / "Data/e3txalek.5iq/LibraryCatalog/catalog.db"
_RESOURCE_MGR_DB = _PROJECT_ROOT / "Data/e3txalek.5iq/ResourceManager/ResourceManager.db"
_DOTNET = "/opt/homebrew/opt/dotnet@9/bin/dotnet"


class LogosSearcher:
    """Persistent connection to the LogosSearcher subprocess.

    Use as a context manager or call shutdown() explicitly. Thread-safe — the
    underlying pipe is serialized with an internal lock.
    """

    def __init__(self, startup_timeout=60):
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._stderr_lines: list[str] = []
        self._stderr_thread: Optional[threading.Thread] = None
        self._start(startup_timeout)

    # ── Public API ────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10, order: str = "Relevance",
               enrich_titles: bool = True) -> dict:
        """Run a full-text search across the library.

        Args:
            query: Logos query syntax (or plain words for naive search).
            limit: max documents to return.
            order: one of "Relevance", "Position", "Index", "DocumentCount".
            enrich_titles: look up resource title/abbrev/type from catalog.db
                and add them to each hit.

        Returns:
            {ok, query, order, total_documents, total_results, total_hits, hits[]}
            Each hit: {resource_id, title, abbrev, type, filename, section_offset,
                       section_length, hit_count, hit_positions[]}.
            On error: {ok: False, error, message}.
        """
        if not query or not query.strip():
            return {"ok": False, "error": "empty_query"}
        payload = {"op": "search", "q": query, "limit": int(limit), "order": order}
        response = self._send(payload)
        if not response.get("ok"):
            return response
        if enrich_titles:
            response["hits"] = _enrich_hits(response.get("hits") or [])
        return response

    def ping(self) -> bool:
        try:
            response = self._send({"op": "ping"}, timeout=10)
            return bool(response.get("pong"))
        except Exception:
            return False

    def shutdown(self):
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                try:
                    self._proc.stdin.write(json.dumps({"op": "shutdown"}) + "\n")
                    self._proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=5)
        finally:
            self._proc = None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── Context manager ───────────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.shutdown()

    # ── Internals ─────────────────────────────────────────────────────────

    def _start(self, timeout):
        if not _SEARCHER_DLL.exists():
            raise FileNotFoundError(
                f"LogosSearcher.dll not built: {_SEARCHER_DLL}. "
                "Run: cd tools/LogosSearcher && /opt/homebrew/opt/dotnet@9/bin/dotnet build -c Release"
            )
        if not _INDEX_SNAPSHOT.exists():
            raise FileNotFoundError(
                f"Index snapshot missing: {_INDEX_SNAPSHOT}. "
                "Run tools/refresh_logos_search_snapshot.sh first."
            )

        cmd = [_DOTNET, str(_SEARCHER_DLL), str(_INDEX_SNAPSHOT)]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()

        # Wait for [ready] line on stderr OR confirm with ping
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_alive():
                raise RuntimeError(
                    f"LogosSearcher exited during startup. "
                    f"stderr: {''.join(self._stderr_lines[-20:])}"
                )
            if any("[ready]" in line for line in self._stderr_lines):
                if self.ping():
                    return
            time.sleep(0.2)
        raise TimeoutError(
            f"LogosSearcher did not become ready within {timeout}s. "
            f"stderr: {''.join(self._stderr_lines[-20:])}"
        )

    def _drain_stderr(self):
        if self._proc is None or self._proc.stderr is None:
            return
        for line in self._proc.stderr:
            self._stderr_lines.append(line)
            # Keep only the last 200 lines
            if len(self._stderr_lines) > 200:
                self._stderr_lines = self._stderr_lines[-200:]

    def _send(self, payload: dict, timeout: int = 30) -> dict:
        """Send one JSON request and read one JSON response. Serialized."""
        with self._lock:
            if not self.is_alive():
                raise RuntimeError("LogosSearcher subprocess is not alive")
            line = json.dumps(payload) + "\n"
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise RuntimeError(f"Failed to write to LogosSearcher: {e}")

            response_line = self._proc.stdout.readline()
            if not response_line:
                raise RuntimeError(
                    "LogosSearcher closed stdout unexpectedly. "
                    f"stderr: {''.join(self._stderr_lines[-10:])}"
                )
            try:
                return json.loads(response_line)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Bad JSON from LogosSearcher: {response_line!r} ({e})")


# ── Title enrichment via catalog.db ─────────────────────────────────────────

def _enrich_hits(hits: list[dict]) -> list[dict]:
    """Add title, abbrev, type, filename to each hit by joining catalog.db."""
    if not hits:
        return hits
    ids = sorted({h["resource_id"] for h in hits if h.get("resource_id")})
    if not ids:
        return hits

    meta_by_id: dict[str, dict] = {}
    conn = sqlite3.connect(f"file:{_CATALOG_DB}?mode=ro", uri=True)
    try:
        conn.execute(f"ATTACH 'file:{_RESOURCE_MGR_DB}?mode=ro' AS rm")
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""
            SELECT c.ResourceId, c.Title, c.AbbreviatedTitle, c.Type, c.Authors,
                   rm.Resources.Location
            FROM Records c
            LEFT JOIN rm.Resources ON c.ResourceId = rm.Resources.ResourceId
            WHERE c.ResourceId IN ({placeholders})
            """,
            ids,
        ).fetchall()
        for r in rows:
            location = r[5] or ""
            filename = os.path.basename(location) if location else None
            meta_by_id[r[0]] = {
                "title": r[1],
                "abbrev": r[2] or None,
                "type": r[3] or None,
                "authors": r[4] or None,
                "filename": filename,
            }
    finally:
        conn.close()

    enriched = []
    for h in hits:
        rid = h.get("resource_id")
        meta = meta_by_id.get(rid, {})
        enriched.append({**h, **meta})
    return enriched


# ── Singleton helper ────────────────────────────────────────────────────────

_singleton_lock = threading.Lock()
_singleton: Optional[LogosSearcher] = None


def get_searcher() -> LogosSearcher:
    """Get a process-wide singleton LogosSearcher (lazy start, restart on death)."""
    global _singleton
    with _singleton_lock:
        if _singleton is None or not _singleton.is_alive():
            _singleton = LogosSearcher()
        return _singleton


def shutdown_searcher():
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.shutdown()
            _singleton = None


# ── CLI for quick testing ───────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 logos_search.py <query> [limit]")
        sys.exit(1)
    query = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    with LogosSearcher() as s:
        result = s.search(query, limit=limit)
        print(json.dumps(result, indent=2, default=str))
