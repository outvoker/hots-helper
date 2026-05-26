"""Ingest a single replay file or bulk-scan a directory into the DB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..db import Store
from ..parser import ReplayParseError, parse_replay


@dataclass
class IngestResult:
    path: Path
    inserted: bool
    replay_id: int | None
    error: str | None = None
    # "new" | "file-dup" | "match-dup" | "dup" | "error"
    # "skip-cache" — file fingerprint was already in scan_index, we
    # didn't even open the replay. UI can suppress these in logs.
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def skipped_via_cache(self) -> bool:
        return self.reason == "skip-cache"


def ingest_file(store: Store, path: Path) -> IngestResult:
    try:
        replay = parse_replay(path)
    except ReplayParseError as e:
        return IngestResult(path=path, inserted=False, replay_id=None, error=str(e), reason="error")
    except Exception as e:
        return IngestResult(
            path=path, inserted=False, replay_id=None,
            error=f"{type(e).__name__}: {e}", reason="error",
        )

    try:
        # Classify the skip reason for reporting. Peek at file_hash /
        # match_key before the upsert so we can tell the user whether the
        # replay was rejected as a byte-identical duplicate or as a second
        # perspective of a match we already have.
        pre_file = store.has_replay(replay.file_hash)
        pre_match = False
        if not pre_file and replay.match_key:
            row = store.conn.execute(
                "SELECT 1 FROM replays WHERE match_key = ? LIMIT 1",
                (replay.match_key,),
            ).fetchone()
            pre_match = row is not None

        rid, inserted = store.upsert_replay(replay)
    except Exception as e:
        return IngestResult(
            path=path, inserted=False, replay_id=None, error=f"db: {e}", reason="error"
        )

    if inserted:
        reason = "new"
    elif pre_file:
        reason = "file-dup"
    elif pre_match:
        reason = "match-dup"
    else:
        reason = "dup"
    result = IngestResult(path=path, inserted=inserted, replay_id=rid, reason=reason)

    # Remember this file revision so future scans can short-circuit before
    # ever calling parse_replay. file_hash is empty when result.error is
    # set; we never get here in that case, but guard anyway.
    try:
        st = path.stat()
        store.scan_index_touch(
            str(path),
            st.st_mtime_ns,
            st.st_size,
            replay.file_hash,
            datetime.now(timezone.utc).isoformat(),
        )
    except Exception:
        # scan_index is a perf cache — failing to update it must never
        # poison the ingest result the caller is about to consume.
        pass

    return result


def ingest_directory(store: Store, directory: Path, pattern: str = "*.StormReplay") -> list[IngestResult]:
    """Walk ``directory`` and ingest every matching replay.

    Skips files whose ``(path, mtime_ns, size)`` fingerprint is already
    in the local ``scan_index`` table — those produce a synthetic
    ``IngestResult`` with ``reason="skip-cache"`` so callers can ignore
    them in progress UI without losing the count.
    """
    results: list[IngestResult] = []
    for file in sorted(directory.rglob(pattern)):
        try:
            st = file.stat()
        except OSError:
            results.append(ingest_file(store, file))
            continue
        if store.scan_index_has(str(file), st.st_mtime_ns, st.st_size):
            results.append(IngestResult(
                path=file, inserted=False, replay_id=None, reason="skip-cache"
            ))
            continue
        results.append(ingest_file(store, file))
    return results
