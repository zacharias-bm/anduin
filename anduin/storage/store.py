from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from pathlib import Path

APP_DIR = Path.home() / "Library" / "Application Support" / "Anduin"
MEETINGS_DIR = APP_DIR / "meetings"
DB_PATH = APP_DIR / "anduin.db"
CONFIG_PATH = APP_DIR / "config.json"


def init():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    MEETINGS_DIR.mkdir(exist_ok=True)
    _init_db()


def meeting_dir(title: str) -> Path:
    now = datetime.now()
    date = now.strftime("%Y-%m-%d")
    time = now.strftime("%H%M%S")
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in title.strip().lower())[:40]
    path = MEETINGS_DIR / f"{date}-{time}-{slug}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_summary(path: Path, summary: str, title: str | None = None, template_id: str | None = None) -> Path:
    summary_path = path / "summary.md"
    summary_path.write_text(summary)
    if template_id:
        (path / "template_id.txt").write_text(template_id)
    _index_meeting(path, title=title)
    return summary_path


def list_meetings(limit: int = 10) -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            "SELECT id, title, date, path, duration_secs, speaker_count "
            "FROM meetings ORDER BY date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r[0], "title": r[1], "date": r[2], "path": r[3],
            "duration_secs": r[4], "speaker_count": r[5],
            "has_summary": (Path(r[3]) / "summary.md").exists(),
        }
        for r in rows
    ]


def get_meeting(meeting_id: int) -> dict | None:
    with _connect() as con:
        row = con.execute(
            "SELECT id, title, date, path, duration_secs, speaker_count "
            "FROM meetings WHERE id = ?",
            (meeting_id,),
        ).fetchone()
    if not row:
        return None
    path = Path(row[3])
    transcript = []
    transcript_path = path / "transcript.json"
    if transcript_path.exists():
        transcript = json.loads(transcript_path.read_text())
    summary = ""
    summary_path = path / "summary.md"
    if summary_path.exists():
        summary = summary_path.read_text()
    has_audio = (path / "audio.wav").exists()
    tid_path = path / "template_id.txt"
    template_id = tid_path.read_text().strip() if tid_path.exists() else None
    return {
        "id": row[0], "title": row[1], "date": row[2], "path": row[3],
        "duration_secs": row[4], "speaker_count": row[5],
        "transcript": transcript, "summary": summary,
        "has_summary": bool(summary), "has_audio": has_audio,
        "template_id": template_id,
    }


def update_title(meeting_id: int, new_title: str):
    with _connect() as con:
        con.execute("UPDATE meetings SET title = ? WHERE id = ?", (new_title, meeting_id))


def rename_speaker(meeting_id: int, old_name: str, new_name: str):
    meeting = get_meeting(meeting_id)
    if not meeting:
        return
    path = Path(meeting["path"])

    # Update transcript.json
    transcript_path = path / "transcript.json"
    if transcript_path.exists():
        segments = json.loads(transcript_path.read_text())
        changed = False
        for s in segments:
            if s["speaker"] == old_name:
                s["speaker"] = new_name
                changed = True
        if changed:
            transcript_path.write_text(json.dumps(segments, indent=2, ensure_ascii=False))

    # Update transcript.md
    transcript_md_path = path / "transcript.md"
    if transcript_md_path.exists():
        content = transcript_md_path.read_text()
        # Look for **Speaker Name** patterns
        content = content.replace(f"**{old_name}**", f"**{new_name}**")
        transcript_md_path.write_text(content)

    # Update summary.md
    summary_path = path / "summary.md"
    if summary_path.exists():
        content = summary_path.read_text()
        # Case-sensitive replace for specific speaker references
        content = content.replace(old_name, new_name)
        summary_path.write_text(content)

    # Re-index
    _index_meeting(path, title=meeting["title"])


def delete_meeting(meeting_id: int) -> bool:
    """Delete a meeting and all its files."""
    meeting = get_meeting(meeting_id)
    if not meeting:
        return False
    path = Path(meeting["path"])

    # Remove from DB
    with _connect() as con:
        con.execute("DELETE FROM meetings_fts WHERE rowid = ?", (meeting_id,))
        con.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))

    # Remove files
    import shutil
    if path.exists():
        shutil.rmtree(path)

    return True


def delete_audio(meeting_id: int) -> bool:
    """Delete only the audio file for a meeting, keeping transcript and summary."""
    meeting = get_meeting(meeting_id)
    if not meeting:
        return False
    path = Path(meeting["path"])
    audio_file = path / "audio.wav"
    if audio_file.exists():
        audio_file.unlink()
        return True
    return False


