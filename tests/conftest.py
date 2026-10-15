from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import yaml

from agent_daily_digest.core import Digest


@pytest.fixture
def service(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "roots": ["workspace"],
                "agents": [
                    {"glob": "report/**", "name": "Report assistant"},
                    {"prefix": "study/", "name": "Study assistant"},
                    {"regex": "^content/", "name": "Content assistant"},
                ],
                "countdowns": [{"name": "Exam day", "date": "2026-11-29"}],
            }
        )
    )
    return Digest(
        tmp_path / "data",
        config,
        clock=lambda: datetime(2026, 10, 15, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
