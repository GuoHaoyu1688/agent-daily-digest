"""Incremental local indexing, deadline grouping and reproducible reports."""

from __future__ import annotations

import csv
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
import zipfile
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from markdown_it import MarkdownIt
from pypdf import PdfReader
from pypdf.errors import PyPdfError


class DigestError(Exception):
    """An expected error with a stable machine-readable code."""

    def __init__(self, message: str, code: str = "INVALID_INPUT", exit_code: int = 2):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


TABLES = ("files", "tasks", "countdowns", "runs")
SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
 id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, root TEXT NOT NULL, agent TEXT NOT NULL,
 size INTEGER, mtime REAL, sha1 TEXT, title TEXT, summary TEXT, first_seen TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, agent TEXT NOT NULL, due_at TEXT,
 status TEXT NOT NULL, priority INTEGER NOT NULL, source TEXT NOT NULL,
 updated_at TEXT NOT NULL, local_override INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS countdowns (id TEXT PRIMARY KEY, name TEXT, date TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT,
 files_scanned INTEGER, files_changed INTEGER
);
"""


def matches(path: str, pattern: str) -> bool:
    """Treat **/ as matching zero directories, unlike plain fnmatch."""
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])
    )


class Digest:
    """A local service; inject `clock` to make time-sensitive behavior testable."""

    def __init__(self, data_dir=None, config=None, clock: Callable | None = None):
        self.data_dir = Path(data_dir or os.getenv("DIGEST_DATA_DIR", "./data")).resolve()
        self.config_path = Path(config).resolve() if config else None
        self.config = {}
        if self.config_path:
            if not self.config_path.is_file():
                raise DigestError("Configuration file not found", "NOT_FOUND", 3)
            if self.config_path.suffix == ".toml":
                import tomllib

                self.config = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
            else:
                self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(self.config, dict):
            raise DigestError("Configuration must be a mapping")
        self.base = self.config_path.parent if self.config_path else Path.cwd()
        self.tz = ZoneInfo(self.config.get("timezone", "Asia/Shanghai"))
        self.clock = clock or (lambda: datetime.now(self.tz))
        roots = self.config.get("roots", ["./workspace"])
        if not isinstance(roots, list) or not all(isinstance(p, str) for p in roots):
            raise DigestError("roots must be a list of paths")
        self.roots = [self.resolve_config_path(p) for p in roots]
        self.max_size = int(self.config.get("max_file_size", 10_000_000))
        if self.max_size < 1:
            raise DigestError("max_file_size must be positive")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = self.data_dir / "digest.db"
        with self.connection() as conn:
            conn.executescript(SCHEMA)
        self.warnings: list[str] = []

    def resolve_config_path(self, value: str) -> Path:
        p = Path(value).expanduser()
        return (p if p.is_absolute() else self.base / p).resolve()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def now(self) -> datetime:
        now = self.clock()
        return now.replace(tzinfo=self.tz) if now.tzinfo is None else now.astimezone(self.tz)

    def parse_time(self, value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return (
                parsed.replace(tzinfo=self.tz)
                if parsed.tzinfo is None
                else parsed.astimezone(self.tz)
            )
        except (ValueError, AttributeError) as exc:
            raise DigestError(f"Invalid ISO date or timestamp: {value}") from exc

    def day(self, value: str | None = None) -> date:
        try:
            return date.fromisoformat(value) if value else self.now().date()
        except ValueError as exc:
            raise DigestError(f"Invalid ISO date: {value}") from exc

    def agent_for(self, path: str) -> str:
        rules = self.config.get("agents", [])
        if isinstance(rules, dict):
            rules = [{"glob": key, "name": value} for key, value in rules.items()]
        for rule in rules:
            if (
                ("glob" in rule and matches(path, rule["glob"]))
                or ("prefix" in rule and path.startswith(rule["prefix"]))
                or ("regex" in rule and re.search(rule["regex"], path))
            ):
                return str(rule.get("name", rule.get("agent", "Unassigned")))
        return "Unassigned"

    def allowed(self, path: Path) -> Path:
        resolved = path.resolve()
        if not any(resolved.is_relative_to(root) for root in self.roots):
            raise DigestError("File is outside configured roots", "FORBIDDEN", 2)
        if not resolved.is_file():
            raise DigestError("File not found", "NOT_FOUND", 3)
        if resolved.stat().st_size > self.max_size:
            raise DigestError("File exceeds max_file_size", "FILE_TOO_LARGE")
        return resolved

    def extract(self, path: Path) -> tuple[str, str]:
        title = path.stem
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            reader = PdfReader(path)
            text = reader.pages[0].extract_text() or "" if reader.pages else ""
        elif suffix in {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".py", ".html", ".log"}:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            if suffix == ".md":
                headings = re.findall(r"^#\s+(.+)$", text, re.MULTILINE)
                if headings:
                    title = headings[0]
        else:
            text = ""
        return title, " ".join(text.split())[:200]

    def scan(self, progress: Callable[[str], None] | None = None) -> dict:
        """Refresh indexed metadata and imported tasks without duplicate records."""
        self.warnings = []
        started = self.now().isoformat()
        scanned = changed = removed = 0
        seen = set()
        active_roots = set()
        includes = self.config.get("include", ["**/*"])
        excludes = self.config.get("exclude", ["**/.git/**", "**/.env", "tasks.json", "tasks.csv"])
        with self.connection() as conn:
            for root in self.roots:
                if not root.is_dir():
                    self.warnings.append(f"Root unavailable; previous index preserved: {root}")
                    continue
                active_roots.add(str(root))
                for candidate in sorted(root.rglob("*")):
                    if not candidate.is_file():
                        continue
                    rel = candidate.relative_to(root).as_posix()
                    if not any(matches(rel, p) for p in includes) or any(
                        matches(rel, p) for p in excludes
                    ):
                        continue
                    try:
                        path = self.allowed(candidate)
                        if path.is_relative_to(self.data_dir):
                            continue
                        if str(path) in seen:
                            continue
                        seen.add(str(path))
                        stat = path.stat()
                        scanned += 1
                        sha = hashlib.sha1(path.read_bytes()).hexdigest()
                        agent = self.agent_for(rel)
                        old = conn.execute(
                            "SELECT * FROM files WHERE path=?", (str(path),)
                        ).fetchone()
                        if (
                            old
                            and old["sha1"] == sha
                            and old["agent"] == agent
                            and old["mtime"] == stat.st_mtime
                        ):
                            conn.execute(
                                "UPDATE files SET last_seen=? WHERE id=?", (started, old["id"])
                            )
                            continue
                        title, summary = self.extract(path)
                        conn.execute(
                            "INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET root=excluded.root,agent=excluded.agent,size=excluded.size,mtime=excluded.mtime,sha1=excluded.sha1,title=excluded.title,summary=excluded.summary,last_seen=excluded.last_seen",
                            (
                                old["id"] if old else uuid.uuid4().hex,
                                str(path),
                                str(root),
                                agent,
                                stat.st_size,
                                stat.st_mtime,
                                sha,
                                title,
                                summary,
                                old["first_seen"] if old else started,
                                started,
                            ),
                        )
                        changed += 1
                    except (OSError, DigestError, ValueError, PyPdfError) as exc:
                        self.warnings.append(f"Skipped {rel}: {exc}")
                    if progress and scanned % 100 == 0:
                        progress(f"Scanned {scanned} files")
            for old in conn.execute("SELECT id,path,root FROM files").fetchall():
                if (old["root"] in active_roots and old["path"] not in seen) or old["root"] not in {
                    str(r) for r in self.roots
                }:
                    conn.execute("DELETE FROM files WHERE id=?", (old["id"],))
                    removed += 1
            conn.execute("DELETE FROM countdowns")
            for i, item in enumerate(self.config.get("countdowns", [])):
                target = self.day(str(item["date"]))
                conn.execute(
                    "INSERT INTO countdowns VALUES (?,?,?,?)",
                    (str(i), item["name"], target.isoformat(), item.get("note", "")),
                )
            conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?)",
                (uuid.uuid4().hex, started, self.now().isoformat(), scanned, changed),
            )
        sources = self.config.get("task_sources")
        paths = (
            [self.resolve_config_path(p) for p in sources]
            if sources is not None
            else [root / name for root in self.roots for name in ("tasks.json", "tasks.csv")]
        )
        imported = 0
        for path in paths:
            if path.is_file():
                try:
                    rows = self.read_rows(path)
                    if isinstance(rows, dict):
                        rows = rows.get("tasks", [])
                    imported += self.import_tasks(rows, str(path))
                except (ValueError, OSError, DigestError) as exc:
                    self.warnings.append(f"Task import failed for {path.name}: {exc}")
        return {
            "files_scanned": scanned,
            "files_changed": changed,
            "files_removed": removed,
            "tasks_imported": imported,
        }

    def outputs(self, day=None, agent=None) -> list[dict]:
        wanted = self.day(day)
        with self.connection() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM files ORDER BY mtime DESC,path")]
        result = []
        for row in rows:
            timestamp = datetime.fromtimestamp(row["mtime"], self.tz)
            if timestamp.date() == wanted and (not agent or row["agent"] == agent):
                row["at"] = timestamp.isoformat()
                row["extension"] = Path(row["path"]).suffix.lstrip(".").upper() or "FILE"
                result.append(row)
        return result

    def normalize_task(self, row: dict, source="manual") -> dict:
        if not isinstance(row, dict) or not str(row.get("title", "")).strip():
            raise DigestError("Task title is required")
        status = row.get("status", "todo")
        if status not in {"todo", "doing", "done", "blocked"}:
            raise DigestError("Task status must be todo, doing, done or blocked")
        priority = int(row.get("priority", 2))
        if priority not in (1, 2, 3):
            raise DigestError("Priority must be 1, 2 or 3")
        due = row.get("due_at") or None
        if due:
            self.parse_time(due)
        task_id = (
            row.get("id")
            or hashlib.sha256(
                json.dumps([source, row["title"], row.get("agent", "Unassigned"), due]).encode()
            ).hexdigest()[:24]
        )
        if source != "manual":
            task_id = "import:" + hashlib.sha256(f"{source}:{task_id}".encode()).hexdigest()[:24]
        return {
            "id": str(task_id),
            "title": str(row["title"]),
            "agent": str(row.get("agent", "Unassigned")),
            "due_at": due,
            "status": status,
            "priority": priority,
            "source": source,
            "updated_at": self.now().isoformat(),
            "local_override": 0,
        }

    def import_tasks(self, rows: list[dict], source: str) -> int:
        normalized = [self.normalize_task(row, source) for row in rows]
        with self.connection() as conn:
            for row in normalized:
                conn.execute(
                    "INSERT INTO tasks VALUES (:id,:title,:agent,:due_at,:status,:priority,:source,:updated_at,:local_override) ON CONFLICT(id) DO UPDATE SET title=excluded.title,agent=excluded.agent,due_at=excluded.due_at,priority=excluded.priority,status=CASE WHEN tasks.local_override=1 THEN tasks.status ELSE excluded.status END,updated_at=excluded.updated_at",
                    row,
                )
        return len(normalized)

    def add_task(self, title, agent="Unassigned", due_at=None, priority=2) -> dict:
        row = self.normalize_task(
            {"title": title, "agent": agent, "due_at": due_at, "priority": priority}
        )
        with self.connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tasks VALUES (:id,:title,:agent,:due_at,:status,:priority,:source,:updated_at,:local_override)",
                row,
            )
            return dict(conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())

    def complete_task(self, task_id: str) -> dict:
        if task_id.startswith("board:"):
            raise DigestError("Complete task-board tasks in agent-task-board", "EXTERNAL_TASK")
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise DigestError("Task not found", "NOT_FOUND", 3)
            conn.execute(
                "UPDATE tasks SET status='done',local_override=1,updated_at=? WHERE id=?",
                (self.now().isoformat(), task_id),
            )
            return dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def board_tasks(self) -> list[dict]:
        setting = self.config.get("task_board_db")
        if not setting:
            return []
        path = self.resolve_config_path(setting)
        if not path.is_file():
            self.warnings.append("Task-board database is unavailable")
            return []
        conn = None
        try:
            conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id,title,assignee,due_at,status,priority,updated_at FROM tasks WHERE status != 'cancelled'"
            ).fetchall()
            mapping = {
                "claimed": "doing",
                "running": "doing",
                "review": "blocked",
                "failed": "blocked",
            }
            return [
                {
                    "id": "board:" + str(r["id"]),
                    "title": r["title"],
                    "agent": r["assignee"] or "Unassigned",
                    "due_at": r["due_at"],
                    "status": mapping.get(r["status"], r["status"]),
                    "priority": r["priority"],
                    "source": "task-board",
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]
        except sqlite3.Error as exc:
            self.warnings.append(f"Task-board schema or connection error: {exc}")
            return []
        finally:
            if conn:
                conn.close()

    def tasks(self, status=None, due_before=None, agent=None, day=None) -> list[dict]:
        today = self.day(day)
        week_end = today + timedelta(days=6 - today.weekday())
        with self.connection() as conn:
            rows = [
                dict(r) for r in conn.execute("SELECT * FROM tasks ORDER BY priority,due_at,id")
            ]
        rows += self.board_tasks()
        result = []
        for row in rows:
            if status and row["status"] != status or agent and row["agent"] != agent:
                continue
            due = self.parse_time(row["due_at"]).date() if row["due_at"] else None
            if due_before and (not due or due > self.parse_time(due_before).date()):
                continue
            row["group"] = (
                "done"
                if row["status"] == "done"
                else "undated"
                if due is None
                else "overdue"
                if due < today
                else "today"
                if due == today
                else "week"
                if due <= week_end
                else "later"
            )
            result.append(row)
        return result

    def digest(self, day=None, agent=None) -> dict:
        chosen = self.day(day)
        outputs = self.outputs(chosen.isoformat(), agent)
        tasks = self.tasks(agent=agent, day=chosen.isoformat())
        with self.connection() as conn:
            countdowns = [
                dict(r) for r in conn.execute("SELECT * FROM countdowns ORDER BY date,id")
            ]
        for item in countdowns:
            item["days_remaining"] = (date.fromisoformat(item["date"]) - chosen).days
        return {
            "date": chosen.isoformat(),
            "timezone": str(self.tz),
            "outputs": outputs,
            "agents": dict(Counter(r["agent"] for r in outputs)),
            "tasks": tasks,
            "countdowns": countdowns,
            "counts": {
                "outputs": len(outputs),
                "active_agents": len({r["agent"] for r in outputs}),
                "due_today": sum(r["group"] == "today" for r in tasks),
            },
        }

    def preview(self, file_id: str) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        if not row:
            raise DigestError("Indexed file not found", "NOT_FOUND", 3)
        path = self.allowed(Path(row["path"]))
        suffix = path.suffix.lower()
        result = {
            "id": row["id"],
            "title": row["title"],
            "agent": row["agent"],
            "size": path.stat().st_size,
            "path": str(path),
            "kind": "text",
        }
        if suffix == ".md":
            result.update(
                kind="markdown",
                content=MarkdownIt("commonmark", {"html": False}).render(
                    path.read_text(encoding="utf-8-sig", errors="replace")
                ),
            )
        elif suffix == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                import itertools

                result.update(kind="table", content=list(itertools.islice(csv.reader(stream), 50)))
        elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            result.update(kind="image", content=f"/api/files/{file_id}/raw")
        elif suffix == ".pdf":
            reader = PdfReader(path)
            result["content"] = (reader.pages[0].extract_text() or "") if reader.pages else ""
        else:
            result["content"] = (
                self.extract(path)[1] or "Preview is not available for this file type."
            )
        return result

    def report(self, day=None, week=None) -> dict:
        if week:
            try:
                year, weekno = map(int, week.split("-W"))
                start = date.fromisocalendar(year, weekno, 1)
            except ValueError as exc:
                raise DigestError("Week must use YYYY-Www, for example 2026-W42") from exc
            days = [start + timedelta(days=i) for i in range(7)]
            title = f"Weekly digest — {week}"
            name = week
        else:
            days = [self.day(day)]
            title = f"Daily digest — {days[0]}"
            name = days[0].isoformat()
        rows = [r for d in days for r in self.outputs(d.isoformat())]
        tasks = self.tasks(day=days[-1].isoformat())
        lines = [
            f"# {title}",
            "",
            f"Timezone: {self.tz}",
            "",
            f"Outputs: {len(rows)} · Tasks completed: {sum(t['status'] == 'done' for t in tasks)}/{len(tasks)}",
            "",
        ]
        for agent in sorted({r["agent"] for r in rows}):
            lines.extend([f"## {agent}", ""])
            for r in sorted(
                (r for r in rows if r["agent"] == agent), key=lambda r: r["mtime"], reverse=True
            ):
                label = r["title"].replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
                lines.extend(
                    [f"- [{label}]({Path(r['path']).as_uri()}) — {r['at']}", f"  {r['summary']}"]
                )
            lines.append("")
        lines.extend(["## Tasks", ""])
        for task in tasks:
            title_text = task["title"].replace("\n", " ")
            lines.append(
                f"- [{'x' if task['status'] == 'done' else ' '}] {title_text} — {task['agent']} · {task['due_at'] or 'No deadline'} · {task['group']}"
            )
        text = "\n".join(lines) + "\n"
        directory = self.data_dir / "reports"
        directory.mkdir(exist_ok=True)
        output = directory / f"{name}.md"
        output.write_text(text, encoding="utf-8")
        return {"path": str(output), "markdown": text}

    @staticmethod
    def read_rows(path: Path):
        if path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                return list(csv.DictReader(stream))
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def export(self, output, format="json", bom=True):
        if format not in {"json", "csv"}:
            raise DigestError("Export format must be json or csv")
        with self.connection() as conn:
            data = {
                table: [dict(r) for r in conn.execute(f'SELECT * FROM "{table}" ORDER BY id')]
                for table in TABLES
            }
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        if format == "json":
            path.write_text(
                json.dumps({"schema_version": 1, "tables": data}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["table", "row_json"])
                for table, rows in data.items():
                    for row in rows:
                        writer.writerow([table, json.dumps(row, ensure_ascii=False)])
        return {"path": str(path.resolve()), "rows": sum(map(len, data.values()))}

    def import_data(self, path):
        """Merge a trusted local export by primary key; validate before committing."""
        data = self.read_rows(Path(path))
        if isinstance(data, list) and data and "row_json" in data[0]:
            groups = {table: [] for table in TABLES}
            for row in data:
                if row["table"] not in groups:
                    raise DigestError("Unknown table in CSV export")
                groups[row["table"]].append(json.loads(row["row_json"]))
        elif isinstance(data, dict) and data.get("schema_version") == 1:
            groups = data.get("tables", {})
        else:
            rows = data if isinstance(data, list) else data.get("tasks", [])
            return {"tasks_imported": self.import_tasks(rows, str(Path(path).resolve()))}
        if not isinstance(groups, dict) or set(groups) - set(TABLES):
            raise DigestError("Unknown export schema")
        count = 0
        with self.connection() as conn:
            for table, rows in groups.items():
                columns = [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')]
                for row in rows:
                    if set(row) != set(columns):
                        raise DigestError(f"Invalid columns for {table}")
                    if table == "files":
                        self.allowed(Path(row["path"]))
                    if table == "tasks":
                        self.normalize_task(row)
                    cols = ",".join('"' + c + '"' for c in columns)
                    updates = ",".join(f'"{c}"=excluded."{c}"' for c in columns if c != "id")
                    conn.execute(
                        f'INSERT INTO "{table}" ({cols}) VALUES ({",".join("?" for _ in columns)}) ON CONFLICT(id) DO UPDATE SET {updates}',
                        [row[c] for c in columns],
                    )
                    count += 1
        return {"rows_imported": count}

    def backup(self):
        directory = self.data_dir / "backups"
        directory.mkdir(exist_ok=True)
        path = directory / f"digest_{self.now().strftime('%Y%m%d_%H%M%S_%f')}.zip"
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "digest.db"
            with sqlite3.connect(self.db) as source, sqlite3.connect(snapshot) as target:
                source.backup(target)
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, "digest.db")
                archive.writestr("config.json", json.dumps(self.config, default=str, indent=2))
                for report in sorted((self.data_dir / "reports").glob("*.md")):
                    archive.write(report, "reports/" + report.name)
        return {"path": str(path)}
