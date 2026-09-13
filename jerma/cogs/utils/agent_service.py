"""Orchestration for coding-agent conversations.

The agent cog knows only this module's interface (has_conversation / run /
start / close), so changes to how conversations execute stay behind it. A
conversation is keyed by the Discord channel its replies live in, and it
owns a branch, an agent session, and at most one pull request per repo,
updated turn by turn. Conversations run concurrently; turns of the same
conversation queue on its lock.

A conversation is its identity, meaning its thread id, branch, session id,
and pull request urls, and state.json holds exactly that. The worktrees
are a cache. Any turn that finds them missing rebuilds them from the
branch on origin (see agent_workspace), so nothing drops an entry because
its directory went away, and the code side of a conversation continues on
its own branch however long the gap. Nothing evicts identities.

When even state.json is gone, recover() puts an identity back from what
Discord and GitHub still know: the pull requests a thread announced, or a
search of the agent's pull requests for the one that names the thread
(R3.3). The cog supplies the Discord half; everything after it is here.

What the agent remembers of a conversation is the SDK's transcript, and
that lives only where the SDK wrote it. So every turn starts by deciding
where its context comes from (R2.2). Resume the session when this host
still has the transcript. Otherwise run a new session with the
conversation's history rebuilt from its Discord thread. Rebuilding is the
caller's job, since only the cog knows Discord. This module decides when a
rebuild is needed and says so in the thread, in the one muted line R4.3
allows. When a resume that should have worked fails inside the SDK, the
same turn falls through to that rebuild (R2.4).
"""
import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .agent_config import (
    AGENT_REPOS,
    get_conversations_root,
    get_github_token,
    get_workspace_root,
)
from .agent_runner import (
    AgentRunResult,
    OnProgress,
    SessionResumeError,
    local_transcript_path,
    run_agent,
)
from .agent_title import generate_title
from .agent_workspace import (
    AgentWorkspace,
    ConversationCheckout,
    PullRequestUpdate,
    WorkspaceError,  # noqa: F401. Re-exported for callers.
    one_line,
)

STATE_FILE = 'state.json'
# The one thing the owner hears about recovery, in Discord's muted subtext
# style so it reads as harness chatter rather than the agent talking
# (R4.3). Its wording is the spec's, verbatim.
RELOADING_NOTE = '-# _Reloading thread history. Some context might be lost._'


@dataclass
class ReconstructedHistory:
    """A conversation's history as its thread remembers it (R2c).

    The caller builds this, since only the cog knows Discord, and a turn
    with no transcript to resume gets it in front of its prompt. `text` is
    the whole prior-history block, already labelled as prior history.
    `images` are attachments from earlier messages, re-downloaded as
    (filename, bytes) so the turn can save them beside its own and the
    agent can still see them. Empty text means the thread had nothing to
    rebuild from.
    """
    text: str
    images: list[tuple[str, bytes]] = field(default_factory=list)


# Called with no arguments; see ReconstructedHistory.
Reconstruct = Callable[[], Awaitable[ReconstructedHistory]]


@dataclass
class TaskReport:
    """The outcome of one turn."""
    answer: str
    pull_requests: list[PullRequestUpdate]
    timed_out: bool


