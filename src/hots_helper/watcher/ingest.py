"""Ingest a single replay file or bulk-scan a directory into the DB."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..db import Store
from ..parser import ReplayParseError, parse_replay


@dataclass
class IngestResult:
    path: Path
    inserted: bool
    replay_id: int | None
    error: str | None = None
    reason: str = ""   # "new" | "file-dup" | "match-dup" | "error"

    @property
    def ok(self) -> bool:
        return self.error is None


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
    return IngestResult(path=path, inserted=inserted, replay_id=rid, reason=reason)


def ingest_directory(store: Store, directory: Path, pattern: str = "*.StormReplay") -> list[IngestResult]:
    results: list[IngestResult] = []
    for file in sorted(directory.rglob(pattern)):
        results.append(ingest_file(store, file))
    return results
