# Agent Daily Digest

A local dashboard and CLI that turn files, tasks and deadlines produced by multiple AI assistants into a daily briefing. No model calls, cloud accounts or external network requests are used at runtime.

## Install and quick start

Python 3.11+ is required. From a checkout, these three shell commands install the application, prepare fictional examples and open the dashboard:

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
python examples/prepare.py && digest --config examples/config.yaml scan
 digest --config examples/config.yaml serve
```

Open `http://127.0.0.1:8761`. Use the current date to view six fictional outputs from three assistants. Date selection, assistant filters, previews, task completion, workspace scanning and Markdown export work against the local SQLite database. For a headless check, run `digest --config examples/config.yaml today --json` instead of the final command.

Runtime-only installation: `pip install -r requirements.txt && pip install -e . --no-deps`. The example generator needs the `dev` extra because it creates a synthetic PDF.

## Features

- Incremental, repeatable indexing of local roots, configurable glob inclusion/exclusion and assistant assignment by glob, prefix or regular expression. Changed assignment rules take effect on the next scan.
- Output timeline sorted newest first. Markdown, CSV (first 50 rows), raster images and the first page of a PDF can be previewed.
- Deterministic extraction of titles and 200-character summaries, without a language model.
- Tasks imported from JSON/CSV, local task commands and an optional read-only connection to `agent-task-board`.
- Overdue / today / this week / later / undated deadline groups; countdowns and daily or ISO-week Markdown reports.
- JSON/CSV round-trip exports, dated SQLite online backups and official FastMCP stdio tools.

## Configuration and data

See [`examples/config.yaml`](examples/config.yaml). YAML and TOML are supported. Relative configured paths resolve against the configuration file's directory. Without a configuration file, the root is `./workspace`. Runtime data defaults to `./data`; override it with `--data-dir` or `DIGEST_DATA_DIR`. The database is `digest.db`, generated reports are under `reports/` inside the data directory, and backup ZIPs are under `backups/`.

```yaml
roots: [workspace]
timezone: Asia/Shanghai
max_file_size: 10000000
include: ['**/*']
exclude: ['**/.git/**', '**/.env', tasks.json, tasks.csv]
agents:
  - {glob: 'report/**', name: Report assistant}
  - {prefix: 'study/', name: Study assistant}
  - {regex: '^content/', name: Content assistant}
countdowns:
  - {name: Project review, date: '2026-10-22'}
# Optional: task_sources: [tasks.json, tasks.csv]
# Optional: task_board_db: ../agent-task-board/data/tasks.db
```

The optional board adapter reads the `tasks` table's `id`, `title`, `assignee`, `due_at`, `status`, `priority` and `updated_at` fields. `claimed`/`running` map to `doing`; `review`/`failed` map to `blocked`. Complete those tasks in the task-board application itself.

Task JSON is an array of objects (or `{ "tasks": [...] }`); CSV uses the same columns:

```json
[{"id":"review-1","title":"Review sample report","agent":"Report assistant","due_at":"2026-10-15","status":"todo","priority":2}]
```

Dates and timestamps use ISO 8601. Date-only deadlines remain due for the entire local calendar day. A bare local timestamp uses the configured timezone. Task imports are deduplicated by source and ID (or a deterministic key); completing an imported task creates a local override retained on subsequent imports. Removing a row from an import file does not delete its existing task. File removal, root removal and exclusion changes remove obsolete file-index entries. Temporarily unavailable roots preserve their previous index and produce a warning.

## CLI reference

All commands support `--data-dir`, `--config`, `--json`, `--quiet`, `--version`; `--progress` writes scan progress to stderr. Global options may precede or follow subcommands. No command requires a TTY.

| Command | Purpose |
|---|---|
| `digest scan` | Index changed files and import tasks |
| `digest today --date 2026-10-15 --agent 'Report assistant'` | Read a daily briefing |
| `digest tasks --status todo --due-before 2026-10-22` | Filter tasks |
| `digest task add 'Review report' --agent 'Report assistant' --due 2026-10-22 --priority 1` | Add a repeat-safe task |
| `digest task done ID` | Complete a local task |
| `digest preview ID` | Read a file preview |
| `digest daily --date 2026-10-15` | Write a daily Markdown report |
| `digest weekly --week 2026-W42` | Write an ISO-week report |
| `digest export --format csv --out snapshot.csv` | Export all indexed tables (UTF-8 BOM by default) |
| `digest export --format json --out snapshot.json` | Export JSON tables |
| `digest import snapshot.json` | Merge an export, or import a plain task file |
| `digest backup` | Write a dated ZIP with a consistent database snapshot |
| `digest serve --host 127.0.0.1 --port 8761` | Start the local dashboard |
| `digest mcp` | Start the stdio MCP server |

JSON responses are a single object: `{"ok":true,"data":...,"warnings":[]}`. Errors use `{"ok":false,"error":{"code":"NOT_FOUND","message":"Task not found"}}`. Exit codes: 0 success, 1 operation failure, 2 invalid arguments/data, 3 missing resource. Export CSV uses `table,row_json` to preserve nested values and types; task-import CSV uses ordinary task columns. Add `--no-bom` to CSV export for plain UTF-8. Imports merge by primary key; make a backup before importing over existing data.

## MCP

Install the package, then configure an MCP client with:

```json
{"mcpServers":{"daily-digest":{"command":"digest","args":["--data-dir","/workspace/data/agent-daily-digest","--config","/workspace/apps/agent-daily-digest/config.yaml","mcp"]}}}
```

Tools: `get_digest(date?)`, `list_outputs(date, agent?)`, `list_tasks(status?, due_before?)`, `add_task(title, agent?, due_at?, priority?)`, `complete_task(id)`. Write tools describe their side effects and share the CLI core. Run `digest scan` before querying to refresh the index. [`AGENTS.md`](AGENTS.md) contains installation commands, JSON examples and operational recipes.

## Verification

```sh
ruff check .
ruff format --check .
pytest -q --cov=agent_daily_digest
```

CI runs on Ubuntu with Python 3.11 and 3.12. Tests include fixed-time deadline grouping, deterministic reports, config reassignment, duplicate imports, changed/deleted files, previews, directory escape rejection, transport errors, MCP tools and backups.

## FAQ and limitations

- **Does it watch remote workspaces?** No. It scans local configured directories on command; use cron to refresh them.
- **Is this a multi-user server?** No. It defaults to loopback, has no accounts, and rejects cross-origin browser requests. Keep it on a trusted local machine.
- **Are previews full document editors?** No. CSV previews are capped at 50 rows and PDF previews use only first-page text. Scanned PDFs need an external OCR tool.
- **What does a daily report mean?** File dates use modification timestamps in the configured timezone. Rewriting a file moves it to the new date; the index is not an immutable historical file archive. Task completion counts use current indexed state, not a reconstructed historical task state.
- **How do I restore a backup?** Stop the app, preserve the existing data directory, extract `digest.db` into a new data directory and restore your YAML/TOML configuration from the included JSON representation. Never replace a live WAL database in place.
- **Are private files published?** Runtime data, databases, reports and `.env` are ignored by Git. Review configured roots yourself before sharing generated reports, which contain local paths and source excerpts.

## Roadmap

Optional file-system watch mode, richer historical snapshots and additional task-board adapters.

## License

MIT. All bundled example content is fictional.
