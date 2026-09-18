"""Artifact store: large JSON payloads on disk, references in DB (task 2.7)."""
import json, uuid
from pathlib import Path

def artifact_dir(base: str | None = None) -> Path:
    from app.config import settings
    p = Path(base or settings.artifact_dir)
    if not p.is_absolute(): p = Path.cwd() / p
    p.mkdir(parents=True, exist_ok=True)
    return p

def write_artifact(job_id: str, kind: str, payload: dict, db_path=None, base=None) -> dict:
    aid = uuid.uuid4().hex[:12]
    d = artifact_dir(base) / job_id
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{aid}.{kind}.json"
    path.write_text(json.dumps(payload, indent=2))
    from app.db import get_conn
    with get_conn(db_path) as c:
        c.execute("INSERT INTO artifacts(id,job_id,kind,path) VALUES(?,?,?,?)",
                  (aid, job_id, kind, str(path)))
    return {"id": aid, "path": str(path)}

def read_artifact(path: str) -> dict:
    return json.loads(Path(path).read_text())
