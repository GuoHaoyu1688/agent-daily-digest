"""Generate six fictional outputs and refresh their timestamps for today's demo."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.pdfgen import canvas

root = Path(__file__).parent / "workspace"
for name in ("report", "study", "content"):
    (root / name).mkdir(parents=True, exist_ok=True)
(root / "report/operations.md").write_text(
    "# Weekly operations report\n\nSummary of fictional project milestones, operational updates and next steps.\n\n## Next steps\n\n- Review the sample report.\n- Confirm the next milestone.\n",
    encoding="utf-8",
)
(root / "report/Cashflow summary.csv").write_text(
    "month,income,expenses\n2026-09,8000,5500\n2026-10,8500,5800\n", encoding="utf-8"
)
(root / "study/Study notes.txt").write_text(
    "Study notes\nConsolidated notes from a fictional study session on data interpretation.\n",
    encoding="utf-8",
)
(root / "content/thread.md").write_text(
    "# Draft content thread\n\nA short thread about productivity habits for focused work.\n\n1. Define the next step.\n2. Set aside a quiet interval.\n3. Review the result.\n",
    encoding="utf-8",
)
(root / "content/Content calendar.csv").write_text(
    "date,title,status\n2026-10-16,Sample productivity note,draft\n2026-10-17,Sample study reflection,idea\n",
    encoding="utf-8",
)
pdf = canvas.Canvas(str(root / "study/Review practice results.pdf"))
pdf.drawString(60, 790, "Review practice results")
pdf.drawString(60, 765, "Fictional practice: 8 out of 10 correct. Review two mistakes.")
pdf.save()
now = datetime.now(ZoneInfo("Asia/Shanghai"))
for path, hour, minute in [
    ("report/operations.md", 9, 12),
    ("study/Review practice results.pdf", 10, 3),
    ("content/thread.md", 11, 28),
    ("report/Cashflow summary.csv", 13, 5),
    ("study/Study notes.txt", 15, 20),
    ("content/Content calendar.csv", 16, 45),
]:
    timestamp = now.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()
    os.utime(root / path, (timestamp, timestamp))
tasks = [
    {
        "id": "review",
        "title": "Review the draft report",
        "agent": "Report assistant",
        "due_at": (now.date() - timedelta(days=1)).isoformat(),
        "priority": 1,
    },
    {
        "id": "practice",
        "title": "Complete practice set",
        "agent": "Study assistant",
        "due_at": now.date().isoformat(),
        "priority": 2,
    },
    {
        "id": "draft",
        "title": "Prepare content draft",
        "agent": "Content assistant",
        "due_at": now.date().isoformat(),
        "priority": 2,
    },
]
(root / "tasks.json").write_text(json.dumps(tasks, indent=2) + "\n", encoding="utf-8")
print("Prepared six fictional outputs and three tasks for " + now.date().isoformat())
