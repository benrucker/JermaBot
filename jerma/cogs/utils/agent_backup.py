"""Off-host backup for what a conversation remembers (R2b, R5.2).

Everything else about a conversation can be rebuilt from GitHub and
Discord; its transcript — the agent's own view of every turn, including
the tool calls and file contents nobody posted in the thread — exists only
where the SDK wrote it. This module keeps a second copy in a private
GitHub repo (`JERMABOT_AGENT_BACKUP_REPO`), through a local clone under
`get_backup_root()`, alongside the conversation's identity record so a
wiped host can find its way back to a thread's branch, pull requests, and
session id.

Layout inside the backup repo, keyed by thread id because that is the one
identifier Discord guarantees us:

    <thread_id>/transcript/<session_id>.jsonl
    <thread_id>/identity.json

The transcript file is the SDK's own JSONL, entry per line, handed to us
by `ClaudeAgentOptions.session_store`; `uuid` is the idempotency key
within a file, since the SDK retries a failed batch with the same entries.
Subagent transcripts are not supported (the agent has no Task tool, and
list_subkeys is deliberately unimplemented).

Why this store must not lose a batch: measured against SDK 0.2.110, the
first resume that passes a session_store makes the store authoritative.
`ClaudeSDKClient.connect()` always calls `store.load()` and materializes
the result into a temporary CLAUDE_CONFIG_DIR, which it deletes on exit —
from then on ~/.claude/projects stops receiving that session's turns. A
dropped append is therefore a permanently lost turn, not a stale mirror,
whatever the SDK's docstrings say. So `append` never raises for anything
recoverable: the entries are written into the local clone and committed
there (durable on this host) before the push is attempted, and a push that
fails is recorded and retried on the next append, identity write, or turn
end rather than reported back to the SDK, which would drop the batch.
`flush()` says at the end of a turn what is still wrong.

Two hosts may share one backup repo (a dev machine and the server), so
divergence is normal: the local commits are rebased onto whatever GitHub
has. When two hosts wrote the same file the rebase conflicts, and the
answer is never to leave the clone diverged — that wedges every later
push. Instead the conflict is healed by taking GitHub's copy and putting
this host's writing back on top of it: transcripts are unioned by uuid
(nobody's turns are lost, in either direction), identity records are
whole-file, so the local one wins. Writes hold `_lock`; talking to GitHub
holds `_push_lock` instead, so a slow fetch or push for one conversation
cannot stall another's append — the SDK gives an append 60 seconds before
it gives up on the batch for good.
"""
import asyncio
import json
import shutil
from pathlib import Path

from .agent_config import AGENT_COMMIT_EMAIL, AGENT_COMMIT_NAME
from .agent_workspace import AgentWorkspace, WorkspaceError, _run_git, one_line

IDENTITY_FILE = 'identity.json'
# Rebasing and healing both write commits, which need an author.
_COMMIT_AS = ('-c', f'user.name={AGENT_COMMIT_NAME}',
              '-c', f'user.email={AGENT_COMMIT_EMAIL}')


def _entry_key(entry: dict) -> str:
    """What makes an entry the same entry as another.

    The SDK stamps every transcript entry with a uuid and retries a failed
    batch unchanged, so that is the idempotency key; the whole entry
    stands in for one that somehow has no uuid, so two byte-identical
    uuid-less entries count as one.
    """
    return entry.get('uuid') or json.dumps(entry, sort_keys=True)


def _parse_entries(text: str, source) -> list[dict]:
    """Transcript lines as entries, skipping anything unreadable.

    A line can be half-written: a crash between the write and the commit
    leaves one, and the SDK's retry then appends the batch again after it.
    Dropping that line costs one entry; refusing the file would cost the
    whole conversation.
    """
    entries = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as error:
            print(f'Agent backup: skipping an unreadable line in {source}: '
                  f'{error}')
    return entries


def _entries(path: Path) -> list[dict]:
    """A transcript file's entries, or none if it isn't there."""
    if not path.exists():
        return []
    return _parse_entries(path.read_text(encoding='utf-8'), path)


def _write_entries(path: Path, entries: list[dict]):
    # newline='\n': the file is git-tracked JSONL, the same on both hosts.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + '\n')


