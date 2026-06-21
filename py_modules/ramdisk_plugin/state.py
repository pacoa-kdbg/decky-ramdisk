import json
from pathlib import Path
from typing import Any

from .models import ActiveMove


class MoveStateStore:
    def __init__(self, settings_dir: str) -> None:
        self.path = Path(settings_dir) / "active_move.json"

    def read(self) -> ActiveMove | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return ActiveMove(**data)

    def write(self, move: ActiveMove) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(move.to_dict(), indent=2), encoding="utf-8")

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def raw(self) -> dict[str, Any] | None:
        move = self.read()
        return None if move is None else move.to_dict()

