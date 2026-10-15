import base64
import json
import os
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from reportlab.pdfgen import canvas

from agent_daily_digest.core import Digest, DigestError


def write(service, name, text="Sample", hour=10):
    path = service.roots[0] / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    timestamp = datetime(2026, 10, 15, hour, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    os.utime(path, (timestamp, timestamp))
    return path


def test_scan_incremental_reassignment_and_deletion(service):
    one = write(service, "report/result.md", "# Report\nText")
    write(service, "study/notes.txt")
    write(service, "content/draft.md")
    assert service.scan()["files_changed"] == 3
    assert service.scan()["files_changed"] == 0
    assert service.digest("2026-10-15")["agents"] == {
        "Report assistant": 1,
        "Study assistant": 1,
        "Content assistant": 1,
    }
    stat = one.stat()
    one.write_text("# Report\nMore")
    os.utime(one, (stat.st_atime, stat.st_mtime))
    assert service.scan()["files_changed"] == 1
    service.config["agents"][0]["name"] = "Updated assistant"
    assert service.scan()["files_changed"] == 1
    assert service.outputs("2026-10-15", "Updated assistant")[0]["summary"].endswith("More")
    one.unlink()
    assert service.scan()["files_removed"] == 1


def test_config_relative_paths_and_timezones(service):
    assert service.roots[0] == service.config_path.parent / "workspace"
    assert service.parse_time("2026-10-15T01:00:00Z").hour == 9
    with pytest.raises(DigestError):
        service.day("yesterday")
    with pytest.raises(DigestError):
        service.parse_time("nonsense")


def test_ignores_size_escape_and_removed_roots(service, tmp_path):
    write(service, ".env", "private")
    write(service, "report/ok.md")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (service.roots[0] / "escape.txt").symlink_to(outside)
    write(service, "large.txt", "A" * 100)
    service.max_size = 30
    result = service.scan()
    assert result["files_scanned"] == 1
    assert len(service.warnings) == 2
    with pytest.raises(DigestError, match="outside"):
        service.allowed(outside)
    service.roots = []
    assert service.scan()["files_removed"] == 1


def test_missing_root_keeps_index(service):
    write(service, "report/ok.md")
    service.scan()
    service.roots[0].rename(service.roots[0].with_name("unavailable"))
    assert service.scan()["files_removed"] == 0
    assert len(service.outputs("2026-10-15")) == 1
    assert service.warnings


def test_duplicate_task_import_and_completion_override(service):
    source = service.roots[0] / "tasks.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "a",
                    "title": "Read report",
                    "agent": "Report assistant",
                    "due_at": "2026-10-15",
                }
            ]
        )
    )
    assert service.scan()["tasks_imported"] == 1
    assert service.scan()["tasks_imported"] == 1
    tasks = service.tasks()
    assert len(tasks) == 1
    assert tasks[0]["group"] == "today"
    service.complete_task(tasks[0]["id"])
    service.scan()
    assert service.tasks()[0]["status"] == "done"
    assert service.tasks()[0]["group"] == "done"


def test_deadline_groups_and_filters(service):
    for title, due in [
        ("old", "2026-10-14"),
        ("today", "2026-10-15"),
        ("week", "2026-10-18"),
        ("later", "2026-10-19"),
        ("none", None),
    ]:
        service.add_task(title, "Example assistant", due)
    assert {t["title"]: t["group"] for t in service.tasks()} == {
        "old": "overdue",
        "today": "today",
        "week": "week",
        "later": "later",
        "none": "undated",
    }
    assert len(service.tasks(due_before="2026-10-15")) == 2
    assert len(service.tasks(agent="No such assistant")) == 0
    assert service.add_task("today", "Example assistant", "2026-10-15")["id"] == next(
        t["id"] for t in service.tasks() if t["title"] == "today"
    )
    assert len(service.tasks()) == 5


@pytest.mark.parametrize(
    "kwargs", [{"title": ""}, {"title": "A", "priority": 4}, {"title": "A", "due_at": "bad"}]
)
def test_invalid_tasks(service, kwargs):
    with pytest.raises(DigestError):
        service.add_task(**kwargs)
    assert service.tasks() == []


def test_task_import_is_atomic(service):
    with pytest.raises(DigestError):
        service.import_tasks([{"title": "Good"}, {"title": "Bad", "status": "unknown"}], "example")
    assert service.tasks() == []


def test_csv_import(service):
    path = service.roots[0] / "tasks.csv"
    path.write_text(
        "\ufeffid,title,agent,due_at,status,priority\na,Review,Example assistant,2026-10-15,todo,1\n",
        encoding="utf-8",
    )
    assert service.scan()["tasks_imported"] == 1
    assert service.tasks()[0]["priority"] == 1


