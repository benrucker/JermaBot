"""How one turn folds the SDK's message stream into its result.

The interesting case is the mirror error: MirrorErrorMessage subclasses
SystemMessage, so the obvious `isinstance(message, SystemMessage)` branch
swallows it, and a backup that dropped a batch would pass in silence —
which, once a session has resumed from the store, means a turn lost with
nobody told (R2b.1).
"""
import asyncio

from claude_agent_sdk import (
    AssistantMessage,
    MirrorErrorMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

from cogs.utils.agent_runner import AgentRunResult, handle_message


def fold(*messages) -> tuple[AgentRunResult, list[str]]:
    """Run messages through the handler; returns the result and narration."""
    result = AgentRunResult(final_text='', timed_out=False)
    outbox: asyncio.Queue = asyncio.Queue()
    for message in messages:
        handle_message(message, result, outbox)
    narration = []
    while not outbox.empty():
        narration.append(outbox.get_nowait())
    return result, narration


def assistant(*blocks) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model='claude')


def result_message(text: str) -> ResultMessage:
    return ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1,
                         is_error=False, num_turns=1, session_id='s',
                         result=text)


def test_a_dropped_backup_batch_is_recorded():
    result, _ = fold(MirrorErrorMessage(
        subtype='mirror_error',
        data={'type': 'system', 'subtype': 'mirror_error'},
        key={'project_key': 'p', 'session_id': 's'},
        error='boom:\n  the store refused the batch'))

    assert result.mirror_errors == ['boom: the store refused the batch']


def test_a_mirror_error_is_not_mistaken_for_an_init_message():
    """It carries no session_id, and the id from init must survive it."""
    result, _ = fold(
        SystemMessage(subtype='init', data={'session_id': 'sess-1'}),
        MirrorErrorMessage(subtype='mirror_error', data={}, error='boom'),
    )

    assert result.session_id == 'sess-1'
    assert result.mirror_errors == ['boom']


def test_a_clean_turn_records_no_mirror_error():
    result, narration = fold(
        SystemMessage(subtype='init', data={'session_id': 'sess-1'}),
        assistant(TextBlock(text='looking now'),
                  ToolUseBlock(id='t1', name='Read', input={})),
        assistant(TextBlock(text='here you go')),
        result_message('here you go'),
    )

    assert result.mirror_errors == []
    assert result.session_id == 'sess-1'
    assert result.final_text == 'here you go'
    # Text alongside a tool call is narration, not the answer.
    assert narration == ['looking now']
