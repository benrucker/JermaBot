"""Orchestration for coding-agent conversations.

This is the seam between Discord and the machinery: the agent cog only
knows this module's interface (has_conversation / run / start / close), so
changes to how conversations execute stay behind it. A conversation is
keyed by the Discord channel its replies live in and owns a branch, an
agent session, and at most one pull request per repo, updated turn by turn.
Conversations run concurrently; turns of the same conversation queue on its
lock.

A conversation is its identity — thread id, branch, session id, pull
request urls — and that is what state.json holds. The worktrees are a
cache, rebuilt from the branch on origin at the start of any turn that
finds them missing (see agent_workspace), so an entry is never dropped
because its directory went away and the code side of a conversation
continues on its own branch however long the gap. Nothing evicts
identities. What the agent remembers of the conversation is a separate
problem: that is the SDK's transcript, and it is only as durable as this
host until a later phase backs it up.
"""
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .agent_config import (
    AGENT_REPOS,
    get_conversations_root,
    get_github_token,
    get_workspace_root,
)
from .agent_runner import OnProgress, run_agent
from .agent_workspace import (
    AgentWorkspace,
    ConversationCheckout,
    PullRequestUpdate,
    WorkspaceError,  # noqa: F401 — re-exported for callers
)

STATE_FILE = 'state.json'


@dataclass
class TaskReport:
    """The outcome of one turn."""
    answer: str
    pull_requests: list[PullRequestUpdate]
    timed_out: bool


@dataclass
class Conversation:
    """One channel's ongoing work: its branch, session, and pull requests.

    The checkout names the branch and where its worktrees go; they may or
    may not be on disk at any moment, and the conversation is complete
    without them.
    """
    checkout: ConversationCheckout
    session_id: str | None = None
    pr_urls: dict[str, str] = field(default_factory=dict)  # repo -> PR url
    # Kept for the state file's shape only; nothing reads it since
    # eviction went away.
    last_active: datetime = field(default_factory=datetime.now)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def to_state(self) -> dict:
        """The persisted shape; the lock and checkout root are runtime-only."""
        return {
            'branch': self.checkout.branch,
            'session_id': self.session_id,
            'pr_urls': self.pr_urls,
            'last_active': self.last_active.isoformat(),
        }

    @classmethod
    def from_state(cls, entry: dict, root: Path,
                   workspace: AgentWorkspace) -> 'Conversation':
        """Rebuild from a persisted entry. Every field but the branch is
        optional so that entries written by older versions — and by later
        ones, which may add fields — still load."""
        last_active = entry.get('last_active')
        return cls(
            checkout=ConversationCheckout(root, entry['branch'], workspace),
            session_id=entry.get('session_id'),
            pr_urls=entry.get('pr_urls') or {},
            last_active=(datetime.fromisoformat(last_active) if last_active
                         else datetime.now()),
        )


class AgentTaskService:
    """Runs conversations, each on its own branch and checkout."""

    def __init__(self):
        self.workspace = AgentWorkspace(
            root=get_workspace_root(),
            repos=AGENT_REPOS,
            github_token=get_github_token(),
        )
        self.conversations_root = get_conversations_root()
        self.conversations: dict[int, Conversation] = {}
        self._ensure_task: asyncio.Task | None = None

    def has_conversation(self, key: int) -> bool:
        return key in self.conversations

    def start(self):
        """Load conversation state and begin readying repos in the background."""
        self._load_state()
        self._ensure_task = asyncio.create_task(self._startup())

    def close(self):
        if self._ensure_task is not None:
            self._ensure_task.cancel()

    async def run(self, key: int, prompt: str,
                  on_progress: OnProgress,
                  images: list[tuple[str, bytes]] = ()) -> TaskReport:
        """Run one turn of the keyed conversation, creating it if new.

        Turns of the same conversation queue on its lock; different
        conversations run in parallel.
        """
        conversation = self._get_or_create(key, prompt)
        async with conversation.lock:
            conversation.last_active = datetime.now()
            # The worktrees are a cache; a conversation whose turn comes
            # after a restart, a sweep, or a lost disk gets them back here.
            await self._ready()
            await conversation.checkout.ensure_materialized()
            image_paths = self._save_images(conversation.checkout.root, images)
            result = await run_agent(
                prompt=prompt,
                workspace_root=conversation.checkout.root,
                repos=self.workspace.repos,
                on_progress=on_progress,
                resume=conversation.session_id,
                image_paths=image_paths,
            )
            if result.session_id is not None:
                conversation.session_id = result.session_id
                # Persist the moment it changes: a publish that blows up
                # below must not cost the id the next turn resumes from.
                self._save_state()

            pull_requests = await conversation.checkout.publish_turn(
                prompt, result.title, result.body, conversation.pr_urls)
            for update in pull_requests:
                conversation.pr_urls[update.repo_name] = update.url

            self._save_state()
            return TaskReport(answer=result.final_text,
                              pull_requests=pull_requests,
                              timed_out=result.timed_out)

    def _save_images(self, root: Path,
                     images: list[tuple[str, bytes]]) -> list[Path]:
        if not images:
            return []
        attachments_dir = root / '_attachments'
        attachments_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for filename, data in images:
            path = attachments_dir / Path(filename).name
            path.write_bytes(data)
            paths.append(path)
        return paths

    def _get_or_create(self, key: int, prompt: str) -> Conversation:
        """Deliberately synchronous: with no await between the lookup and
        the insert, two turns arriving together cannot both create one."""
        conversation = self.conversations.get(key)
        if conversation is not None:
            return conversation

        checkout = self.workspace.new_checkout(
            self.conversations_root / str(key), prompt)
        conversation = Conversation(checkout=checkout)
        self.conversations[key] = conversation
        self._save_state()
        return conversation

    async def _startup(self):
        cloned = await self.workspace.ensure_repos()
        if cloned:
            print(f'Agent service: cloned {", ".join(cloned)} '
                  f'into {self.workspace.root}')

    async def _ready(self):
        """Wait for the startup task, restarting it if it failed."""
        task = self._ensure_task
        if task is None or task.cancelled() or (
                task.done() and task.exception() is not None):
            task = asyncio.create_task(self._startup())
            self._ensure_task = task
        await task

    def _state_path(self) -> Path:
        return self.conversations_root / STATE_FILE

    def _load_state(self):
        path = self._state_path()
        if not path.exists():
            return
        # Entries are kept whatever the disk looks like: the checkout is
        # rebuilt on demand, and an identity is the one thing that cannot
        # be recreated locally.
        for key, entry in json.loads(path.read_text(encoding='utf-8')).items():
            self.conversations[int(key)] = Conversation.from_state(
                entry, self.conversations_root / key, self.workspace)

    def _save_state(self):
        self.conversations_root.mkdir(parents=True, exist_ok=True)
        state = {str(key): conversation.to_state()
                 for key, conversation in self.conversations.items()}
        self._state_path().write_text(json.dumps(state, indent=2),
                                      encoding='utf-8')
