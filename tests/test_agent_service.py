"""State loading, recovery, and what one turn does to a conversation.

An identity outlives its checkout directory — and, with recover(), the
state file too, since the backup repo holds a copy of it off this host.
The backup itself is exercised in test_agent_backup; here it is a stub,
so what these tests check is that the service writes to it on every
change and asks it before it asks GitHub.
"""
import asyncio
import json

import pytest

from cogs.utils.agent_runner import AgentRunResult
from cogs.utils.agent_service import AgentTaskService, Conversation
from cogs.utils.agent_workspace import TurnPreparation, WorkspaceError

BRANCH = 'jermabot/fix-the-thing-20260816-101500'
PR_URL = 'https://github.com/x/y/pull/1'
OTHER_PR = 'https://github.com/x/z/pull/2'
STARTER = 'fix the thing'


def write_state(conversations_root, state: dict):
    conversations_root.mkdir(parents=True, exist_ok=True)
    (conversations_root / 'state.json').write_text(json.dumps(state),
                                                   encoding='utf-8')


def test_entry_survives_a_missing_checkout(agent_dirs):
    """The v0 loader dropped these; v1 exists so that it doesn't."""
    _, conversations = agent_dirs
    write_state(conversations, {
        '123': {
            'branch': BRANCH,
            'session_id': 'abc-123',
            'pr_urls': {'jermabot': PR_URL},
            'last_active': '2026-08-16T10:15:00',
        },
    })

    service = AgentTaskService()
    service._load_state()

    assert service.has_conversation(123)
    conversation = service.conversations[123]
    assert conversation.checkout.branch == BRANCH
    assert conversation.session_id == 'abc-123'
    assert conversation.pr_urls == {'jermabot': PR_URL}
    # The checkout is named but not on disk, and that is fine.
    assert not conversation.checkout.root.exists()
    assert not conversation.checkout.is_materialized()


def test_v0_entries_load_unchanged(agent_dirs):
    """A record written before this feature must still load, as-is."""
    _, conversations = agent_dirs
    v0 = {
        '999': {
            'branch': 'jermabot/old-thread-20260816-120000',
            'session_id': None,
            'pr_urls': {},
            'last_active': '2026-08-16T12:00:00',
        },
    }
    write_state(conversations, v0)

    service = AgentTaskService()
    service._load_state()
    conversation = service.conversations[999]

    assert conversation.session_id is None
    assert conversation.pr_urls == {}
    assert conversation.last_active.isoformat() == '2026-08-16T12:00:00'
    # ...and round-trips to the same shape but for fields added since,
    # which an older build ignores.
    assert conversation.to_state() == {**v0['999'], 'backed_up': False}


def test_unknown_and_missing_fields_are_tolerated(agent_dirs):
    """Only the branch is required: later versions may add fields, and a
    half-written v0 entry should not take the table down with it."""
    _, conversations = agent_dirs
    write_state(conversations, {
        '7': {'branch': 'jermabot/minimal-20260101-000000', 'future': 'x'},
    })

    service = AgentTaskService()
    service._load_state()

    conversation = service.conversations[7]
    assert conversation.session_id is None
    assert conversation.pr_urls == {}


def test_state_round_trips_through_save(agent_dirs):
    _, conversations = agent_dirs
    service = AgentTaskService()
    checkout = service.workspace.new_checkout(conversations / '42',
                                              'fix the thing')
    service.conversations[42] = Conversation(checkout=checkout,
                                             session_id='sess')
    service._save_state()

    reloaded = AgentTaskService()
    reloaded._load_state()

    assert reloaded.conversations[42].checkout.branch == checkout.branch
    assert reloaded.conversations[42].session_id == 'sess'


def test_new_checkout_names_a_branch_without_touching_disk(agent_dirs):
    _, conversations = agent_dirs
    service = AgentTaskService()

    checkout = service.workspace.new_checkout(conversations / '42',
                                              'Fix the Thing!')

    assert checkout.branch.startswith('jermabot/fix-the-thing-')
    assert not checkout.root.exists()


