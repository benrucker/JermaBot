"""State loading, recovery, and what one turn does to a conversation.

An identity outlives its checkout directory, and with recover() it
outlives the state file too: what Discord and GitHub still know is enough
to put a conversation back. GitHub is a stub here; the workspace's own
half is exercised in test_agent_workspace_git and test_agent_github.
"""
import asyncio
import json
import shutil

import pytest

from cogs.utils.agent_config import AGENT_REPOS
from cogs.utils.agent_runner import (
    AgentRunResult,
    SessionResumeError,
    local_transcript_path,
)
from cogs.utils.agent_service import (
    RELOADING_NOTE,
    AgentTaskService,
    Conversation,
    ReconstructedHistory,
)
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
    # ...and round-trips to exactly the shape it came in as.
    assert conversation.to_state() == v0['999']


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
        if self.publish_error is not None:
            raise self.publish_error
        self.published.append(dict(pr_urls))
        return []


@pytest.fixture
def one_turn(agent_dirs, monkeypatch):
    """A service whose turns run no agent and touch no git."""
    service, conversations = _scripted_agent(agent_dirs, monkeypatch)

    async def ready():
        return None

    monkeypatch.setattr(service, '_ready', ready)
    return service, conversations


@pytest.fixture
def starting_up(agent_dirs, monkeypatch):
    """The same, with the real _ready: these tests are about what it
    readies. Cloning a repo is faked down to making its directory, so
    ensure_repos' own bookkeeping is what runs."""
    service, conversations = _scripted_agent(agent_dirs, monkeypatch)
    service.cloned = []

    async def fake_clone(name):
        service.cloned.append(name)
        (service.workspace.root / name / '.git').mkdir(parents=True)

    monkeypatch.setattr(service.workspace, '_clone', fake_clone)
    return service, conversations


def _scripted_agent(agent_dirs, monkeypatch):
    """A service whose agent runs are answered from a script."""
    _, conversations = agent_dirs
    service = AgentTaskService()
    service.agent_calls = []
    service.agent_result = AgentRunResult(final_text='done', timed_out=False,
                                          session_id='sess')
    # Turn-by-turn script for the tests that need one; an exception in it
    # is raised instead of returned. Empty means every turn answers with
    # agent_result.
    service.agent_results = []

    async def fake_run_agent(**kwargs):
        service.agent_calls.append(kwargs)
        answer = (service.agent_results.pop(0) if service.agent_results
                  else service.agent_result)
        if isinstance(answer, Exception):
            raise answer
        return answer

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


# --- what a turn re-readies (R4.2) -----------------------------------


async def test_a_pristine_clone_deleted_mid_life_comes_back(starting_up):
    """The clones were only ever made at startup, so a directory that went
    away afterwards left every later turn without a repo to work in."""
    service, conversations = starting_up
    service.conversations[42] = Conversation(
        checkout=FakeCheckout(conversations / '42'))

    await service.run(42, 'do it', on_progress=_collect([]))
    assert sorted(service.cloned) == sorted(AGENT_REPOS)

    shutil.rmtree(service.workspace.root / 'jermabot')
    await service.run(42, 'do it', on_progress=_collect([]))

    assert service.cloned.count('jermabot') == 2
    assert service.workspace.missing_repos() == []


async def test_a_startup_with_nothing_to_redo_is_not_repeated(starting_up):
    """The retry is only for what went missing: a healthy service clones
    once, however many turns run."""
    service, conversations = starting_up
    service.conversations[42] = Conversation(
        checkout=FakeCheckout(conversations / '42'))

    await service.run(42, 'do it', on_progress=_collect([]))
    await service.run(42, 'again', on_progress=_collect([]))

    assert sorted(service.cloned) == sorted(AGENT_REPOS)


# --- where a turn's context comes from (R2.2, R2.4, R2c, R6.2) ---------


def write_local_transcript(root, session_id: str):
    """Stand in for the SDK: the transcript this host keeps for a session."""
    path = local_transcript_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"type": "user"}\n', encoding='utf-8')
    return path


def _thread_history(text='Prior history:\n\n[..] Owner:\nhello',
                    images=(), calls=None):
    """A reconstruct callback, counting its calls in `calls`."""
    async def reconstruct():
        if calls is not None:
            calls.append(text)
        return ReconstructedHistory(text=text, images=list(images))
    return reconstruct


def new_conversation(service, conversations, key=42, **kwargs) -> Conversation:
    root = conversations / str(key)
    root.mkdir(parents=True, exist_ok=True)
    conversation = Conversation(checkout=FakeCheckout(root), **kwargs)
    service.conversations[key] = conversation
    return conversation


async def test_a_local_transcript_is_resumed(one_turn):
    """R2.2 source 1: the SDK can still read this host's copy, so the turn
    continues the session and the thread hears nothing."""
    service, conversations = one_turn
    conversation = new_conversation(service, conversations, session_id='sess')
    write_local_transcript(conversation.checkout.root, 'sess')
    posted, rebuilt = [], []

    await service.run(42, 'do it', on_progress=_collect(posted),
                      reconstruct=_thread_history(calls=rebuilt))

    assert service.agent_calls[0]['resume'] == 'sess'
    assert service.agent_calls[0]['prompt'] == 'do it'
    assert rebuilt == []  # the thread was never read
    assert posted == []   # nothing was lost, so nothing is said (R4.3)


