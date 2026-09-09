"""The off-host transcript and identity backup, against a real git repo.

A local bare repo over file:// stands in for the private GitHub backup
repo, so the plumbing under test is the real thing: cloning a repo that is
still empty, committing before pushing, surviving a push that cannot reach
its origin, two hosts writing to one repo, and — the point of the whole
exercise — a second host with nothing but the origin reading back what the
first one wrote.
"""
import asyncio
import json
import subprocess

import pytest

from cogs.utils.agent_backup import BackupStore
from cogs.utils.agent_workspace import AgentWorkspace, WorkspaceError

THREAD = 42
SESSION = 'd5fe6dd5-a73a-4ac7-952b-f8233f1f5975'
GIT_IDENTITY = ['-c', 'user.name=Test', '-c', 'user.email=test@test.invalid']


def git(cwd, *args) -> str:
    result = subprocess.run(['git', *GIT_IDENTITY, *args], cwd=str(cwd),
                            capture_output=True, text=True)
    assert result.returncode == 0, f'git {args} failed: {result.stderr}'
    return result.stdout


def entry(uuid: str, text: str) -> dict:
    """A transcript line, in the shape the SDK hands to a session store."""
    return {'type': 'assistant', 'uuid': uuid, 'text': text}


def transcript_path(store, thread_id=THREAD, session=SESSION):
    return store.root / str(thread_id) / 'transcript' / f'{session}.jsonl'


@pytest.fixture
def origin(tmp_path):
    """The backup repo as GitHub would first hand it over: empty."""
    bare = tmp_path / 'backup.git'
    git(tmp_path, 'init', '--bare', str(bare))
    return bare


def make_store(tmp_path, origin, name: str) -> BackupStore:
    """A store whose clone lives at tmp_path/name. `name` is what makes a
    second store a second host: same origin, its own empty disk."""
    workspace = AgentWorkspace(root=tmp_path / 'repos', repos={},
                               github_token=None)
    return BackupStore(tmp_path / name, 'local/backup', workspace,
                       clone_url=origin.as_uri())


@pytest.fixture
def store(tmp_path, origin):
    return make_store(tmp_path, origin, 'backup')


async def test_entries_come_back_from_an_empty_backup_repo(store, origin):
    """R2b: nothing exists yet — the clone, the repo's first commit, and
    the thread's directory all come out of the first append."""
    entries = [entry('u1', 'hello'), entry('u2', 'world')]

    await store.append(THREAD, SESSION, entries)

    assert store.error is None
    assert await store.load(THREAD, SESSION) == entries
    # ...and it really went to the origin, not just the local clone.
    assert git(origin, 'ls-remote', '.').strip()
    assert git(origin, 'ls-tree', '-r', '--name-only', 'HEAD').split() == [
        f'{THREAD}/transcript/{SESSION}.jsonl']


async def test_a_fresh_host_restores_the_transcript(tmp_path, origin, store):
    """Acceptance scenario 2: the host is gone, and everything the agent
    remembers has to come back from GitHub alone."""
    entries = [entry('u1', 'hello'), entry('u2', 'world')]
    await store.append(THREAD, SESSION, entries)

    elsewhere = make_store(tmp_path, origin, 'other-host')

    assert await elsewhere.load(THREAD, SESSION) == entries


async def test_an_unknown_session_has_no_backup(store):
    assert await store.load(THREAD, SESSION) is None
    assert await store.read_identity(THREAD) is None


async def test_nothing_to_report_before_anything_is_cloned(store):
    """A turn can end before the store was ever touched (a conversation
    that made no transcript, or a failure on the way in)."""
    assert await store.flush(THREAD) == []
    assert not (store.root / '.git').exists()


async def test_a_retried_batch_is_stored_once(store):
    """The SDK retries a failed batch with the same entries, so uuid is an
    idempotency key within the file."""
    entries = [entry('u1', 'hello')]
    await store.append(THREAD, SESSION, entries)
    await store.append(THREAD, SESSION, entries + [entry('u2', 'world')])

    assert await store.load(THREAD, SESSION) == [entry('u1', 'hello'),
                                                 entry('u2', 'world')]


async def test_two_conversations_do_not_share_a_file(store):
    await store.append(THREAD, SESSION, [entry('u1', 'mine')])
    await store.append(7, SESSION, [entry('u2', 'theirs')])

    assert await store.load(THREAD, SESSION) == [entry('u1', 'mine')]
    assert await store.load(7, SESSION) == [entry('u2', 'theirs')]


async def test_conversations_can_append_at_the_same_time(store):
    """Turns of different conversations run in parallel, and they share one
    clone and one working tree."""
    await asyncio.gather(*(store.append(thread, SESSION,
                                        [entry(f'u{thread}', 'hello')])
                           for thread in range(1, 6)))

    for thread in range(1, 6):
        assert await store.load(thread, SESSION) == [
            entry(f'u{thread}', 'hello')]
    assert store.error is None
    assert git(store.root, 'status', '--porcelain').strip() == ''