def test_previews_and_safe_markdown(service):
    write(service, "report/a.md", "# Hello\n<script>alert(1)</script>\n[x](javascript:alert(1))")
    write(service, "report/a.csv", "\n".join(f"{i},value" for i in range(100)))
    path = service.roots[0] / "report/a.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(20, 700, "First page")
    pdf.showPage()
    pdf.drawString(20, 700, "Second page")
    pdf.save()
    image = service.roots[0] / "report/a.png"
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
    )
    service.scan()
    with service.connection() as conn:
        rows = {Path(r["path"]).suffix: r["id"] for r in conn.execute("SELECT * FROM files")}
    html = service.preview(rows[".md"])["content"]
    assert "<script>" not in html and 'href="javascript:' not in html
    assert len(service.preview(rows[".csv"])["content"]) == 50
    assert service.preview(rows[".png"])["kind"] == "image"
    assert "First page" in service.preview(rows[".pdf"])["content"]
    assert "Second page" not in service.preview(rows[".pdf"])["content"]
    with pytest.raises(DigestError):
        service.preview("unknown")
    image.unlink()
    with pytest.raises(DigestError):
        service.preview(rows[".png"])


def test_daily_report_exact_and_weekly(service):
    path = write(service, "report/result.md", "# A result\nDetails", hour=10)
    service.scan()
    service.add_task("Read it", "Report assistant", "2026-10-15")
    report = service.report("2026-10-15")
    expected = f"# Daily digest — 2026-10-15\n\nTimezone: Asia/Shanghai\n\nOutputs: 1 · Tasks completed: 0/1\n\n## Report assistant\n\n- [A result]({path.as_uri()}) — 2026-10-15T10:00:00+08:00\n  # A result Details\n\n## Tasks\n\n- [ ] Read it — Report assistant · 2026-10-15 · today\n"
    assert report["markdown"] == expected
    assert Path(report["path"]).read_text() == expected
    assert service.report("2026-10-15") == report
    assert "Weekly digest — 2026-W42" in service.report(week="2026-W42")["markdown"]
    with pytest.raises(DigestError):
        service.report(week="2026-W99")
    assert service.digest("2026-10-15")["countdowns"][0]["days_remaining"] == 45


@pytest.mark.parametrize("format", ["json", "csv"])
def test_roundtrip_and_backup(service, tmp_path, format):
    write(service, "report/a.md")
    service.scan()
    original = service.add_task("Round trip")
    path = tmp_path / f"export.{format}"
    service.export(path, format)
    if format == "csv":
        assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    target = Digest(tmp_path / "restored", service.config_path, clock=service.clock)
    target.import_data(path)
    target.import_data(path)
    assert target.tasks()[0]["id"] == original["id"]
    assert len(target.tasks()) == 1
    assert len(target.outputs("2026-10-15")) == 1
    backup = service.backup()
    with zipfile.ZipFile(backup["path"]) as archive:
        assert {"digest.db", "config.json"}.issubset(archive.namelist())
        archive.extract("digest.db", tmp_path / "backup")
    with sqlite3.connect(tmp_path / "backup/digest.db") as conn:
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1


def test_import_rejects_unknown_tables_and_columns(service, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema_version": 1, "tables": {"sqlite_master": []}}))
    with pytest.raises(DigestError):
        service.import_data(path)
    path.write_text(json.dumps({"schema_version": 1, "tables": {"tasks": [{"id": "bad"}]}}))
    with pytest.raises(DigestError):
        service.import_data(path)


def test_read_only_task_board_adapter(service, tmp_path):
    path = tmp_path / "board.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE tasks (id TEXT,title TEXT,assignee TEXT,due_at TEXT,status TEXT,priority INTEGER,updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO tasks VALUES ('a','Board task','Example assistant','2026-10-15','review',1,'2026-10-15')"
        )
    service.config["task_board_db"] = str(path)
    assert service.tasks()[0]["status"] == "blocked"
    with pytest.raises(DigestError, match="task-board"):
        service.complete_task("board:a")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT status FROM tasks").fetchone()[0] == "review"
    path.unlink()
    assert service.board_tasks() == []
    assert service.warnings


def test_toml_and_invalid_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('roots = ["workspace"]\ntimezone = "UTC"\n')
    service = Digest(tmp_path / "data", path)
    assert str(service.tz) == "UTC"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump([1, 2]))
    with pytest.raises(DigestError):
        Digest(tmp_path / "data", path)
