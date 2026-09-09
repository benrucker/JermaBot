"""State loading: an identity outlives its checkout directory."""
import json

from cogs.utils.agent_service import AgentTaskService, Conversation

BRANCH = 'jermabot/fix-the-thing-20260816-101500'
PR_URL = 'https://github.com/x/y/pull/1'


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
    # ...and round-trips to the same shape, so an older build could read it.
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
