"""Non-interactive command-line interface with stable JSON envelopes."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from yaml import YAMLError

from . import __version__
from .core import Digest, DigestError


def envelope(data=None, warnings=None, error=None):
    if error:
        return {"ok": False, "error": {"code": error.code, "message": str(error)}}
    return {"ok": True, "data": data, "warnings": warnings or []}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise DigestError(message, "ARGUMENT_ERROR", 2)


def normalize_globals(argv):
    """Permit global options before or after any subcommand."""
    front, rest = [], []
    valued = {"--data-dir", "--config"}
    flags = {"--json", "--quiet", "--version", "--progress"}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in valued:
            if i + 1 == len(argv):
                raise DigestError(f"{token} requires a value", "ARGUMENT_ERROR")
            front.extend(argv[i : i + 2])
            i += 2
        elif token in flags or any(token.startswith(k + "=") for k in valued):
            front.append(token)
            i += 1
        else:
            rest.append(token)
            i += 1
    return front + rest


def parser():
    p = Parser(description="Local output and deadline summaries for AI assistants")
    p.add_argument("--data-dir")
    p.add_argument("--config")
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--progress", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="Index changed files and import configured tasks")
    today = sub.add_parser("today", help="Read a daily digest")
    today.add_argument("--date")
    today.add_argument("--agent")
    daily = sub.add_parser("daily", help="Write a Markdown daily report")
    daily.add_argument("--date")
    weekly = sub.add_parser("weekly", help="Write a Markdown weekly report")
    weekly.add_argument("--week", required=True)
    tasks = sub.add_parser("tasks", help="List and group tasks")
    tasks.add_argument("--status", choices=["todo", "doing", "done", "blocked"])
    tasks.add_argument("--agent")
    tasks.add_argument("--due-before")
    task = sub.add_parser("task", help="Add or complete a task").add_subparsers(
        dest="action", required=True
    )
    add = task.add_parser("add")
    add.add_argument("title")
    add.add_argument("--agent", default="Unassigned")
    add.add_argument("--due", "--due-at", dest="due_at")
    add.add_argument("--priority", type=int, default=2)
    done = task.add_parser("done")
    done.add_argument("id")
    preview = sub.add_parser("preview")
    preview.add_argument("id")
    export = sub.add_parser("export")
    export.add_argument("--format", choices=["json", "csv"], default="json")
    export.add_argument("--out", required=True)
    export.add_argument("--no-bom", action="store_true")
    importer = sub.add_parser("import")
    importer.add_argument("path")
    sub.add_parser("backup")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8761)
    sub.add_parser("mcp")
    return p


def dispatch(args, service):
    cmd = args.command
    if cmd == "scan":
        return service.scan((lambda s: print(s, file=sys.stderr)) if args.progress else None)
    if cmd == "today":
        return service.digest(args.date, args.agent)
    if cmd == "daily":
        return service.report(day=args.date)
    if cmd == "weekly":
        return service.report(week=args.week)
    if cmd == "tasks":
        return service.tasks(args.status, args.due_before, args.agent)
    if cmd == "task":
        return (
            service.add_task(args.title, args.agent, args.due_at, args.priority)
            if args.action == "add"
            else service.complete_task(args.id)
        )
    if cmd == "preview":
        return service.preview(args.id)
    if cmd == "export":
        return service.export(args.out, args.format, not args.no_bom)
    if cmd == "import":
        if not Path(args.path).is_file():
            raise DigestError("Import file not found", "NOT_FOUND", 3)
        return service.import_data(args.path)
    if cmd == "backup":
        return service.backup()
    if cmd == "serve":
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(service), host=args.host, port=args.port)
        return None
    if cmd == "mcp":
        from .mcp_server import create_server

        create_server(service).run(transport="stdio")
        return None
    raise DigestError("Unknown command")


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in raw
    try:
        if "--version" in raw:
            print(json.dumps(envelope({"version": __version__})) if json_mode else __version__)
            return 0
        args = parser().parse_args(normalize_globals(raw))
        service = Digest(args.data_dir, args.config)
        data = dispatch(args, service)
        if args.command not in {"mcp", "serve"}:
            if args.json:
                print(json.dumps(envelope(data, service.warnings), ensure_ascii=False))
            elif not args.quiet:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            if not args.json and not args.quiet:
                for warning in service.warnings:
                    print(warning, file=sys.stderr)
        return 0
    except DigestError as exc:
        if json_mode:
            print(json.dumps(envelope(error=exc)))
        else:
            print(f"{exc.code}: {exc}", file=sys.stderr)
        return exc.exit_code
    except (OSError, ValueError, KeyError, sqlite3.Error, YAMLError, ZoneInfoNotFoundError) as exc:
        error = DigestError(str(exc), "OPERATION_FAILED", 1)
        print(
            json.dumps(envelope(error=error)) if json_mode else f"{error.code}: {error}",
            file=sys.stdout if json_mode else sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