async def test_an_unreadable_line_does_not_cost_the_conversation(store):
    """A crash can leave half a line behind. Losing it costs one entry;
    refusing the file would cost every turn that came before it."""
    await store.append(THREAD, SESSION, [entry('u1', 'hello')])
    with transcript_path(store).open('a', encoding='utf-8',
                                     newline='\n') as handle:
        handle.write('{"type": "assistant", "uuid": "u2", "te')

    assert await store.load(THREAD, SESSION) == [entry('u1', 'hello')]

    # ...and the next append is still filed after it, not lost with it.
    await store.append(THREAD, SESSION, [entry('u2', 'world')])
    assert await store.load(THREAD, SESSION) == [entry('u1', 'hello'),
                                                 entry('u2', 'world')]


async def test_a_push_failure_keeps_the_entries_and_is_reported(
        tmp_path, origin, store):
    """R2b.1: append must not raise — the SDK answers an exception by
    dropping the batch, and after a store-backed resume that batch is the
    only copy of the turn. So it lands locally, and the failure is a note
    for the thread instead."""
    await store.ensure_clone()
    git(store.root, 'remote', 'set-url', 'origin',
        (tmp_path / 'not-a-repo.git').as_uri())

    await store.append(THREAD, SESSION, [entry('u1', 'hello')])

    assert store.error  # ...naming the cause
    assert [note for note in await store.flush(THREAD)
            if 'not pushed to GitHub' in note]
    # The entry is on this host, committed, whatever GitHub thinks.
    assert await store.load(THREAD, SESSION) == [entry('u1', 'hello')]
    assert git(store.root, 'status', '--porcelain').strip() == ''
    assert not git(origin, 'ls-remote', '.').strip()

    # The next append gets everything through, not just its own entry.
    git(store.root, 'remote', 'set-url', 'origin', origin.as_uri())
    await store.append(THREAD, SESSION, [entry('u2', 'world')])

    assert store.error is None
    elsewhere = make_store(tmp_path, origin, 'other-host')
    assert await elsewhere.load(THREAD, SESSION) == [entry('u1', 'hello'),
                                                     entry('u2', 'world')]


async def test_an_entry_that_reaches_nothing_is_reported(store, monkeypatch):
    """A local write that fails stores the batch nowhere, and the SDK's
    own report of it goes to a stream nobody reads by the end of a turn."""
    async def no_clone():
        raise WorkspaceError('the disk is gone')

    monkeypatch.setattr(store, '_ensure_clone', no_clone)

    with pytest.raises(WorkspaceError):
        await store.append(THREAD, SESSION, [entry('u1', 'hello')])

    # Turns run in parallel: another conversation ending must not swallow
    # this thread's news, and must not be told about it either.
    assert await store.flush(7) == []

    assert [note for note in await store.flush(THREAD)
            if 'never reached the transcript backup' in note]
    # Said once: a later success does not undo a lost turn, but nor should
    # every turn after it repeat the news.
    assert await store.flush(THREAD) == []


async def test_a_retry_that_gets_through_is_not_reported(store, monkeypatch):
    """The SDK tries a batch three times; a first failure that the second
    attempt makes good is not a loss to report."""
    calls = []
    real = store._ensure_clone

    async def failing_once():
        calls.append(1)
        if len(calls) == 1:
            raise WorkspaceError('the disk is busy')
        await real()

    monkeypatch.setattr(store, '_ensure_clone', failing_once)

    with pytest.raises(WorkspaceError):
        await store.append(THREAD, SESSION, [entry('u1', 'hello')])
    await store.append(THREAD, SESSION, [entry('u1', 'hello')])

    assert await store.flush(THREAD) == []
    assert await store.load(THREAD, SESSION) == [entry('u1', 'hello')]


async def test_two_hosts_share_one_backup_repo(tmp_path, origin, store):
    """A dev machine and the server can both back up to one repo. Each
    conversation writes its own files, so a host that pushed second
    replays its commits on top rather than wedging on a rejection."""
    other = make_store(tmp_path, origin, 'other-host')
    await store.append(THREAD, SESSION, [entry('u1', 'from the server')])
    await other.ensure_clone()  # cloned while the server was ahead

    # Both write while neither can see the other's newest commit.
    await store.append(THREAD, SESSION, [entry('u2', 'server again')])
    await other.append(7, SESSION, [entry('u3', 'from the laptop')])

    assert other.error is None  # the rejected push resynced and retried
    assert await other.load(7, SESSION) == [entry('u3', 'from the laptop')]
    assert await other.load(THREAD, SESSION) == [entry('u1', 'from the server'),
                                                 entry('u2', 'server again')]
    # And the server sees the laptop's thread once it looks.
    assert await store.load(7, SESSION) == [entry('u3', 'from the laptop')]


