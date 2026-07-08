"""Orchestration for coding-agent conversations.

This is the seam between Discord and the machinery: the agent cog only
knows this module's interface (has_conversation / run / start / close), so
changes to how conversations execute stay behind it. A conversation is
keyed by the Discord channel its replies live in and owns a checkout (git
worktrees on a dedicated branch), an agent session, and at most one pull
request per repo, updated turn by turn. Conversations run concurrently;
turns of the same conversation queue on its lock.

Conversation state persists to a JSON file beside the checkouts, so
conversations survive restarts; the agent sessions themselves resume from
the SDK's on-disk transcripts, which are keyed by the checkout directory —
another reason each conversation keeps one directory for life.
"""
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .agent_config import (
    AGENT_CONVERSATION_IDLE_DAYS,
    AGENT_EVICTION_INTERVAL_SECONDS,
    AGENT_REPOS,
    get_conversations_root,
    get_github_token,
    get_workspace_root,
)
from .agent_runner import OnProgress, run_agent, split_reply
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
    """One channel's ongoing work: its checkout, session, and pull requests."""
    checkout: ConversationCheckout
    session_id: str | None = None
    pr_urls: dict[str, str] = field(default_factory=dict)  # repo -> PR url
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
        return cls(
            checkout=ConversationCheckout(root, entry['branch'], workspace),
            session_id=entry['session_id'],
            pr_urls=entry['pr_urls'],
            last_active=datetime.fromisoformat(entry['last_active']),
        )


class AgentTaskService:
    """Runs conversations, each against its own persistent checkout."""

    def __init__(self):
        self.workspace = AgentWorkspace(
            root=get_workspace_root(),
            repos=AGENT_REPOS,
            github_token=get_github_token(),
        )
        self.conversations_root = get_conversations_root()
        self.conversations: dict[int, Conversation] = {}
        self._ensure_task: asyncio.Task | None = None
        self._evict_task: asyncio.Task | None = None

    def has_conversation(self, key: int) -> bool:
        return key in self.conversations

    def start(self):
        """Load conversation state and begin readying repos in the background."""
        self._load_state()
        self._ensure_task = asyncio.create_task(self._startup())
        self._evict_task = asyncio.create_task(self._evict_loop())

    def close(self):
        for task in (self._ensure_task, self._evict_task):
            if task is not None:
                task.cancel()

    async def run(self, key: int, prompt: str,
                  on_progress: OnProgress) -> TaskReport:
        """Run one turn of the keyed conversation, creating it if new.

        Turns of the same conversation queue on its lock; different
        conversations run in parallel.
        """
        conversation = await self._get_or_create(key, prompt)
        async with conversation.lock:
            conversation.last_active = datetime.now()
            result = await run_agent(
                prompt=prompt,
                workspace_root=conversation.checkout.root,
                repos=self.workspace.repos,
                on_progress=on_progress,
                resume=conversation.session_id,
            )
            if result.session_id is not None:
                conversation.session_id = result.session_id

            pull_requests = []
            if names := await conversation.checkout.dirty_repos():
                title, body = split_reply(prompt, result.final_text)
                pull_requests = await asyncio.gather(*(
                    conversation.checkout.publish_turn(
                        name, prompt, title, body,
                        pr_url=conversation.pr_urls.get(name))
                    for name in names))
            for update in pull_requests:
                conversation.pr_urls[update.repo_name] = update.url

            self._save_state()
            return TaskReport(answer=result.final_text,
                              pull_requests=pull_requests,
                              timed_out=result.timed_out)

    async def _get_or_create(self, key: int, prompt: str) -> Conversation:
        conversation = self.conversations.get(key)
        if conversation is not None:
            return conversation

        await self._ready()
        checkout = await self.workspace.create_checkout(
            self._conversation_root(key), prompt)
        conversation = Conversation(checkout=checkout)
        self.conversations[key] = conversation
        self._save_state()
        return conversation

    async def _evict_loop(self):
        """Evict idle conversations at boot and daily after. Eviction is
        housekeeping, deliberately its own background job so cleanup never
        delays or fails a user's turn."""
        while True:
            try:
                await self._evict_stale()
            except Exception as error:
                print(f'Agent service: eviction failed: {error}')
            await asyncio.sleep(AGENT_EVICTION_INTERVAL_SECONDS)

    async def _evict_stale(self):
        """Drop conversations idle past the limit; their PRs live on GitHub."""
        cutoff = datetime.now() - timedelta(days=AGENT_CONVERSATION_IDLE_DAYS)
        stale = [key for key, conversation in self.conversations.items()
                 if conversation.last_active < cutoff
                 and not conversation.lock.locked()]
        for key in stale:
            await self.workspace.remove_checkout(
                self.conversations.pop(key).checkout)
        if stale:
            self._save_state()
            print(f'Agent service: evicted {len(stale)} idle conversation(s)')

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

    def _conversation_root(self, key: int | str) -> Path:
        return self.conversations_root / str(key)

    def _load_state(self):
        path = self._state_path()
        if not path.exists():
            return
        for key, entry in json.loads(path.read_text(encoding='utf-8')).items():
            root = self._conversation_root(key)
            if not root.exists():
                print(f'Agent service: dropping conversation {key}; '
                      'its checkout is gone')
                continue
            self.conversations[int(key)] = Conversation.from_state(
                entry, root, self.workspace)

    def _save_state(self):
        self.conversations_root.mkdir(parents=True, exist_ok=True)
        state = {str(key): conversation.to_state()
                 for key, conversation in self.conversations.items()}
        self._state_path().write_text(json.dumps(state, indent=2),
                                      encoding='utf-8')
