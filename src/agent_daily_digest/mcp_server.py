"""Official FastMCP stdio adapter over the same core service as the CLI."""

from mcp.server.fastmcp import FastMCP

from .cli import envelope
from .core import Digest, DigestError


def create_server(service: Digest | None = None) -> FastMCP:
    service = service or Digest()
    server = FastMCP("agent-daily-digest")

    def result(fn, *args):
        try:
            return envelope(fn(*args), service.warnings)
        except DigestError as exc:
            return envelope(error=exc)

    @server.tool()
    def get_digest(date: str | None = None) -> dict:
        """Read indexed outputs, grouped tasks and countdowns for an ISO date."""
        return result(service.digest, date)

    @server.tool()
    def list_outputs(date: str, agent: str | None = None) -> dict:
        """Read indexed output metadata for an ISO date and optional assistant."""
        return result(service.outputs, date, agent)

    @server.tool()
    def list_tasks(status: str | None = None, due_before: str | None = None) -> dict:
        """Read local tasks and optional read-only task-board entries."""
        return result(service.tasks, status, due_before)

    @server.tool()
    def add_task(
        title: str, agent: str = "Unassigned", due_at: str | None = None, priority: int = 2
    ) -> dict:
        """WRITE: add a persistent local task; identical arguments are idempotent."""
        return result(service.add_task, title, agent, due_at, priority)

    @server.tool()
    def complete_task(id: str) -> dict:
        """WRITE: mark a local task complete. Cannot modify the external task board."""
        return result(service.complete_task, id)

    return server


if __name__ == "__main__":
    create_server().run(transport="stdio")
