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
import math
import os
import re
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
               enrich_titles: bool = True,
               include_snippets: bool = True,
               snippet_window: int = 250,
               dedup_by_resource: bool = True,
               author_contains: Optional[str] = None,
               exclude_workspaces: bool = True,
               resource_type_prefix: Optional[str] = None,
               verse_filter: Optional[str] = None) -> dict:
        """Run a full-text search across the library.

        Args:
            query: Logos query syntax (or plain words for naive search).
            limit: max documents (after dedup) to return.
            order: "Relevance" | "Position" | "Index" | "DocumentCount".
            enrich_titles: look up resource title/abbrev/type/authors from
                catalog.db and add them to each hit.
            include_snippets: open the resource and extract ~snippet_window
                characters around the hit for human-readable preview.
            snippet_window: characters of context to include before and after
                the hit position.
            dedup_by_resource: collapse multiple sections from the same
                resource into one entry with a sections list.
            author_contains: case-insensitive substring filter on Authors.
                Hits whose author does not contain this are dropped.
            exclude_workspaces: drop user-workspace hits (LLS:WS_ prefix).
            resource_type_prefix: prefix-match on the Logos Type field.
                E.g. 'text.monograph' or 'text.monograph.encyclopedia'.

        Returns:
            {ok, query, order, total_documents, total_results, total_hits,
             results: [...]}.
            Each result (after dedup) includes:
                resource_id, title, abbrev, authors, type, filename,
                score, total_hit_count, sections: [{section_offset,
                section_length, hit_count, snippet}, ...]
        """
        if not query or not query.strip():
            return {"ok": False, "error": "empty_query"}
        # If a verse filter was passed, compose: '<user query> INTERSECTS <ref>'.
        # Logos' QueryParser understands angle-bracket bible references natively.
        effective_query = query.strip()
        if verse_filter:
            effective_query = f"({effective_query}) INTERSECTS <{verse_filter.strip()}>"
        # Over-fetch when filtering or deduping; trim at the end.
        fetch_limit = int(limit)
        if dedup_by_resource:
            fetch_limit = max(fetch_limit * 4, 25)
        if author_contains or resource_type_prefix or exclude_workspaces:
            fetch_limit = max(fetch_limit, 50)
        payload = {"op": "search", "q": effective_query, "limit": fetch_limit, "order": order}
        response = self._send(payload)
        if not response.get("ok"):
            return response
        hits = response.get("hits") or []
        if enrich_titles:
            hits = _enrich_hits(hits)
        if exclude_workspaces:
            hits = [h for h in hits if not (h.get("resource_id") or "").startswith("LLS:WS_")]
        if author_contains:
            needle = author_contains.lower()
            hits = [h for h in hits if needle in (h.get("authors") or "").lower()]
        if resource_type_prefix:
            hits = [h for h in hits if (h.get("type") or "").startswith(resource_type_prefix)]
        if include_snippets:
            hits = _attach_snippets(hits, query, window=snippet_window)
        if dedup_by_resource:
            results = _dedup_by_resource(hits, max_results=int(limit))
        else:
            results = [_single_hit_as_result(h) for h in hits[:int(limit)]]
        response["results"] = results
        response.pop("hits", None)
        return response

    def suggest(self, query: str) -> dict:
        """Get query completions / refinements for a partial query.

        Returns:
            {ok, query, suggestions[]} where each suggestion is a dict with
            'kind' (CompleterContext subclass name) and any context-specific
            fields the parser populated.
        """
        if not query or not query.strip():
            return {"ok": False, "error": "empty_query"}
        return self._send({"op": "suggest", "q": query.strip()})

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


# ── Snippet extraction via LogosBatchReader ─────────────────────────────────

# Resource files this run has no article (corrupt/encrypted), so we don't retry.
_snippet_blacklist: set[str] = set()


def _attach_snippets(hits: list[dict], query: str, window: int = 250) -> list[dict]:
    """For each hit, open the resource, find the article containing the hit
    position, slice ~window chars before/after, and attach a 'snippet' field.

    Skips hits we can't read; the hit stays in the list without a snippet.
    """
    if not hits:
        return hits
    try:
        from logos_batch import LogosBatchReader
    except ImportError:
        return hits

    # Lazy-init a singleton batch reader per process.
    global _batch_reader_singleton
    if _batch_reader_singleton is None:
        try:
            _batch_reader_singleton = LogosBatchReader()
        except Exception:
            return hits

    reader = _batch_reader_singleton
    query_terms = _query_terms(query)
    out = []
    for h in hits:
        filename = h.get("filename")
        section_offset = h.get("section_offset")
        if not filename or filename in _snippet_blacklist or section_offset is None:
            out.append(h)
            continue
        # Pick the first hit position if available, otherwise the section start.
        positions = h.get("hit_positions") or []
        target_offset = positions[0]["offset"] if positions else section_offset
        try:
            mapped = reader.pos2article(filename, target_offset)
        except Exception:
            out.append(h)
            continue
        if mapped is None:
            out.append(h)
            continue
        article_num, offset_within_article = mapped
        try:
            article_text = reader.read_article(filename, article_num, max_chars=20000)
        except Exception:
            _snippet_blacklist.add(filename)
            out.append(h)
            continue
        if not article_text:
            out.append(h)
            continue
        snippet = _make_snippet(article_text, offset_within_article, query_terms, window)
        h2 = dict(h)
        h2["article_num"] = article_num
        h2["snippet"] = snippet
        out.append(h2)
    return out