# --- one turn ----------------------------------------------------------


class FakeCheckout:
    """A checkout that answers the two questions run() asks of it."""

    def __init__(self, root, notes=(), finished=()):
        self.root = root
        self.branch = BRANCH
        self.thread_id = None
        self.preparation = TurnPreparation(notes=list(notes),
                                           finished_repos=list(finished))
        self.published: list[dict] = []
        self.publish_error: Exception | None = None

    async def prepare_for_turn(self, pr_urls):
        return self.preparation

    async def publish_turn(self, prompt, title, body, pr_urls):
        self.published.append(dict(pr_urls))
        if self.publish_error is not None:
            raise self.publish_error
        return []


class FakeBackup:
    """The backup store as the service uses it: identity in, identity out,
    a session store per conversation, and a push that may be failing."""

    def __init__(self, identities: dict | None = None):
        self.identities = dict(identities or {})
        self.notes: list[str] = []
        self.stores: list[int] = []
        self.flushed: list[int] = []
        self.dropped: dict[int, str] = {}
        self.repo_slug = 'local/backup'

    def session_store(self, thread_id: int):
        self.stores.append(thread_id)
        return f'store-{thread_id}'

    async def write_identity(self, thread_id: int, record: dict):
        self.identities[thread_id] = record

    async def read_identity(self, thread_id: int):
        return self.identities.get(thread_id)

    async def flush(self, thread_id: int | None = None):
        self.flushed.append(thread_id)
        return list(self.notes)


@pytest.fixture
def one_turn(agent_dirs, monkeypatch):
    """A service whose turns run no agent and touch no git."""
    _, conversations = agent_dirs
    service = AgentTaskService()
    service.agent_calls = []
    service.agent_result = AgentRunResult(final_text='done', timed_out=False,
                                          session_id='sess')

    async def ready():
        return None

    async def fake_run_agent(**kwargs):
        service.agent_calls.append(kwargs)
        return service.agent_result

    monkeypatch.setattr(service, '_ready', ready)
    monkeypatch.setattr('cogs.utils.agent_service.run_agent', fake_run_agent)
    return service, conversations


async def test_a_finished_repo_forgets_its_pull_request(one_turn):
    """R3.4: the branch is gone and so is the pull request; the next edit
    opens a new one, and the repos still going keep theirs."""
    service, conversations = one_turn
    checkout = FakeCheckout(conversations / '42', finished=['x'])
    service.conversations[42] = Conversation(
        checkout=checkout, pr_urls={'x': PR_URL, 'z': OTHER_PR})

    await service.run(42, 'do it', on_progress=_collect([]))

    assert service.conversations[42].pr_urls == {'z': OTHER_PR}
    # ...and the publish step was never offered the stale url.
    assert checkout.published == [{'z': OTHER_PR}]
    saved = json.loads((conversations / 'state.json').read_text(
        encoding='utf-8'))
    assert saved['42']['pr_urls'] == {'z': OTHER_PR}


async def test_preparation_notes_are_posted_before_the_agent_runs(one_turn,
                                                                  monkeypatch):
    """R3.7: they are true the moment they are made, and the turn can
    still fail on its way to a report the owner would never see."""
    service, conversations = one_turn
    note = "-# _Couldn't merge main into this branch: conflicts in a.py._"
    service.conversations[42] = Conversation(
        checkout=FakeCheckout(conversations / '42', notes=[note]))
    posted = []

    async def exploding_agent(**kwargs):
        raise RuntimeError('the agent fell over')

    monkeypatch.setattr('cogs.utils.agent_service.run_agent', exploding_agent)

    with pytest.raises(RuntimeError):
        await service.run(42, 'do it', on_progress=_collect(posted))

    assert posted == [note]


def _collect(sink):
    async def on_progress(text):
        sink.append(text)
    return on_progress


# --- recovery (R3.3) ---------------------------------------------------