def _union(theirs: list[dict], ours: list[dict]) -> list[dict]:
    """Their entries, then ours that they haven't got. Order within a
    conversation is the order the SDK wrote it in, and a resume replays
    the file as it stands, so keeping both hosts' turns is what matters."""
    known = {_entry_key(entry) for entry in theirs}
    return theirs + [entry for entry in ours
                     if _entry_key(entry) not in known]


class BackupStore:
    """The transcript and identity backup, as a git repo on GitHub.

    One instance serves every conversation; `session_store(thread_id)`
    hands the SDK an adapter scoped to one of them.
    """

    def __init__(self, root: Path, repo_slug: str, workspace: AgentWorkspace,
                 clone_url: str | None = None):
        self.root = root
        self.repo_slug = repo_slug
        self.workspace = workspace
        self.clone_url = clone_url or f'https://github.com/{repo_slug}.git'
        # What is wrong, if anything, as short lines for the thread.
        # error and sync_error are about the one shared clone; dropped is
        # per conversation, since turns of different ones run at the same
        # time and each thread must hear about its own losses only.
        self.error: str | None = None       # commits still only on this host
        self.sync_error: str | None = None  # GitHub could not be taken in
        self.dropped: dict[int, str] = {}   # thread -> why nothing was stored
        self._dropped_keys: dict[int, set[str]] = {}  # thread -> lost entries
        self._lock = asyncio.Lock()       # the clone and its working tree
        self._push_lock = asyncio.Lock()  # talking to GitHub
        self._branch = ''

    # --- what the service and the SDK call ----------------------------

    def session_store(self, thread_id: int) -> 'ConversationSessionStore':
        """The SessionStore to hand ClaudeAgentOptions for a conversation."""
        return ConversationSessionStore(self, thread_id)

    async def ensure_clone(self):
        """Clone the backup repo if this host hasn't got it yet.

        Called at startup with the repo clones; failures are the owner's
        to hear about, so this one does raise.
        """
        async with self._lock:
            await self._ensure_clone()

    async def append(self, thread_id: int, session_id: str,
                     entries: list[dict]):
        """Add transcript entries, committing before pushing (see module
        docstring: losing these loses a turn for good)."""
        try:
            async with self._lock:
                await self._ensure_clone()
                path = self._transcript_path(thread_id, session_id)
                seen = {_entry_key(entry) for entry in _entries(path)}
                new = [entry for entry in entries
                       if _entry_key(entry) not in seen]
                if new:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('a', encoding='utf-8',
                                   newline='\n') as handle:
                        if handle.tell() and not path.read_text(
                                encoding='utf-8').endswith('\n'):
                            # A torn last line (a crash mid-write) would
                            # otherwise swallow the first new entry too.
                            handle.write('\n')
                        for entry in new:
                            handle.write(json.dumps(entry) + '\n')
                    await self._commit(f'{thread_id}: transcript {session_id}')
        except Exception as error:
            # Nothing was stored anywhere. Raising gives the SDK a retry
            # and, when it gives up, a MirrorErrorMessage; the note below
            # covers the close-time flush, whose error reaches a stream
            # nobody is reading by then.
            self.dropped[thread_id] = one_line(error)
            self._dropped_keys.setdefault(thread_id, set()).update(
                _entry_key(entry) for entry in entries)
            raise
        self._stored(thread_id, entries)
        await self._publish()

    async def load(self, thread_id: int, session_id: str) -> list[dict] | None:
        """This conversation's backed-up transcript, or None if there is
        none. Syncs from GitHub first, so a fresh host sees it."""
        await self._ready()
        await self._sync()
        async with self._lock:
            return _entries(self._transcript_path(thread_id, session_id)) or None

    async def write_identity(self, thread_id: int, record: dict):
        """Store a conversation's identity record (R5.2)."""
        async with self._lock:
            await self._ensure_clone()
            path = self.root / str(thread_id) / IDENTITY_FILE
            text = json.dumps(record, indent=2, sort_keys=True) + '\n'
            if not path.exists() or path.read_text(encoding='utf-8') != text:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8', newline='\n')
                await self._commit(f'{thread_id}: identity')
        await self._publish()

    async def read_identity(self, thread_id: int) -> dict | None:
        """A conversation's identity record as GitHub has it, or None."""
        await self._ready()
        await self._sync()
        async with self._lock:
            path = self.root / str(thread_id) / IDENTITY_FILE
            if not path.exists():
                return None
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except json.JSONDecodeError as error:
                print(f'Agent backup: {path} is not readable JSON: {error}')
                return None

    async def flush(self, thread_id: int | None = None) -> list[str]:
        """Publish anything still local, and say what is still wrong.

        Returns finished Discord subtext lines in the muted R4.3 style,
        for one conversation's thread to show as-is; an empty list means
        the backup is whole and on GitHub.
        """
        await self._publish()
        notes = []
        dropped = self.dropped.pop(thread_id, None)
        self._dropped_keys.pop(thread_id, None)
        if dropped:
            # Said once: no later success undoes a lost turn, but nor
            # should every turn after it repeat the news.
            notes.append('-# _Some of this turn never reached the transcript '
                         f'backup: {dropped}._')
        if self.error:
            notes.append(f'-# _Backup not pushed to GitHub: {self.error}._')
        if self.sync_error:
            notes.append(f'-# _{self.sync_error}._')
        return notes

    # --- git plumbing --------------------------------------------------

    def _stored(self, thread_id: int, entries: list[dict]):
        """Take a batch off the lost list — the SDK's retry got it in."""
        lost = self._dropped_keys.get(thread_id)
        if not lost:
            return
        lost.difference_update(_entry_key(entry) for entry in entries)
        if not lost:
            del self._dropped_keys[thread_id]
            self.dropped.pop(thread_id, None)

    def _transcript_path(self, thread_id: int, session_id: str) -> Path:
        return self.root / str(thread_id) / 'transcript' / f'{session_id}.jsonl'

    async def _ready(self):
        async with self._lock:
            await self._ensure_clone()

    async def _ensure_clone(self):
        """Clone on first use. The repo may be brand new and empty, which
        clones fine — an unborn HEAD that the first commit gives a tip."""
        if (self.root / '.git').exists():
            if not self._branch:
                self._branch = await self._current_branch()
            return
        if self.root.exists() and any(self.root.iterdir()):
            raise WorkspaceError(
                f'{self.root} is not a clone of {self.repo_slug} but is not '
                'empty either. Move it aside (or point '
                'JERMABOT_AGENT_BACKUP_DIR somewhere else) so the backup '
                'repo can be cloned there.')
        self.root.parent.mkdir(parents=True, exist_ok=True)
        partial = self.root.parent / f'{self.root.name}.cloning'
        if partial.exists():  # wreckage from a crash mid-clone
            await asyncio.to_thread(shutil.rmtree, partial)
        await self.workspace.run_git_authed(
            self.root.parent, 'clone', self.clone_url, str(partial))
        if self.root.exists():
            self.root.rmdir()  # empty, checked above
        partial.rename(self.root)
        self._branch = await self._current_branch()

    async def _current_branch(self) -> str:
        """The checked-out branch — readable even before the first commit."""
        out = await _run_git(self.root, 'symbolic-ref', '--short', 'HEAD')
        return out.strip()

    async def _commit(self, message: str):
        await _run_git(self.root, 'add', '-A')
        status = await _run_git(self.root, 'status', '--porcelain')
        if not status.strip():
            return
        await _run_git(self.root, *_COMMIT_AS, 'commit', '-m', message)

    async def _publish(self):
        """Push local commits. Never raises: the commit already made the
        entries durable on this host, and the caller may be the SDK, which
        answers an exception by dropping the batch.

        Holds the network lock rather than the write lock, so a slow push
        does not hold up another conversation's append or load.
        """
        async with self._push_lock:
            if not (self.root / '.git').exists() or not self._branch:
                return  # nothing cloned yet, so nothing to push
            try:
                if not await self._needs_push():
                    self.error = None
                    return
                try:
                    await self._push()
                except Exception:
                    # Rejected: the other host pushed first. Take their
                    # commits under ours and try once more.
                    await self._fetch()
                    async with self._lock:
                        await self._integrate()
                    await self._push()
                self.error = None
            except Exception as error:
                self.error = one_line(error)

    async def _push(self):
        await self.workspace.run_git_authed(
            self.root, 'push', 'origin', f'HEAD:refs/heads/{self._branch}')

    async def _needs_push(self) -> bool:
        head = await self._rev('HEAD')
        if not head:
            return False  # nothing committed yet
        return head != await self._rev(f'refs/remotes/origin/{self._branch}')

    async def _rev(self, ref: str) -> str:
        try:
            out = await _run_git(self.root, 'rev-parse', '-q',
                                 '--verify', ref)
        except Exception:
            return ''
        return out.strip()

    async def _sync(self):
        """Take in whatever GitHub has that this clone does not.

        The fetch is network and goes under the network lock, so a slow
        one cannot make another conversation's append time out (60s, and
        the SDK does not retry a timeout); only the replay that follows
        needs the working tree.
        """
        async with self._push_lock:
            if not await self._fetch():
                return
        async with self._lock:
            await self._integrate()

    async def _fetch(self) -> bool:
        try:
            await self.workspace.run_git_authed(self.root, 'fetch', 'origin')
        except Exception as error:
            self.sync_error = ('The backup could not be read from GitHub: '
                               f'{one_line(error)}')
            return False
        return True

    async def _integrate(self):
        """Replay this host's unpushed commits on top of GitHub's.

        A failure is recorded, not raised: the local clone is this host's
        own writing and remains the best answer it has.
        """
        remote = await self._rev(f'refs/remotes/origin/{self._branch}')
        if not remote:
            self.sync_error = None
            return  # the backup repo is still empty
        head = await self._rev('HEAD')
        if head == remote:
            self.sync_error = None
            return
        try:
            if not head:
                await _run_git(self.root, 'reset', '--hard', remote)
            else:
                # A no-op when we are ahead, a fast-forward when behind.
                await _run_git(self.root, *_COMMIT_AS, 'rebase', remote)
            self.sync_error = None
        except Exception as error:
            # Both hosts wrote the same file. Leaving the clone diverged
            # would wedge every future push, so heal it instead.
            try:
                await self._heal(remote)
                self.sync_error = None
            except Exception as healing:
                self.sync_error = (
                    "The backup could not be merged with GitHub's copy: "
                    f'{one_line(error)} ({one_line(healing)})')

    async def _heal(self, remote: str):
        """Rebuild this host's unpushed work on top of GitHub's copy.

        Takes the files our commits touched, resets to what GitHub has,
        and writes ours back over it: transcripts as the union of both
        sides by entry (the other host's turns are as real as ours), and
        anything else — an identity record — as our whole file, which is
        the more recent of the two by definition, since we are the ones
        still writing to this thread.
        """
        try:
            await _run_git(self.root, 'rebase', '--abort')
        except WorkspaceError:
            pass  # The rebase never started; the rest works regardless.
        # Entries written but not yet committed would be lost to the
        # reset below without this.
        await self._commit('Save entries before merging')
        names = await _run_git(self.root, 'diff', '--name-only', '-z',
                               f'{remote}...HEAD')
        mine = {}
        for name in names.split('\0'):
            if not name:
                continue
            path = self.root / name
            if path.exists():
                mine[name] = path.read_text(encoding='utf-8')
        await _run_git(self.root, 'reset', '--hard', remote)
        for name, text in mine.items():
            path = self.root / name
            if name.endswith('.jsonl'):
                _write_entries(path, _union(_entries(path),
                                            _parse_entries(text, path)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8', newline='\n')
        await self._commit("Merge this host's writing with GitHub's copy")


class ConversationSessionStore:
    """One conversation's view of the backup, as the SDK's SessionStore.

    The SDK derives `key['project_key']` from the checkout path and offers
    no way to set it, so the scope that matters — which conversation this
    is — is baked in here and the project_key ignored. `subpath` is
    ignored too: it only ever names a subagent transcript, and the agent
    has no Task tool.
    """

    def __init__(self, backup: BackupStore, thread_id: int):
        self._backup = backup
        self._thread_id = thread_id

    async def append(self, key, entries: list[dict]) -> None:
        await self._backup.append(self._thread_id, key['session_id'],
                                  list(entries))

    async def load(self, key) -> list[dict] | None:
        return await self._backup.load(self._thread_id, key['session_id'])