_batch_reader_singleton = None


def _query_terms(query: str) -> list[str]:
    """Extract lowercased word terms from a query, stripping operators/punct."""
    # Drop Logos operators and quoted-phrase markers; keep words and hyphens.
    cleaned = re.sub(r"\b(AND|OR|NOT|NEAR|WITHIN|BEFORE|INTERSECTS)\b", " ", query, flags=re.IGNORECASE)
    return [t.lower() for t in re.findall(r"[\w\-']+", cleaned) if len(t) > 2]


def _make_snippet(article_text: str, offset: int, query_terms: list[str], window: int) -> str:
    """Slice article_text around offset with a window, mark the query terms.

    If offset is past the end (which happens when pos2article maps to article
    start, offset=0), fall back to the first occurrence of any query term.
    """
    n = len(article_text)
    if offset is None or offset < 0 or offset >= n:
        offset = _first_term_offset(article_text, query_terms) or 0
    start = max(0, offset - window)
    end = min(n, offset + window)
    snippet = article_text[start:end].strip()
    # Mark query terms (case-insensitive) with «term» so the LLM sees emphasis.
    for term in sorted(set(query_terms), key=len, reverse=True):
        if not term:
            continue
        snippet = re.sub(rf"(?i)\b({re.escape(term)})\b", r"«\1»", snippet)
    if start > 0:
        snippet = "… " + snippet
    if end < n:
        snippet = snippet + " …"
    return snippet


def _first_term_offset(text: str, terms: list[str]) -> Optional[int]:
    lower = text.lower()
    for t in terms:
        i = lower.find(t)
        if i >= 0:
            return i
    return None


# ── Dedup + scoring ─────────────────────────────────────────────────────────

def _score(hit_count: int, section_posting_count: int) -> float:
    """Normalized 0-1 relevance score.

    hit_count is the number of query-term occurrences in the section.
    section_posting_count is roughly the total tokens in the section.
    Dividing by log(postings) penalizes long sections that just happen to
    contain the terms many times; rewards dense sections.
    """
    if hit_count <= 0:
        return 0.0
    denom = 1.0 + math.log(max(section_posting_count, 1) + 1)
    raw = hit_count / denom
    # Squash to 0-1 via tanh — most useful scores fall in 0-3 raw.
    return round(math.tanh(raw), 4)


def _single_hit_as_result(h: dict) -> dict:
    """Format a single hit as a result entry (no dedup)."""
    return {
        "resource_id": h.get("resource_id"),
        "title": h.get("title"),
        "abbrev": h.get("abbrev"),
        "authors": h.get("authors"),
        "type": h.get("type"),
        "filename": h.get("filename"),
        "score": _score(h.get("hit_count", 0), h.get("section_posting_count", 0)),
        "total_hit_count": h.get("hit_count", 0),
        "sections": [{
            "section_offset": h.get("section_offset"),
            "section_length": h.get("section_length"),
            "hit_count": h.get("hit_count"),
            "article_num": h.get("article_num"),
            "snippet": h.get("snippet"),
        }],
    }


def _dedup_by_resource(hits: list[dict], max_results: int) -> list[dict]:
    """Group hits by resource_id. Returns at most max_results resources.

    Each resource result has all its sections sorted by relevance (hit_count).
    Resources are ordered by best-section score, then total_hit_count.
    """
    grouped: dict[str, dict] = {}
    for h in hits:
        rid = h.get("resource_id")
        if not rid:
            continue
        section = {
            "section_offset": h.get("section_offset"),
            "section_length": h.get("section_length"),
            "hit_count": h.get("hit_count", 0),
            "article_num": h.get("article_num"),
            "snippet": h.get("snippet"),
            "_score": _score(h.get("hit_count", 0), h.get("section_posting_count", 0)),
        }
        if rid not in grouped:
            grouped[rid] = {
                "resource_id": rid,
                "title": h.get("title"),
                "abbrev": h.get("abbrev"),
                "authors": h.get("authors"),
                "type": h.get("type"),
                "filename": h.get("filename"),
                "sections": [section],
                "total_hit_count": section["hit_count"],
                "best_score": section["_score"],
            }
        else:
            g = grouped[rid]
            g["sections"].append(section)
            g["total_hit_count"] += section["hit_count"]
            if section["_score"] > g["best_score"]:
                g["best_score"] = section["_score"]

    results = list(grouped.values())
    # Sort each resource's sections by score desc, drop internal _score.
    for r in results:
        r["sections"].sort(key=lambda s: s["_score"], reverse=True)
        for s in r["sections"]:
            s.pop("_score", None)
    # Sort resources by best_score, then total hits.
    results.sort(key=lambda r: (r["best_score"], r["total_hit_count"]), reverse=True)
    for r in results:
        r["score"] = r.pop("best_score")
    return results[:max_results]


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
