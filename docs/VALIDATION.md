# Validation and visual review

Version 0.1.0 was tested locally with Python 3.12.13 and official MCP 1.30.0. Ruff lint/format passed; 21 pytest tests passed, with 90% measured total coverage and 95% core coverage. The actual three-command README workflow produced six fictional outputs grouped under three assistants and three imported tasks. CI independently runs Python 3.11 and 3.12 on Ubuntu.

Core checks include deterministic fixed-clock deadline groups and report snapshots, repeated scans/imports, unchanged-metadata content edits, configuration reassignment, deletion, missing roots, symlink escape denial, Markdown HTML escaping, CSV preview limits, PDF first-page previews, JSON/CSV round trips, backup recovery and the read-only task-board adapter. All five MCP tools were called through the official SDK. CLI errors and local HTTP mutation/read paths were exercised.

## Browser verification

The session had no Browser/IAB interaction tool, so Playwright used an isolated headless Chrome profile. Desktop viewport: 1536 × 1024; mobile viewport: 390 × 844. Browser checks passed for six output rows, assistant filters, Markdown preview, workspace scan, task completion, Markdown report download, date changes, no horizontal page overflow and no browser JavaScript errors.

A repeated date-change check exposed stale asynchronous responses overwriting the latest selection. A monotonically increasing request sequence now ignores stale responses; the interaction was rerun successfully.

## Visual fidelity ledger

The built-in image-generation tool produced [the design reference](design-reference.png) from a brief specifying an English local dashboard, white/slate/teal palette, assistant sidebar, date/scan controls, daily totals, countdown strip, output timeline, task groups and file preview. The generated reference and latest desktop/mobile browser screenshots were inspected with the image viewer in the same verification pass.

| Comparison | Reference and implementation review |
| --- | --- |
| Layout | Persistent 256 px desktop sidebar, main header, three totals, countdown strip and timeline/task columns retained. Mobile navigation becomes a horizontal selector and panels stack. |
| Copy | Title, subtitle, principal action, totals and section names match. File titles, counts and dates are supplied by real local records. |
| Color | White content, light slate sidebar, navy text, teal primary controls and red overdue state retained. No decorative image assets or runtime network resources. |
| Typography | System sans-serif with explicit heading, control, metadata and row sizes; controls do not rely on browser-default typography. |
| Interaction | Selected assistant/file states, expandable preview, completion feedback and working report download verified. Preview displays actual parsed document content. |
| Responsive behavior | 390 px view retains readable controls and source rows without horizontal page overflow. Preview can scroll and close with Escape. |

Intentional functional differences from the fictional reference: outputs are newest first as required by the specification, countdowns are ordered by their actual dates, assistant order follows available records, a single synchronized date/assistant control set avoids duplicate filter state, and the preview renders the actual source document rather than fixed sample prose. Unsupported sample file types in the concept were replaced with the specified preview formats. No raster screenshot is used as the application UI.

The implementation was visually checked against the design reference with these documented differences. [Desktop screenshot](dashboard.png) · [Mobile screenshot](mobile.png).

Known limits: this is an index of the current file state, not an immutable history; reports use current task state. PDF preview requires a text layer, CSV preview shows 50 rows, task-board writes remain in that tool, and the Web UI is intended for trusted local use. A Starlette/httpx test-client deprecation warning is emitted by the installed dependency version; all checks pass.