async def test_two_hosts_writing_one_conversation_converge(
        tmp_path, origin, store):
    """The same thread from two hosts — a conversation moved between the
    laptop and the server — puts both hosts' commits on the same file.
    The rebase conflicts, and healing it must keep every entry rather than
    leave the clone diverged and every later push rejected."""
    other = make_store(tmp_path, origin, 'other-host')
    await store.append(THREAD, SESSION, [entry('u1', 'first')])
    await other.ensure_clone()

    await store.append(THREAD, SESSION, [entry('u2', 'from the server')])
    await other.append(THREAD, SESSION, [entry('u3', 'from the laptop')])
    # ...and again, in the other order, on top of the healed history.
    await other.append(THREAD, SESSION, [entry('u4', 'laptop again')])
    await store.append(THREAD, SESSION, [entry('u5', 'server again')])

    assert other.error is None
    assert store.error is None
    everything = {'u1', 'u2', 'u3', 'u4', 'u5'}
    for host in (store, other):
        entries = await host.load(THREAD, SESSION)
        uuids = [item['uuid'] for item in entries]
        assert len(uuids) == len(set(uuids))  # no duplicates
        assert set(uuids) == everything


async def test_two_hosts_writing_one_identity_keep_the_newer_record(
        tmp_path, origin, store):
    """identity.json is whole-file, so the host still writing to the
    thread wins; the point is that neither clone stays wedged."""
    other = make_store(tmp_path, origin, 'other-host')
    await store.write_identity(THREAD, {'branch': 'one'})
    await other.ensure_clone()

    await store.write_identity(THREAD, {'branch': 'two'})
    await other.write_identity(THREAD, {'branch': 'three'})

    assert other.error is None
    assert await other.read_identity(THREAD) == {'branch': 'three'}
    assert await store.read_identity(THREAD) == {'branch': 'three'}


async def test_a_sync_keeps_commits_that_never_reached_github(
        tmp_path, origin, store):
    """The local clone is the copy of last resort: taking in someone
    else's work must never throw away a turn this host could not push."""
    other = make_store(tmp_path, origin, 'other-host')
    await other.append(7, SESSION, [entry('u1', 'theirs')])

    await store.ensure_clone()
    git(store.root, 'remote', 'set-url', 'origin',
        (tmp_path / 'not-a-repo.git').as_uri())
    await store.append(THREAD, SESSION, [entry('u2', 'mine')])
    assert store.error

    git(store.root, 'remote', 'set-url', 'origin', origin.as_uri())

    assert await store.load(7, SESSION) == [entry('u1', 'theirs')]
    assert await store.load(THREAD, SESSION) == [entry('u2', 'mine')]


async def test_identity_round_trips_through_github(tmp_path, origin, store):
    """R5.2: the record that makes a thread findable again outlives the
    host that wrote it."""
    record = {'thread_id': THREAD, 'branch': 'jermabot/thing-20260101-000000',
              'session_id': SESSION,
              'pr_urls': {'jermabot': 'https://github.com/x/y/pull/1'}}

    await store.write_identity(THREAD, record)

    assert await store.read_identity(THREAD) == record
    elsewhere = make_store(tmp_path, origin, 'other-host')
    assert await elsewhere.read_identity(THREAD) == record


async def test_a_changed_identity_is_committed_again(store):
    await store.write_identity(THREAD, {'branch': 'one'})
    commits = git(store.root, 'rev-list', '--count', 'HEAD')

    await store.write_identity(THREAD, {'branch': 'one'})  # nothing changed
    assert git(store.root, 'rev-list', '--count', 'HEAD') == commits

    await store.write_identity(THREAD, {'branch': 'two'})
    assert await store.read_identity(THREAD) == {'branch': 'two'}
    assert git(store.root, 'rev-list', '--count', 'HEAD') != commits


async def test_a_non_empty_directory_is_not_overwritten(tmp_path, store):
    """The backup root is configured by hand; something else living there
    is a mistake to report, not files to delete."""
    store.root.mkdir(parents=True)
    (store.root / 'notes.txt').write_text('mine', encoding='utf-8')

    with pytest.raises(WorkspaceError, match='not empty'):
        await store.ensure_clone()

    assert (store.root / 'notes.txt').exists()


async def test_the_sdk_adapter_scopes_a_key_to_its_conversation(store):
    """The SDK derives project_key from the checkout path, so the adapter
    ignores it and files everything under the thread it was built for."""
    adapter = store.session_store(THREAD)
    key = {'project_key': 'C--wherever-the-checkout-happens-to-be',
           'session_id': SESSION}

    await adapter.append(key, [entry('u1', 'hello')])

    assert await adapter.load(key) == [entry('u1', 'hello')]
    assert await store.load(THREAD, SESSION) == [entry('u1', 'hello')]
    lines = transcript_path(store).read_text(encoding='utf-8').splitlines()
    assert [json.loads(line) for line in lines] == [entry('u1', 'hello')]