@dataclass
class Conversation:
    """One channel's ongoing work: its branch, session, and pull requests.

    The checkout names the branch and where its worktrees go. Those
    worktrees may or may not be on disk at any moment, and the
    conversation is complete without them.
    """
    checkout: ConversationCheckout
    session_id: str | None = None
    pr_urls: dict[str, str] = field(default_factory=dict)  # repo -> PR url
    # to_state still writes this, and nothing reads it back since
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
        """Rebuild from a persisted entry.

        Every field but the branch is optional, so entries written by
        older versions still load, and so do entries from later versions
        that may have added fields.
        """
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
        # Two messages arriving together in a thread this host has no
        # record of must not recover it twice, which would leave the
        # second turn on a different Conversation (and a different lock)
        # from the first.
        self._recovery_lock = asyncio.Lock()

    def has_conversation(self, key: int) -> bool:
        return key in self.conversations

    def start(self):
        """Load saved conversations and start readying the repos."""
        self._load_state()
        self._ensure_task = asyncio.create_task(self._startup())

    def close(self):
        if self._ensure_task is not None:
            self._ensure_task.cancel()

    async def run(self, key: int, prompt: str,
                  on_progress: OnProgress,
                  images: list[tuple[str, bytes]] = (),
                  reconstruct: Reconstruct | None = None) -> TaskReport:
        """Run one turn of the keyed conversation, creating it if new.

        Turns of the same conversation queue on its lock; different
        conversations run in parallel. The lock covers every decision
        about the turn, including which source its context comes from, so
        a second message arriving during a rebuild waits and then resumes
        the session the first one made (R6.2).

        `reconstruct` builds the conversation's history from its thread
        for a turn that has no transcript to resume (R2c). A conversation
        with no thread to read passes none and starts fresh.
        """
        conversation = self._get_or_create(key, prompt)
        async with conversation.lock:
            conversation.last_active = datetime.now()
            await self._ready()
            # Git continuity (R3). The worktrees are a cache, so a turn
            # that comes after a restart, a sweep, or a lost disk gets
            # them back here, on a branch that starts over if GitHub
            # finished with it and that catches up with its base either
            # way. A catch-up that went wrong is a note for the thread,
            # not a failed turn (R3.7). A checkout that cannot be built at
            # all raises, since there would be nothing for the agent to
            # edit.
            preparation = await conversation.checkout.prepare_for_turn(
                conversation.pr_urls)
            for name in preparation.finished_repos:
                conversation.pr_urls.pop(name, None)
            # The branch and its pull requests may have moved.
            self._save_state()
            # Straight out to the thread, ahead of the answer. These notes
            # are already true, and the turn can still fail on its way to
            # a report the owner would never see (R3.7).
            for note in preparation.notes:
                await on_progress(note)
            # Where this turn's context comes from (R2.2). Choose it
            # inside the lock, so the answer holds for the whole turn.
            resume = (conversation.session_id
                      if self._can_resume(conversation) else None)
            history = (None if resume is not None else
                       await self._history(conversation, reconstruct,
                                           on_progress))
            try:
                result = await self._run_turn(conversation, prompt, images,
                                              history, resume, on_progress)
            except SessionResumeError as error:
                # The transcript was there a moment ago and the SDK could
                # not load it anyway. Say so on the console, then run the
                # same turn again from the thread (R2.4). Nothing has
                # happened yet that a second attempt would repeat.
                print(f'Agent service: resuming session '
                      f'{conversation.session_id} for {key} failed, so this '
                      f'turn falls back to its thread: {error}')
                history = await self._history(conversation, reconstruct,
                                              on_progress)
                result = await self._run_turn(conversation, prompt, images,
                                              history, None, on_progress)
            if result.session_id is not None:
                conversation.session_id = result.session_id
                # Save the moment the id changes. A publish that fails
                # below must not cost the id the next turn resumes from.
                self._save_state()

            # Only a turn with edits needs a name, and the name comes
            # from the owner's own words and the reply, not from a
            # rebuilt history in front of them.
            edited = await conversation.checkout.dirty_repos()
            title = ''
            if edited:
                title = await generate_title(
                    request=prompt, reply=result.final_text,
                    edited_repos=edited,
                    workspace_root=conversation.checkout.root,
                    repos=self.workspace.repos)
            pull_requests = await conversation.checkout.publish_turn(
                prompt, title, result.final_text, conversation.pr_urls)
            for update in pull_requests:
                conversation.pr_urls[update.repo_name] = update.url
            self._save_state()
            return TaskReport(answer=result.final_text,
                              pull_requests=pull_requests,
                              timed_out=result.timed_out)

    async def _run_turn(self, conversation: Conversation, prompt: str,
                        images: list[tuple[str, bytes]],
                        history: ReconstructedHistory | None,
                        resume: str | None,
                        on_progress: OnProgress) -> AgentRunResult:
        """One attempt at the turn, with the context source already chosen.

        A rebuilt history goes in front of the owner's message, labelled
        as what it is, and this turn saves its images beside its own so
        the agent reads them all the same way (R2c.2).
        """
        if history is not None:
            prompt = (f'{history.text}\n\n'
                      f'New message from the owner:\n{prompt}')
            images = [*images, *history.images]
        image_paths = self._save_images(conversation.checkout.root, images)
        return await run_agent(
            prompt=prompt,
            workspace_root=conversation.checkout.root,
            repos=self.workspace.repos,
            on_progress=on_progress,
            resume=resume,
            image_paths=image_paths,
        )

    def _can_resume(self, conversation: Conversation) -> bool:
        """Whether this host still has the transcript to resume from.

        That transcript is the lossless context source of R2.2. Nothing
        copies it off the host, so a host that lost it has only the thread
        left to rebuild from (R2c).
        """
        session_id = conversation.session_id
        if session_id is None:
            return False  # a v0 thread, or a conversation that never ran
        return local_transcript_path(conversation.checkout.root,
                                     session_id).exists()

    async def _history(self, conversation: Conversation,
                       reconstruct: Reconstruct | None,
                       on_progress: OnProgress
                       ) -> ReconstructedHistory | None:
        """Rebuild the conversation's history from its thread (R2c).

        The muted line goes out for anything the owner lost (R4.3): a
        rebuilt history, which is lossy by definition, or a session that
        existed and could not be continued. That second case fires with no
        callback at all. A conversation with no thread to read, a DM say,
        rebuilds nothing but has still lost everything it knew, and saying
        so is the difference between a fresh start and a silent one. A
        brand new conversation has lost nothing and hears nothing.
        """
        history = None
        if reconstruct is not None:
            try:
                history = await reconstruct()
            except Exception as error:
                # Nothing is left to run the turn from, so the turn fails
                # and says why (R4.4).
                raise WorkspaceError(
                    "This conversation's transcript is gone and its history "
                    'could not be rebuilt from the thread: '
                    f'{one_line(error)}') from error
            if not history.text:
                history = None  # nothing in the thread to tell the agent
        if history is not None or conversation.session_id is not None:
            await on_progress(RELOADING_NOTE)
        return history

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
        """Deliberately synchronous.

        With no await between the lookup and the insert, two turns
        arriving together cannot both create one.
        """
        conversation = self.conversations.get(key)
        if conversation is not None:
            return conversation

        checkout = self.workspace.new_checkout(
            self.conversations_root / str(key), prompt)
        conversation = Conversation(checkout=checkout)
        self.conversations[key] = conversation
        self._save_state()
        return conversation

    async def recover(self, key: int, starter_prompt: str,
                      pr_urls: dict[str, str]) -> bool:
        """Put back a conversation this host has no record of (R3.3).

        Two sources, in order. First, what the caller says Discord
        knows: the pull requests the thread announced, most recently
        announced last, plus the message that started the thread. The
        latest of those pull requests gives the branch. Second, for a
        thread that announced none, a search of the agent's pull requests
        on GitHub for one that names this thread. Returns whether anything
        was found. A conversation whose turns never touched code has
        nothing to recover and starts fresh on its next edit.

        GitHub knows the code side and nothing else, so a conversation
        recovered here has no session id, and its next turn rebuilds what
        the agent knew from the thread (R2c).
        """
        if self.has_conversation(key):
            return False
        async with self._recovery_lock:
            # recover() holds the lock across the GitHub round-trip below,
            # so a second caller waits here and finds the conversation on
            # this second look rather than building one of its own.
            if self.has_conversation(key):
                return False
            if pr_urls:
                latest = list(pr_urls.values())[-1]
                branch = await self.workspace.branch_for_pull_request(latest)
            else:
                found = await self.workspace.find_conversation_on_github(
                    starter_prompt)
                if found is None:
                    return False
                branch, pr_urls = found
            if not branch:
                return False

            checkout = ConversationCheckout(
                self.conversations_root / str(key), branch, self.workspace)
            self.conversations[key] = Conversation(checkout=checkout,
                                                   pr_urls=dict(pr_urls))
            self._save_state()
            return True

    async def _startup(self):
        cloned = await self.workspace.ensure_repos()
        if cloned:
            print(f'Agent service: cloned {", ".join(cloned)} '
                  f'into {self.workspace.root}')

    async def _ready(self):
        """Wait for the startup task, and restart it if its repos vanished.

        A task that finished cleanly proves nothing later on. Someone can
        delete a pristine clone long after startup, and then every turn
        has no repo to work in until the bot restarts, which R4.2 says
        must not be necessary. Retrying costs almost nothing when the
        directories are in place, since ensure_repos then does nothing.
        One task at a time, so two turns cannot clone at once.
        """
        task = self._ensure_task
        if task is None or task.cancelled() or (task.done() and (
                task.exception() is not None
                or self.workspace.missing_repos())):
            task = asyncio.create_task(self._startup())
            self._ensure_task = task
        await task

    def _state_path(self) -> Path:
        return self.conversations_root / STATE_FILE

    def _load_state(self):
        path = self._state_path()
        if not path.exists():
            return
        # Load every entry whatever the disk looks like. A later turn
        # rebuilds the checkout on demand, and an identity is the one
        # thing this host cannot recreate on its own.
        for key, entry in json.loads(path.read_text(encoding='utf-8')).items():
            self.conversations[int(key)] = Conversation.from_state(
                entry, self.conversations_root / key, self.workspace)

    def _save_state(self):
        self.conversations_root.mkdir(parents=True, exist_ok=True)
        state = {str(key): conversation.to_state()
                 for key, conversation in self.conversations.items()}
        self._state_path().write_text(json.dumps(state, indent=2),
                                      encoding='utf-8')