def search(query: str) -> list[dict]:
    tokens = query.lower().split()
    if not tokens:
        return []
    clauses = []
    params = []
    for tok in tokens:
        pattern = f"%{tok}%"
        clauses.append(
            "(LOWER(m.title) LIKE ? OR f.transcript LIKE ? OR f.summary LIKE ?)"
        )
        params.extend([pattern, pattern, pattern])
    sql = (
        "SELECT m.id, m.title, m.date, m.path, m.duration_secs, m.speaker_count "
        "FROM meetings_fts f JOIN meetings m ON m.id = f.rowid "
        "WHERE " + " AND ".join(clauses) +
        " ORDER BY m.date DESC LIMIT 50"
    )
    with _connect() as con:
        rows = con.execute(sql, params).fetchall()
    return [
        {
            "id": r[0], "title": r[1], "date": r[2], "path": r[3],
            "duration_secs": r[4], "speaker_count": r[5],
            "has_summary": (Path(r[3]) / "summary.md").exists(),
        }
        for r in rows
    ]


def get_speaker_names() -> dict[str, str]:
    with _connect() as con:
        rows = con.execute("SELECT speaker_id, name FROM speaker_names").fetchall()
    return dict(rows)


def set_speaker_name(speaker_id: str, name: str):
    with _connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO speaker_names (speaker_id, name) VALUES (?, ?)",
            (speaker_id, name),
        )


def get_config(key: str, default=None):
    if not CONFIG_PATH.exists():
        return default
    return json.loads(CONFIG_PATH.read_text()).get(key, default)


def set_config(key: str, value):
    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    config[key] = value
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _init_db():
    with _connect() as con:
        # Migration: Drop the old FTS table if it was created with the broken content=meetings option
        res = con.execute("SELECT sql FROM sqlite_master WHERE name='meetings_fts'").fetchone()
        if res and "content=" in res[0] and "meetings" in res[0]:
            con.execute("DROP TABLE meetings_fts")

        con.executescript("""
            CREATE TABLE IF NOT EXISTS meetings (
                id             INTEGER PRIMARY KEY,
                title          TEXT NOT NULL,
                date           TEXT NOT NULL,
                path           TEXT NOT NULL,
                duration_secs  REAL,
                speaker_count  INTEGER
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS meetings_fts
                USING fts5(transcript, summary);
            CREATE TABLE IF NOT EXISTS speaker_names (
                speaker_id TEXT PRIMARY KEY,
                name       TEXT NOT NULL
            );
        """)
        # Idempotent migration for existing databases
        cols = {r[1] for r in con.execute("PRAGMA table_info(meetings)").fetchall()}
        if "duration_secs" not in cols:
            con.execute("ALTER TABLE meetings ADD COLUMN duration_secs REAL")
        if "speaker_count" not in cols:
            con.execute("ALTER TABLE meetings ADD COLUMN speaker_count INTEGER")


def _index_meeting(path: Path, title: str | None = None):
    if title is None:
        parts = path.name.split("-")
        if len(parts) >= 4 and len(parts[3]) == 6 and parts[3].isdigit():
            # Format: YYYY-MM-DD-HHMMSS-slug
            title_parts = parts[4:]
        else:
            # Legacy format: YYYY-MM-DD-slug
            title_parts = parts[3:]
        
        title = " ".join(w.capitalize() for w in title_parts) if title_parts else "Untitled Meeting"
    
    # Use the folder's modification time for a more precise timestamp
    mtime = path.stat().st_mtime
    date = datetime.fromtimestamp(mtime).isoformat()
    transcript_md = (path / "transcript.md").read_text() if (path / "transcript.md").exists() else ""
    summary = (path / "summary.md").read_text() if (path / "summary.md").exists() else ""

    duration_secs = None
    speaker_count = None
    transcript_json = path / "transcript.json"
    if transcript_json.exists():
        segments = json.loads(transcript_json.read_text())
        if segments:
            duration_secs = max(s["end"] for s in segments)
            speaker_count = len({s["speaker"] for s in segments})

    with _connect() as con:
        existing = con.execute("SELECT id FROM meetings WHERE path = ?", (str(path),)).fetchone()
        if existing:
            mid = existing[0]
            con.execute(
                "UPDATE meetings SET title=?, date=?, duration_secs=?, speaker_count=? WHERE id=?",
                (title, date, duration_secs, speaker_count, mid),
            )
        else:
            cur = con.execute(
                "INSERT INTO meetings (title, date, path, duration_secs, speaker_count) VALUES (?, ?, ?, ?, ?)",
                (title, date, str(path), duration_secs, speaker_count),
            )
            mid = cur.lastrowid
        con.execute("DELETE FROM meetings_fts WHERE rowid = ?", (mid,))
        con.execute(
            "INSERT INTO meetings_fts (rowid, transcript, summary) VALUES (?, ?, ?)",
            (mid, transcript_md, summary),
        )
