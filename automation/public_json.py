from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def serialize_public_json(payload: Any) -> str:
    """Serialize public payloads in the repository's stable, mergeable format."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def write_public_json_atomic(path: Path, payload: Any) -> None:
    """Atomically write formatted public JSON with fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialize_public_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
