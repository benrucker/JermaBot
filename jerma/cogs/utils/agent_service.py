"""Orchestration for coding-agent tasks.

This is the seam between Discord and the machinery: the agent cog only
knows this module's interface (busy / run / start / close), so changes to
how tasks execute — workspace layout, parallelism, queueing — stay behind
it. Agent output is streamed through the on_text callback; outcomes come
back in a TaskReport.
"""
import asyncio
from dataclasses import dataclass

from .agent_config import (
    AGENT_REPOS,
    get_github_token,
    get_workspace_root,
)
from .agent_runner import OnText, run_agent
from .agent_workspace import (
    AgentWorkspace,
    OpenedPullRequest,
    WorkspaceError,  # noqa: F401 — re-exported for callers
)


class AgentBusyError(Exception):
    """A task is already running."""


@dataclass
class TaskReport:
    """The outcome of one task; the answer itself was already streamed."""
    pull_requests: list[OpenedPullRequest]
    timed_out: bool


class AgentTaskService:
    """Runs one coding task at a time against a persistent workspace."""

    def __init__(self):
        self.workspace = AgentWorkspace(
            root=get_workspace_root(),
            repos=AGENT_REPOS,
            github_token=get_github_token(),
        )
        self._lock = asyncio.Lock()
        self._ensure_task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def start(self):
        """Begin cloning any missing repos in the background."""
        self._ensure_task = asyncio.create_task(self._clone_missing())

    def close(self):
        if self._ensure_task is not None:
            self._ensure_task.cancel()

    async def run(self, prompt: str, on_text: OnText) -> TaskReport:
        """Run one task: ready the workspace, run the agent, publish changes."""
        if self.busy:
            raise AgentBusyError()

        async with self._lock:
            await self._ready()
            await self.workspace.reset_all()
            result = await run_agent(
                prompt=prompt,
                workspace_root=self.workspace.root,
                repos=self.workspace.repos,
                on_text=on_text,
            )

            pull_requests = [
                await self.workspace.publish(name, prompt, result.final_text)
                for name in await self.workspace.dirty_repos()
            ]

            return TaskReport(pull_requests=pull_requests,
                              timed_out=result.timed_out)

    async def _ready(self):
        """Wait for the startup clone task, restarting it if it failed."""
        task = self._ensure_task
        if task is None or task.cancelled() or (
                task.done() and task.exception() is not None):
            task = asyncio.create_task(self._clone_missing())
            self._ensure_task = task
        await task

    async def _clone_missing(self):
        cloned = await self.workspace.ensure_repos()
        if cloned:
            print(f'Agent service: cloned {", ".join(cloned)} '
                  f'into {self.workspace.root}')
