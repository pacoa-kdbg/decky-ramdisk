import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import ActiveMove, Phase


class MoveStateStore:
    """JSON-backed store for the single active RAM-disk move.

    Writes go through a temp file + fsync + atomic rename so that a crash or
    power loss cannot leave a half-written ``active_move.json`` on disk.
    Phase transitions are persisted explicitly so the startup recovery pass
    can tell the difference between a staged-but-not-finished move and an
    active one.
    """

    def __init__(self, settings_dir: str) -> None:
        self.dir = Path(settings_dir)
        self.path = self.dir / "active_move.json"

    # ---- read ---------------------------------------------------------
    def read(self) -> ActiveMove | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        # Strip unknown keys so future-version state files still load on
        # older code. Drop keys not in the dataclass fields.
        allowed = {f for f in ActiveMove.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in allowed}
        return ActiveMove(**filtered)

    def raw(self) -> dict[str, Any] | None:
        move = self.read()
        return None if move is None else move.to_dict()

    # ---- write --------------------------------------------------------
    def write(self, move: ActiveMove) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(move.to_dict(), indent=2, sort_keys=True)
        # Write to a sibling temp file, fsync, then atomic rename. fsync the
        # directory afterwards so the rename itself is durable.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".active_move.", suffix=".tmp", dir=str(self.dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
            dir_fd = os.open(str(self.dir), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            # Best-effort cleanup of the temp file on failure.
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

    # ---- phase helpers -----------------------------------------------
    def set_phase(self, phase: Phase) -> ActiveMove:
        move = self.read()
        if move is None:
            raise RuntimeError("Cannot set phase: no active move recorded.")
        updated = replace(move, phase=phase)
        self.write(updated)
        return updated
