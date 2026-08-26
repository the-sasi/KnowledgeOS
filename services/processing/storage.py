"""Canonical document storage.

Processed documents are written as JSON under `data/processed/`, mirroring the
layout of `data/raw/`. The output path is derived from the input path, which
is what makes reprocessing idempotent: the same source always writes to the
same file, so a re-run overwrites rather than accumulating.

    data/raw/sec/Apple/10-K/2024-11-01_..._aapl-20240928.htm
    data/processed/sec/Apple/10-K/2024-11-01_..._aapl-20240928.canonical.json

JSON on disk rather than a blob column, for three reasons: a 2 MB filing is
awkward to keep in a row that is read for status; the files are trivially
diffable when comparing processor versions; and PostgreSQL stays the source of
truth for *state* while bulk content stays on the filesystem, matching how raw
files are already handled.

`data/raw` is never written to.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from knowledgeos.config import REPO_ROOT
from services.processing.canonical import CanonicalDocument

RAW_ROOT = REPO_ROOT / "data" / "raw"
PROCESSED_ROOT = REPO_ROOT / "data" / "processed"

SUFFIX = ".canonical.json"


def processed_path_for(raw_path: Path | str) -> Path:
    """Deterministic output path for a raw input path."""
    raw_path = Path(raw_path)
    absolute = raw_path if raw_path.is_absolute() else (REPO_ROOT / raw_path)
    absolute = absolute.resolve()

    try:
        relative = absolute.relative_to(RAW_ROOT.resolve())
    except ValueError:
        # Outside data/raw: keep only the file name rather than mirroring an
        # arbitrary absolute path into data/processed.
        relative = Path(absolute.name)

    return PROCESSED_ROOT / relative.parent / f"{relative.name}{SUFFIX}"


def relative_to_repo(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def write_canonical(document: CanonicalDocument, destination: Path) -> Path:
    """Write the canonical JSON atomically.

    Written to a temp file in the same directory then renamed, so a crash
    mid-write cannot leave a half-written document that later looks valid.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(document.to_dict(), indent=2, ensure_ascii=False)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(destination.parent), prefix=".tmp-", suffix=SUFFIX
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, destination)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    return destination


def read_canonical(path: Path | str) -> CanonicalDocument:
    path = Path(path)
    absolute = path if path.is_absolute() else (REPO_ROOT / path)
    data = json.loads(absolute.read_text(encoding="utf-8"))
    return CanonicalDocument.from_dict(data)