@pytest.fixture
def recovering(agent_dirs, monkeypatch):
    """A service whose GitHub lookups are answered from a script."""
    service = AgentTaskService()
    service.asked = []

    async def branch_for_pull_request(url):
        service.asked.append(('branch_for', url))
        return service.branch_answer

    async def find_conversation_on_github(thread_id, starter_prompt):
        service.asked.append(('search', thread_id, starter_prompt))
        return service.search_answer

    service.branch_answer = BRANCH
    service.search_answer = None
    monkeypatch.setattr(service.workspace, 'branch_for_pull_request',
                        branch_for_pull_request)
    monkeypatch.setattr(service.workspace, 'find_conversation_on_github',
                        find_conversation_on_github)
    return service


async def test_announced_pull_requests_give_back_the_branch(recovering):
    """The thread announced them, so GitHub only has to say which branch
    the newest one is on."""
    assert await recovering.recover(42, STARTER, {'x': OTHER_PR,
                                                  'z': PR_URL})

    conversation = recovering.conversations[42]
    assert conversation.checkout.branch == BRANCH
    assert conversation.checkout.thread_id == 42
    assert conversation.pr_urls == {'x': OTHER_PR, 'z': PR_URL}
    # The most recently announced pull request is the live one.
    assert recovering.asked == [('branch_for', PR_URL)]


async def test_a_thread_with_no_announcements_is_searched_for(recovering):
    recovering.search_answer = (BRANCH, {'x': PR_URL})

    assert await recovering.recover(42, STARTER, {})

    assert recovering.asked == [('search', 42, STARTER)]
    assert recovering.conversations[42].pr_urls == {'x': PR_URL}
    assert recovering.conversations[42].checkout.branch == BRANCH


async def test_nothing_on_github_is_not_a_conversation(recovering):
    """A thread whose turns never edited code has nothing to put back, and
    starts a branch when one finally does."""
    recovering.search_answer = None

    assert not await recovering.recover(42, STARTER, {})
    assert not recovering.has_conversation(42)


async def test_a_pull_request_without_a_branch_recovers_nothing(recovering):
    recovering.branch_answer = ''

    assert not await recovering.recover(42, STARTER, {'x': PR_URL})
    assert not recovering.has_conversation(42)


async def test_a_known_conversation_is_left_alone(recovering, agent_dirs):
    _, conversations = agent_dirs
    existing = Conversation(
        checkout=recovering.workspace.new_checkout(conversations / '42',
                                                   STARTER))
    recovering.conversations[42] = existing

    assert not await recovering.recover(42, STARTER, {'x': PR_URL})

    assert recovering.conversations[42] is existing
    assert recovering.asked == []


async def test_two_messages_at_once_recover_one_conversation(recovering):
    """Recovery awaits GitHub before the conversation exists, so without
    the lock both callers would build one — and the two turns would then
    queue on different locks."""
    async def slow_branch(url):
        recovering.asked.append(('branch_for', url))
        await asyncio.sleep(0)
        return BRANCH

    recovering.workspace.branch_for_pull_request = slow_branch

    done = await asyncio.gather(recovering.recover(42, STARTER,
                                                   {'x': PR_URL}),
                                recovering.recover(42, STARTER,
                                                   {'x': PR_URL}))

    assert sorted(done) == [False, True]
    assert len(recovering.asked) == 1


# --- the backup (R2b, R5.2) -------------------------------------------


async def test_a_turn_writes_its_identity_to_the_backup(one_turn):
    """R5.2: the record that makes a wiped host find this thread again is
    written off-host on every change, not only at shutdown."""
    service, conversations = one_turn
    service.backup = FakeBackup()
    service.conversations[42] = Conversation(
        checkout=FakeCheckout(conversations / '42'))

    await service.run(42, 'do it', on_progress=_collect([]))

    record = service.backup.identities[42]
    assert record['thread_id'] == 42
    assert record['branch'] == BRANCH
    assert record['session_id'] == 'sess'
    # Not last_active: it changes every turn and nothing reads it, so
    # including it would commit to the backup repo on every message.
    assert 'last_active' not in record
    # The store now holds turns this host's transcript does not, and that
    # is remembered here, in state.json, and off-host, so no restart can
    # leave the next turn resuming from a frozen local copy.
    assert service.conversations[42].backed_up is True
    assert record['backed_up'] is True
    state = json.loads((conversations / 'state.json').read_text(
        encoding='utf-8'))
    assert state['42']['backed_up'] is True
    assert service.backup.flushed == [42]
    # ...and the turn ran against this conversation's session store.
    assert service.agent_calls[0]['session_store'] == 'store-42'
    assert service.backup.stores == [42]