async def test_a_failed_publish_keeps_the_session_id(one_turn):
    """The id is saved the moment the agent answers: a push that fails
    afterwards must not cost the next turn its transcript."""
    service, conversations = one_turn
    conversation = new_conversation(service, conversations)
    conversation.checkout.publish_error = WorkspaceError('push refused')

    with pytest.raises(WorkspaceError, match='push refused'):
        await service.run(42, 'do it', on_progress=_collect([]))

    saved = json.loads((conversations / 'state.json').read_text(
        encoding='utf-8'))
    assert saved['42']['session_id'] == 'sess'


async def test_no_transcript_anywhere_rebuilds_from_the_thread(one_turn):
    """R2.2 source 2 with R2c: a new session, the thread's history in front
    of the owner's message, its images beside this turn's own, and the one
    muted line that admits what was lost (R4.3)."""
    service, conversations = one_turn
    # A session this host no longer has the transcript for.
    new_conversation(service, conversations, session_id='sess')
    service.agent_result = AgentRunResult(final_text='done', timed_out=False,
                                          session_id='sess-2')
    posted = []

    await service.run(42, 'do it', on_progress=_collect(posted),
                      images=[('now.png', b'now')],
                      reconstruct=_thread_history(
                          text='Prior history:\n\n[..] Owner:\nhello',
                          images=[('then.png', b'then')]))

    call = service.agent_calls[0]
    assert call['resume'] is None
    assert call['prompt'] == ('Prior history:\n\n[..] Owner:\nhello\n\n'
                              'New message from the owner:\ndo it')
    # The commit and pull request still describe what was asked, not the
    # history bolted in front of it.
    assert call['request'] == 'do it'
    assert [path.name for path in call['image_paths']] == ['now.png',
                                                           'then.png']
    assert posted == [RELOADING_NOTE]
    # The session the rebuilt turn made is what the next one resumes.
    assert service.conversations[42].session_id == 'sess-2'


async def test_a_brand_new_conversation_says_nothing(one_turn):
    """Nothing was lost: no session, no history, no muted line."""
    service, conversations = one_turn
    new_conversation(service, conversations)

    posted = []
    await service.run(42, 'do it', on_progress=_collect(posted),
                      reconstruct=_thread_history(text=''))

    assert service.agent_calls[0]['resume'] is None
    assert service.agent_calls[0]['prompt'] == 'do it'
    assert posted == []


async def test_a_resume_the_sdk_refuses_falls_back_to_the_thread(one_turn):
    """R2.4: the transcript was there a moment ago and would not load. The
    same turn runs again from the thread, and says so once."""
    service, conversations = one_turn
    conversation = new_conversation(service, conversations, session_id='sess')
    write_local_transcript(conversation.checkout.root, 'sess')
    service.agent_results = [SessionResumeError('No conversation found')]
    posted, rebuilt = [], []

    report = await service.run(42, 'do it', on_progress=_collect(posted),
                               reconstruct=_thread_history(calls=rebuilt))

    assert report.answer == 'done'
    assert len(service.agent_calls) == 2
    assert service.agent_calls[0]['resume'] == 'sess'
    assert service.agent_calls[1]['resume'] is None
    assert service.agent_calls[1]['prompt'].endswith(
        'New message from the owner:\ndo it')
    assert len(rebuilt) == 1
    assert posted == [RELOADING_NOTE]


async def test_a_history_that_cannot_be_rebuilt_ends_the_turn(one_turn):
    """R4.4: no transcript and no thread to read is the end of the turn,
    with the cause in the thread rather than a silent fresh start."""
    service, conversations = one_turn
    new_conversation(service, conversations, session_id='sess')

    async def broken():
        raise RuntimeError('Discord said 500')

    with pytest.raises(WorkspaceError, match='Discord said 500'):
        await service.run(42, 'do it', on_progress=_collect([]),
                          reconstruct=broken)

    assert service.agent_calls == []


async def test_a_second_message_reuses_the_rebuilt_session(one_turn,
                                                           monkeypatch):
    """R6.2: it queues on the conversation's lock, and the source decision
    is made inside it, so it resumes what the rebuild made instead of
    rebuilding the same thread again."""
    service, conversations = one_turn
    conversation = new_conversation(service, conversations, session_id='old')
    rebuilt = []
    running = asyncio.Event()   # the first turn is inside the lock
    finish = asyncio.Event()    # ...and may now leave it
    queued = asyncio.Event()    # the second message is on its way in

    async def fake_run_agent(**kwargs):
        service.agent_calls.append(kwargs)
        if len(service.agent_calls) == 1:
            running.set()
            await finish.wait()
        # What the SDK does on the way out, and what the next turn looks
        # for.
        write_local_transcript(conversation.checkout.root, 'sess-2')
        return AgentRunResult(final_text='done', timed_out=False,
                              session_id='sess-2')

    monkeypatch.setattr('cogs.utils.agent_service.run_agent', fake_run_agent)
    posted = []

    async def second_message():
        # Set from inside the task: run() reaches the conversation's lock
        # without awaiting anything else, so once this is seen the second
        # message really is queued behind the first.
        queued.set()
        return await service.run(42, 'second', on_progress=_collect(posted),
                                 reconstruct=_thread_history(calls=rebuilt))

    first = asyncio.create_task(
        service.run(42, 'first', on_progress=_collect(posted),
                    reconstruct=_thread_history(calls=rebuilt)))
    await running.wait()
    second = asyncio.create_task(second_message())
    await queued.wait()
    finish.set()

    await asyncio.gather(first, second)

    assert len(service.agent_calls) == 2
    assert service.agent_calls[0]['resume'] is None
    assert service.agent_calls[1]['resume'] == 'sess-2'
    assert service.agent_calls[1]['prompt'] == 'second'
    assert len(rebuilt) == 1
    assert posted == [RELOADING_NOTE]
