from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "projects"


def safe_filename(name: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-zぁ-んァ-ン一-龥_-]+", "_", name.strip())
    return normalized.strip("_") or "project"


def save_project(project: dict) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    project["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = DATA_DIR / f"{project['id']}_{safe_filename(project['name'])}.json"
    for existing in DATA_DIR.glob(f"{project['id']}_*.json"):
        if existing != path:
            existing.unlink()
    path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def list_projects() -> list[Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(DATA_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)


def load_project(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def delete_project(project_id: str) -> None:
    for path in list_projects():
        if path.name.startswith(project_id + "_"):
            path.unlink()