async def test_a_turn_without_a_backup_still_runs(one_turn):
    """No JERMABOT_AGENT_BACKUP_REPO: loud at startup, but every turn
    still works, with no store for the SDK to mirror to."""
    service, conversations = one_turn
    service.conversations[42] = Conversation(
        checkout=FakeCheckout(conversations / '42'))
    posted = []

    report = await service.run(42, 'do it', on_progress=_collect(posted))

    assert report.answer == 'done'
    assert service.agent_calls[0]['session_store'] is None
    assert posted == []


async def test_an_unpushed_backup_is_reported_at_turn_end(one_turn):
    """R2b.1: the reply stands, but the thread hears that the copy never
    left this host."""
    service, conversations = one_turn
    service.backup = FakeBackup()
    service.backup.notes = ['-# _Backup not pushed to GitHub: fatal: could '
                            'not read from remote repository._']
    service.conversations[42] = Conversation(
        checkout=FakeCheckout(conversations / '42'))
    posted = []

    report = await service.run(42, 'do it', on_progress=_collect(posted))

    assert report.answer == 'done'
    assert posted == service.backup.notes


async def test_a_dropped_transcript_batch_is_reported(one_turn):
    """The SDK gives up on a batch after three tries; that must not pass
    silently, since the store is the only transcript after a resume."""
    service, conversations = one_turn
    service.backup = FakeBackup()
    service.agent_result = AgentRunResult(final_text='done', timed_out=False,
                                          session_id='sess',
                                          mirror_errors=['disk on fire'])
    service.conversations[42] = Conversation(
        checkout=FakeCheckout(conversations / '42'))
    posted = []

    await service.run(42, 'do it', on_progress=_collect(posted))

    assert posted == ['-# _Part of this turn is missing from the transcript '
                      'backup: disk on fire._']


async def test_the_store_says_it_better_than_the_sdk_does(one_turn):
    """The SDK's report and the store's are the same lost batch; the
    store's names what actually went wrong with it."""
    service, conversations = one_turn
    service.backup = FakeBackup()
    service.backup.dropped = {42: 'the disk is gone'}
    service.backup.notes = ['-# _Some of this turn never reached the '
                            'transcript backup: the disk is gone._']
    service.agent_result = AgentRunResult(final_text='done', timed_out=False,
                                          session_id='sess',
                                          mirror_errors=['disk on fire'])
    service.conversations[42] = Conversation(
        checkout=FakeCheckout(conversations / '42'))
    posted = []

    await service.run(42, 'do it', on_progress=_collect(posted))

    assert posted == service.backup.notes


async def test_a_backed_up_turn_refuses_to_run_without_the_backup(
        one_turn):
    """R2b.1: from its first store-backed turn, the SDK stops updating
    this host's transcript, so resuming without the store would answer
    from a frozen copy and quietly lose every turn since."""
    service, conversations = one_turn
    service.conversations[42] = Conversation(
        checkout=FakeCheckout(conversations / '42'),
        session_id='sess', backed_up=True)

    with pytest.raises(WorkspaceError,
                       match='JERMABOT_AGENT_BACKUP_REPO'):
        await service.run(42, 'do it', on_progress=_collect([]))

    assert service.agent_calls == []


