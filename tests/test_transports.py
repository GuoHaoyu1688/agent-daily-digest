import asyncio
import json

from fastapi.testclient import TestClient

from agent_daily_digest.cli import main
from agent_daily_digest.mcp_server import create_server
from agent_daily_digest.web import create_app


def test_cli_contract(tmp_path, capsys):
    args = ["--data-dir", str(tmp_path / "data")]
    assert main(["tasks", *args, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "data": [], "warnings": []}
    assert main([*args, "task", "done", "missing", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "NOT_FOUND"
    assert main([*args, "task", "add", "Example task", "--priority", "9", "--json"]) == 2
    assert not json.loads(capsys.readouterr().out)["ok"]
    assert main([*args, "nonsense", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "ARGUMENT_ERROR"
    assert main(["--data-dir", "--json"]) == 2
    capsys.readouterr()
    assert main([*args, "backup", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["path"].endswith(".zip")


def test_web_lifecycle_and_origin(service):
    client = TestClient(create_app(service))
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/digest?date=2026-10-15").json()["data"]["counts"]["outputs"] == 0
    task = client.post("/api/tasks", json={"title": "Example task"}).json()["data"]
    assert client.post(f"/api/tasks/{task['id']}/done").json()["data"]["status"] == "done"
    assert client.post("/api/tasks", json={"title": "Bad", "priority": 10}).status_code == 422
    assert client.post("/api/scan").status_code == 200
    assert (
        client.post("/api/scan", headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    )
    assert client.get("/api/files/unknown").status_code == 404
    report = client.get("/api/report?date=2026-10-15")
    assert "Daily digest" in report.text
    assert "attachment" in report.headers["content-disposition"]


def test_all_mcp_tools(service):
    async def run():
        server = create_server(service)
        tools = await server.list_tools()
        assert {tool.name for tool in tools} == {
            "get_digest",
            "list_outputs",
            "list_tasks",
            "add_task",
            "complete_task",
        }
        results = {}
        for name, args in [
            ("get_digest", {"date": "2026-10-15"}),
            ("list_outputs", {"date": "2026-10-15"}),
            ("list_tasks", {}),
            ("add_task", {"title": "MCP task"}),
        ]:
            result = await server.call_tool(name, args)
            structured = result[1] if isinstance(result, tuple) else json.loads(result[0].text)
            assert structured["ok"]
            results[name] = structured
        task_id = results["add_task"]["data"]["id"]
        result = await server.call_tool("complete_task", {"id": task_id})
        structured = result[1] if isinstance(result, tuple) else json.loads(result[0].text)
        assert structured["data"]["status"] == "done"

    asyncio.run(run())
