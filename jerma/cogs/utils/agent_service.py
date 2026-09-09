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
identities.

When even state.json is gone, recover() puts an identity back from what
Discord and GitHub still know: the pull requests a thread announced, or a
search of the agent's pull requests for the one that names the thread
(R3.3). The cog supplies the Discord half; everything after it is here.

What the agent remembers of the conversation — the SDK's transcript — is
backed up off this host by agent_backup, which also keeps the identity
record beside it, so recover() asks the backup first and falls back to
Discord and GitHub only when it has nothing (R2b.4, R3.3). Without
JERMABOT_AGENT_BACKUP_REPO the bot still runs, loudly, with transcripts
no more durable than this host.

Every turn therefore begins by deciding where its context comes from
(R2.2), in the spec's order: resume the session when its transcript is
still readable — on this host, or in the backup, which is what a resumed
session is actually rebuilt from once one has been backed up — and
otherwise run a new session with the conversation's history rebuilt from
its Discord thread. The rebuilding is the caller's job (only the cog knows
Discord); this module decides when it is needed, says so in the thread in
the one muted line R4.3 allows, and, when a resume that should have worked
fails at the SDK's door, falls through to the same rebuild inside the same
turn (R2.4). The new session is backed up like any other, so a
conversation is lossless from a rebuilt turn on (R2c.4).
"""
import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .agent_backup import BackupStore, read_transcript
from .agent_config import (
    AGENT_REPOS,
    get_backup_repo,
    get_backup_root,
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
from .agent_workspace import (
    AgentWorkspace,
    ConversationCheckout,
    PullRequestUpdate,
    WorkspaceError,  # noqa: F401 — re-exported for callers
    one_line,
)

STATE_FILE = 'state.json'
# The one thing the owner hears about recovery, in Discord's muted subtext
# style so it reads as harness chatter rather than the agent talking
# (R4.3). Its wording is the spec's, verbatim.
RELOADING_NOTE = '-# _Reloading thread history. Some context might be lost._'
# How long a failed backup clone stands before a turn tries it again. Each
# try is a network clone with no timeout of its own, so during an outage
# turns that never needed the backup should not each pay for one.
BACKUP_RETRY_SECONDS = 60


@dataclass
class ReconstructedHistory:
    """A conversation's history as its thread remembers it (R2c).

    Built by the caller — only the cog knows Discord — and prepended to
    the prompt of a turn that has no transcript to resume. `text` is the
    whole prior-history block, already labelled as prior history; `images`
    are attachments from earlier messages, re-downloaded so the agent can
    still see them, as (filename, bytes) for the turn to save beside its
    own. Empty text means the thread had nothing to rebuild from.
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

    The checkout names the branch and where its worktrees go; they may or
    may not be on disk at any moment, and the conversation is complete
    without them.
    """
    checkout: ConversationCheckout
    session_id: str | None = None
    pr_urls: dict[str, str] = field(default_factory=dict)  # repo -> PR url
    # Whether a turn of this conversation has run with the backup as its
    # session store. Once one has, the SDK stopped updating this host's
    # transcript (see agent_backup), so the backup is the only place the
    # rest of the conversation exists, and a turn without it would quietly
    # resume from a frozen copy.
    backed_up: bool = False
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
            'backed_up': self.backed_up,
            'last_active': self.last_active.isoformat(),
        }

    @classmethod
    def from_state(cls, entry: dict, root: Path, workspace: AgentWorkspace,
                   thread_id: int | None = None) -> 'Conversation':
        """Rebuild from a persisted entry. Every field but the branch is
        optional so that entries written by older versions — and by later
        ones, which may add fields — still load."""
        last_active = entry.get('last_active')
        return cls(
            checkout=ConversationCheckout(root, entry['branch'], workspace,
                                          thread_id),
            session_id=entry.get('session_id'),
            pr_urls=entry.get('pr_urls') or {},
            backed_up=bool(entry.get('backed_up')),
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
        backup_repo = get_backup_repo()
        self.backup = (BackupStore(get_backup_root(), backup_repo,
                                   self.workspace)
                       if backup_repo else None)
        self.conversations: dict[int, Conversation] = {}
        # Backup trouble a turn should mention: a clone that never came
        # up, and an identity record that could not be written.
        self.backup_error: str | None = None
        self._backup_failed_at = 0.0  # time.monotonic() of the last failure
        # Keyed by conversation: turns run in parallel, and a thread must
        # hear about its own record, not another thread's.
        self.identity_error: dict[int, str] = {}
        self._ensure_task: asyncio.Task | None = None
        # Two messages arriving together in a thread this host has no
        # record of must not recover it twice, which would leave the
        # second turn on a different Conversation (and a different lock)
        # from the first.
        self._recovery_lock = asyncio.Lock()

    def has_conversation(self, key: int) -> bool:
        return key in self.conversations

    def start(self):
        """Load conversation state and begin readying repos in the background."""
        self._load_state()
        if self.backup is None:
            print('Agent service: JERMABOT_AGENT_BACKUP_REPO is not set, so '
                  'nothing backs up conversation transcripts off this host. '
                  'A conversation that loses its transcript will fall back '
                  'to rebuilding what it can from its thread.')
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
        conversations run in parallel. Everything about the turn — which
        source its context comes from included — is decided under that
        lock, so a second message arriving during a rebuild waits for it
        and then resumes the session the first one made (R6.2).

        `reconstruct` builds the conversation's history from its thread
        for a turn that has no transcript to resume (R2c); a conversation
        with no thread to read passes none and simply starts fresh.
        """
        conversation = self._get_or_create(key, prompt)
        async with conversation.lock:
            conversation.last_active = datetime.now()
            await self._ready()
            self._require_backup(conversation)
            # Git continuity (R3): the worktrees are a cache, so a turn
            # that comes after a restart, a sweep, or a lost disk gets them
            # back here, on a branch that starts over if GitHub finished
            # with it and that catches up with its base either way. A
            # catch-up that went wrong is a note for the thread, not a
            # failed turn (R3.7); a checkout that cannot be built at all
            # raises, since there would be nothing for the agent to edit.
            preparation = await conversation.checkout.prepare_for_turn(
                conversation.pr_urls)
            for name in preparation.finished_repos:
                conversation.pr_urls.pop(name, None)
            # The branch and its pull requests may have moved.
            await self._persist(key)
            # Straight out to the thread, ahead of the answer: these are
            # already true, and the turn can still fail on its way to a
            # report the owner would never see (R3.7).
            for note in preparation.notes:
                await on_progress(note)
            # A store that cannot be reached is not handed out: the SDK
            # would fail the turn loading from it, while a conversation
            # that never depended on the backup still runs from its local
            # transcript (the ones that do depend on it were refused
            # above by _require_backup).
            store = (self.backup.session_store(key)
                     if self.backup is not None and not self.backup_error
                     else None)
            seeded = False
            if store is not None and not conversation.backed_up:
                # The store is about to become where this conversation
                # lives, so what this host has goes into it first.
                seeded = await self._seed_backup(key, conversation)
                # Set before the run, not after: from the moment the store
                # is handed to the SDK it holds turns this host's
                # transcript will not, and a restart in the middle of the
                # turn must not leave the guard disarmed.
                conversation.backed_up = True
                await self._persist(key)
            # Where this turn's context comes from (R2.2). Decided here,
            # inside the lock, so the answer holds for the whole turn.
            resume = (conversation.session_id
                      if await self._can_resume(key, conversation, store,
                                                seeded)
                      else None)
            history = (None if resume is not None else
                       await self._history(conversation, reconstruct,
                                           on_progress))
            try:
                result = await self._run_turn(conversation, prompt, images,
                                              history, resume, on_progress,
                                              store)
            except SessionResumeError as error:
                # The transcript was there a moment ago and the SDK could
                # not load it anyway. Loudly, and then the same turn runs
                # again from the thread (R2.4) — nothing has happened yet
                # that a second attempt would repeat.
                print(f'Agent service: resuming session '
                      f'{conversation.session_id} for {key} failed, so this '
                      f'turn falls back to its thread: {error}')
                history = await self._history(conversation, reconstruct,
                                              on_progress)
                result = await self._run_turn(conversation, prompt, images,
                                              history, None, on_progress,
                                              store)
            if result.session_id is not None:
                conversation.session_id = result.session_id
                # Persist the moment it changes: a publish that blows up
                # below must not cost the id the next turn resumes from,
                # and the backup's transcript is filed under it.
                await self._persist(key)

            try:
                pull_requests = await conversation.checkout.publish_turn(
                    prompt, result.title, result.body, conversation.pr_urls)
                for update in pull_requests:
                    conversation.pr_urls[update.repo_name] = update.url

                await self._persist(key)
            finally:
                # Last thing in the turn, and said even when publishing the
                # code failed: the backup is what the next turn resumes
                # from, so a copy that never left this host is news for the
                # thread even though the reply stands (R2b.1).
                for note in await self._backup_notes(key, result):
                    await on_progress(note)
            return TaskReport(answer=result.final_text,
                              pull_requests=pull_requests,
                              timed_out=result.timed_out)

    async def _run_turn(self, conversation: Conversation, prompt: str,
                        images: list[tuple[str, bytes]],
                        history: ReconstructedHistory | None,
                        resume: str | None, on_progress: OnProgress,
                        store) -> AgentRunResult:
        """One attempt at the turn, with the context source already chosen.

        A rebuilt history goes in front of the owner's message, labelled
        as what it is, and its images are saved beside this turn's own so
        the agent reads them all the same way (R2c.2).
        """
        request = prompt
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
            session_store=store,
            request=request,
        )

    async def _seed_backup(self, key: int,
                           conversation: Conversation) -> bool:
        """Copy this host's transcript into the backup before the store
        takes the conversation over (R2.2).

        The SDK asks the store first and falls back to this host's disk
        only while the store has nothing for the session; from the moment
        it has, the local copy stops being updated. Without this, the
        first turn after JERMABOT_AGENT_BACKUP_REPO is set would have to
        choose between resuming a copy that is about to freeze and
        throwing the transcript away for a thread rebuild. One append
        makes the store a true continuation instead.

        Fails the turn rather than running on: carrying on would rebuild
        from the thread and lose the transcript for good.

        Returns whether the store is now known to hold this session, so
        the resume decision below does not have to ask the backup a second
        time — the question costs a sync of the whole backup repo. False
        means unknown, not no.
        """
        session_id = conversation.session_id
        if self.backup is None or session_id is None:
            return False
        path = local_transcript_path(conversation.checkout.root, session_id)
        if not path.exists():
            return False  # another host may still have backed it up
        try:
            if await self.backup.has_transcript(key, session_id):
                return True  # the store already has it; nothing to copy
            entries = read_transcript(path)
            if entries:
                await self.backup.append(key, session_id, entries)
                return True
        except Exception as error:
            raise WorkspaceError(
                "This conversation's transcript could not be copied into "
                'the backup, so the turn stopped rather than starting the '
                f'conversation over: {one_line(error)}') from error
        return False

    async def _can_resume(self, key: int, conversation: Conversation,
                          store, seeded: bool = False) -> bool:
        """Whether this conversation's transcript is still there to resume
        from — the lossless sources of R2.2, in order.

        A conversation that has run with the backup as its session store
        resumes from what the store holds: the SDK asks the store first,
        and once it has the session this host's copy stops being updated
        (see agent_backup), so a local file left over from before that is
        not an answer to this question. _seed_backup is what makes sure
        the store does hold it.
        """
        session_id = conversation.session_id
        if session_id is None:
            return False  # a v0 thread, or a conversation that never ran
        if seeded:
            return True  # _seed_backup just established it
        if not conversation.backed_up and local_transcript_path(
                conversation.checkout.root, session_id).exists():
            return True
        if store is None or self.backup is None:
            return False
        try:
            return await self.backup.has_transcript(key, session_id)
        except Exception as error:
            # R2.4: a source that fails is a source that falls through,
            # with its cause on the record.
            print('Agent service: looking for the backed-up transcript of '
                  f'{key} ({session_id}) failed: {error}')
            return False

    async def _history(self, conversation: Conversation,
                       reconstruct: Reconstruct | None,
                       on_progress: OnProgress
                       ) -> ReconstructedHistory | None:
        """The conversation's history rebuilt from its thread, and the one
        muted line that says so (R2c, R4.3).

        The line is posted for anything the owner lost: a rebuilt history,
        which is lossy by definition, or a session that existed and could
        not be continued. That second half fires with no callback at all —
        a conversation with no thread to read, such as a DM, rebuilds
        nothing but has still lost everything it knew, and saying so is
        the difference between a fresh start and a silent one. A
        conversation with neither — a brand new one — has lost nothing and
        hears nothing.
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
        """Deliberately synchronous: with no await between the lookup and
        the insert, two turns arriving together cannot both create one."""
        conversation = self.conversations.get(key)
        if conversation is not None:
            return conversation

        checkout = self.workspace.new_checkout(
            self.conversations_root / str(key), prompt, thread_id=key)
        conversation = Conversation(checkout=checkout)
        self.conversations[key] = conversation
        self._save_state()
        return conversation

    async def recover(self, key: int, starter_prompt: str,
                      pr_urls: dict[str, str]) -> bool:
        """Put back a conversation this host has no record of (R3.3).

        Sources in order: the identity record in the backup repo, which
        knows the session id too and so restores the whole conversation
        rather than only its code (R2b.4); then what the caller says
        Discord knows — the pull requests the thread announced, most
        recently announced last, and the message that started the thread —
        which gives the branch; then, for a thread that announced none, a
        search of the agent's pull requests on GitHub for one that names
        this thread. Returns whether anything was found — a conversation
        whose turns never touched code has nothing to recover and simply
        starts fresh on its next edit.
        """
        if self.has_conversation(key):
            return False
        async with self._recovery_lock:
            # The lock is held across the GitHub round-trip below, so a
            # second caller waits here and finds the conversation on this
            # second look rather than building one of its own.
            if self.has_conversation(key):
                return False
            identity = await self._backup_identity(key)
            if identity is not None:
                # Everything at once, session id included, so the turn
                # resumes the conversation rather than only its branch.
                # The thread's announcements cover repos the record says
                # nothing about, so both are kept; where the two disagree
                # the record wins, and either answer is safe — a url whose
                # branch GitHub no longer has is dropped by
                # prepare_for_turn at the start of the turn anyway (R3.4).
                identity = {**identity,
                            'pr_urls': {**pr_urls,
                                        **(identity.get('pr_urls') or {})}}
                self.conversations[key] = Conversation.from_state(
                    identity, self.conversations_root / str(key),
                    self.workspace, key)
                await self._persist(key)
                return True
            if pr_urls:
                latest = list(pr_urls.values())[-1]
                branch = await self.workspace.branch_for_pull_request(latest)
            else:
                found = await self.workspace.find_conversation_on_github(
                    key, starter_prompt)
                if found is None:
                    return False
                branch, pr_urls = found
            if not branch:
                return False

            checkout = ConversationCheckout(
                self.conversations_root / str(key), branch, self.workspace,
                key)
            self.conversations[key] = Conversation(checkout=checkout,
                                                   pr_urls=dict(pr_urls))
            await self._persist(key)
            return True

    async def _backup_identity(self, key: int) -> dict | None:
        """The identity record the backup repo holds for a thread, if any
        (R2b.4, R3.3 source 1).

        A backup that cannot be read is logged and skipped rather than
        failing recovery: the thread and GitHub are still to be tried
        (R2.4).
        """
        if self.backup is None:
            return None
        try:
            record = await self.backup.read_identity(key)
        except Exception as error:
            print(f'Agent service: reading the backup identity for {key} '
                  f'failed: {error}')
            return None
        if record and record.get('branch'):
            return record
        return None

    async def _backup_notes(self, key: int,
                            result: AgentRunResult) -> list[str]:
        """Muted lines for a turn the backup could not fully store."""
        if self.backup is None:
            return []
        notes = []
        # The store says the same thing better when it has a note of its
        # own for this thread, so only one of the two is posted.
        if result.mirror_errors and key not in self.backup.dropped:
            notes.append('-# _Part of this turn is missing from the '
                         'transcript backup: '
                         f'{"; ".join(result.mirror_errors)}._')
        identity_error = self.identity_error.pop(key, None)
        if identity_error:
            notes.append("-# _This conversation's identity record was not "
                         f'updated: {identity_error}._')
        notes.extend(await self.backup.flush(key))
        return notes

    def _require_backup(self, conversation: Conversation):
        """Refuse a turn whose transcript the backup cannot supply (R2b.1).

        Only conversations that have already run with the store: from
        their first store-backed turn the SDK stopped writing this host's
        transcript, so resuming without the backup would answer from a
        frozen copy and quietly lose everything since. A conversation that
        never had a backup still has its local transcript and runs as it
        always did.

        Deliberately not an R2.4 fall-through: a clone that failed once is
        usually transient, and rebuilding from the thread instead would
        trade a lossless transcript for a lossy summary and strand the
        backup under the old session id.
        """
        if not conversation.backed_up:
            return
        if self.backup is None:
            raise WorkspaceError(
                'This conversation is backed up to GitHub, but '
                'JERMABOT_AGENT_BACKUP_REPO is not set, so its transcript '
                'cannot be read. Set it back to the backup repo and try '
                'again: running without it would answer from a stale copy '
                'of the conversation.')
        if self.backup_error:
            raise WorkspaceError(
                'This conversation is backed up to GitHub, but the backup '
                f'repo could not be cloned: {self.backup_error}')

    async def _startup(self):
        cloned = await self.workspace.ensure_repos()
        if cloned:
            print(f'Agent service: cloned {", ".join(cloned)} '
                  f'into {self.workspace.root}')
        # Same job as the repo clones: get the backup ready before any
        # turn needs it.
        if self.backup is not None:
            try:
                await self.backup.ensure_clone()
                self.backup_error = None
            except Exception as error:
                # Not raised: a conversation with nothing backed up yet has
                # nothing to load and can still run. _require_backup fails
                # the ones that do depend on it (R2b).
                self.backup_error = one_line(error)
                self._backup_failed_at = time.monotonic()
                print('Agent service: the transcript backup repo '
                      f'{self.backup.repo_slug} could not be cloned: '
                      f'{error}')

    async def _ready(self):
        """Wait for the startup task, restarting it when what it readied is
        not, or is no longer, there.

        A task that finished cleanly is not proof of anything: the backup
        clone records its failure instead of raising, and a pristine clone
        can be deleted long after startup. Either would otherwise stand
        until someone restarted the bot — and a stuck backup_error refuses
        every backed-up conversation, which R4.2 says must recover by
        itself. Retrying is nearly free when the directories are in
        place: ensure_repos and ensure_clone then do nothing. A backup
        clone that keeps failing is not free — it is a network clone per
        try — so those are spaced BACKUP_RETRY_SECONDS apart. One task at
        a time, so two turns cannot clone at once.
        """
        task = self._ensure_task
        if task is None or task.cancelled() or (task.done() and (
                task.exception() is not None or self._needs_startup())):
            task = asyncio.create_task(self._startup())
            self._ensure_task = task
        await task

    def _needs_startup(self) -> bool:
        """Whether a finished startup has something left to redo."""
        backup_due = bool(self.backup_error) and (
            time.monotonic() - self._backup_failed_at >= BACKUP_RETRY_SECONDS)
        return backup_due or bool(self.workspace.missing_repos())

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
                entry, self.conversations_root / key, self.workspace,
                int(key))

    async def _persist(self, key: int):
        """Save an identity everywhere it lives: state.json on this host,
        and the backup repo off it (R5.1, R5.2).

        Never raises: this runs in the middle of a turn, and an identity
        that could not be copied off the host is a note for the thread,
        not a lost answer.
        """
        self._save_state()
        conversation = self.conversations.get(key)
        if self.backup is None or conversation is None:
            return
        record = {'thread_id': key, **conversation.to_state()}
        # last_active changes every turn and nothing reads it; leaving it
        # out keeps the record's history down to real changes of identity.
        record.pop('last_active', None)
        try:
            await self.backup.write_identity(key, record)
        except Exception as error:
            self.identity_error[key] = one_line(error)
            print(f'Agent service: backing up the identity for {key} '
                  f'failed: {error}')

    def _save_state(self):
        self.conversations_root.mkdir(parents=True, exist_ok=True)
        state = {str(key): conversation.to_state()
                 for key, conversation in self.conversations.items()}
        self._state_path().write_text(json.dumps(state, indent=2),
                                      encoding='utf-8')