async def test_a_backup_that_will_not_clone_only_stops_what_needs_it(
        one_turn):
    """R2b: a conversation with nothing in the backup yet has nothing to
    load, so it runs; one that depends on the backup hears why not."""
    service, conversations = one_turn
    service.backup = FakeBackup()
    service.backup_error = 'fatal: repository not found'
    service.conversations[42] = Conversation(
        checkout=FakeCheckout(conversations / '42'))
    service.conversations[7] = Conversation(
        checkout=FakeCheckout(conversations / '7'),
        session_id='sess', backed_up=True)

    report = await service.run(42, 'do it', on_progress=_collect([]))
    assert report.answer == 'done'
    # It ran from its local transcript: no store the SDK would fail to
    # load from, and no guard armed for a backup that never happened.
    assert service.agent_calls[-1]['session_store'] is None
    assert service.backup.stores == []
    assert service.conversations[42].backed_up is False

    with pytest.raises(WorkspaceError, match='repository not found'):
        await service.run(7, 'do it', on_progress=_collect([]))


async def test_an_identity_the_backup_refuses_does_not_cost_the_answer(
        one_turn):
    """state.json still has the conversation, and the next turn writes the
    record again; the turn's reply is worth more than the off-host copy."""
    class Refusing(FakeBackup):
        async def write_identity(self, thread_id, record):
            raise RuntimeError('github is down')

    service, conversations = one_turn
    service.backup = Refusing()
    service.conversations[42] = Conversation(
        checkout=FakeCheckout(conversations / '42'))
    posted = []

    report = await service.run(42, 'do it', on_progress=_collect(posted))

    assert report.answer == 'done'
    assert posted == ["-# _This conversation's identity record was not "
                      'updated: github is down._']
    assert service.conversations[42].session_id == 'sess'
    state = json.loads((conversations / 'state.json').read_text(
        encoding='utf-8'))
    assert state['42']['session_id'] == 'sess'


async def test_a_failed_publish_still_reports_the_backup(one_turn):
    """The turn is lost either way; which of the two things went wrong is
    not something to guess at from the thread."""
    service, conversations = one_turn
    service.backup = FakeBackup()
    service.backup.notes = ['-# _Backup not pushed to GitHub: no route._']
    checkout = FakeCheckout(conversations / '42')
    checkout.publish_error = WorkspaceError('the push failed')
    service.conversations[42] = Conversation(checkout=checkout)
    posted = []

    with pytest.raises(WorkspaceError, match='the push failed'):
        await service.run(42, 'do it', on_progress=_collect(posted))

    assert posted == service.backup.notes


async def test_recovery_asks_the_backup_before_github(recovering):
    """R2b.4: the identity record knows the session id too, so the
    conversation comes back whole rather than as a branch."""
    recovering.backup = FakeBackup({42: {
        'branch': BRANCH,
        'session_id': 'sess',
        'pr_urls': {'x': PR_URL},
    }})

    assert await recovering.recover(42, STARTER, {'z': OTHER_PR})

    conversation = recovering.conversations[42]
    assert conversation.checkout.branch == BRANCH
    assert conversation.checkout.thread_id == 42
    assert conversation.session_id == 'sess'
    # Both sources are kept: the thread announced a pull request the last
    # identity record to reach GitHub had not heard about yet.
    assert conversation.pr_urls == {'x': PR_URL, 'z': OTHER_PR}
    assert recovering.asked == []  # GitHub was never needed


async def test_recovery_falls_through_when_the_backup_has_nothing(recovering):
    """A v0 thread, or one whose backup predates the identity record."""
    recovering.backup = FakeBackup()

    assert await recovering.recover(42, STARTER, {'z': PR_URL})

    assert recovering.asked == [('branch_for', PR_URL)]
    assert recovering.conversations[42].checkout.branch == BRANCH
    # ...and what was recovered is written straight back to the backup.
    assert recovering.backup.identities[42]['branch'] == BRANCH


async def test_an_unreadable_backup_does_not_block_recovery(recovering):
    """R2.4: a source that fails falls through to the next one."""
    class Broken(FakeBackup):
        async def read_identity(self, thread_id):
            raise RuntimeError('github is down')

    recovering.backup = Broken()

    assert await recovering.recover(42, STARTER, {'z': PR_URL})

    assert recovering.asked == [('branch_for', PR_URL)]
