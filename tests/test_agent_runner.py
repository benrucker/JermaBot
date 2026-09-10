"""How one turn folds the SDK's message stream into its result, and what a
resume the SDK cannot load comes back as.
"""
import asyncio
import os
import re

import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ProcessError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

from cogs.utils import agent_runner
from cogs.utils.agent_workspace import WorkspaceError
from cogs.utils.agent_runner import (
    AgentRunResult,
    SessionResumeError,
    handle_message,
    local_transcript_path,
    run_agent,
)


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


def test_a_turn_folds_into_its_session_answer_and_narration():
    result, narration = fold(
        SystemMessage(subtype='init', data={'session_id': 'sess-1'}),
        assistant(TextBlock(text='looking now'),
                  ToolUseBlock(id='t1', name='Read', input={})),
        assistant(TextBlock(text='here you go')),
        result_message('here you go'),
    )

    # The init message names the session up front, so a timeout that cut
    # the run short of its ResultMessage would still know what to resume.
    assert result.session_id == 'sess-1'
    assert result.final_text == 'here you go'
    # Text alongside a tool call is narration, not the answer.
    assert narration == ['looking now']


# --- where a transcript lives, and what a lost one raises ---------------


def test_a_transcript_is_looked_for_where_the_sdk_files_it(tmp_path,
                                                           monkeypatch):
    """Measured against SDK 0.2.110: the checkout's real path with every
    non-alphanumeric character replaced, under the projects directory of
    the config dir the CLI is using."""
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude'))
    checkout = tmp_path / 'conversations' / '42'
    checkout.mkdir(parents=True)

    path = local_transcript_path(checkout, 'abc-123')

    project_key = re.sub(r'[^a-zA-Z0-9]', '-', os.path.realpath(checkout))
    assert path == (tmp_path / 'claude' / 'projects' / project_key
                    / 'abc-123.jsonl')


CLI_COMPLAINT = 'No conversation found with session ID: sess'
# What the SDK raises when the CLI exits non-zero, measured against
# 0.2.110: a fixed sentence with a placeholder where the reason should be.
# The reason itself only ever arrives through the options.stderr callback.
NO_SESSION = ProcessError('Command failed with exit code 1', exit_code=1,
                          stderr='Check stderr output for details')


def refusing_client(error, stderr_text: str | None = None):
    """An SDK client whose connect() fails, having first said `stderr_text`
    down the stderr callback the way the CLI subprocess would. It has
    already cleaned up after itself, so nothing is disconnected."""
    class _Client:
        def __init__(self, options):
            self.options = options

        async def connect(self):
            if stderr_text is not None:
                self.options.stderr(stderr_text)
            raise error
    return _Client


async def test_a_resume_the_sdk_cannot_load_is_its_own_failure(tmp_path,
                                                               monkeypatch):
    """R2.4: the caller can still run the turn from somewhere else, so
    this must not look like the turn falling over — and it carries what
    the CLI said, not ProcessError's placeholder."""
    monkeypatch.setattr(agent_runner, 'ClaudeSDKClient',
                        refusing_client(NO_SESSION, CLI_COMPLAINT))

    with pytest.raises(SessionResumeError) as raised:
        await run_agent(prompt='do it', workspace_root=tmp_path, repos={},
                        on_progress=None, resume='sess')

    assert CLI_COMPLAINT in str(raised.value)


async def test_a_failure_with_nothing_to_resume_is_just_a_failure(
        tmp_path, monkeypatch):
    """Nothing to fall through to: the turn is over, said in the words the
    thread can be shown."""
    monkeypatch.setattr(agent_runner, 'ClaudeSDKClient',
                        refusing_client(NO_SESSION, CLI_COMPLAINT))

    with pytest.raises(WorkspaceError) as raised:
        await run_agent(prompt='do it', workspace_root=tmp_path, repos={},
                        on_progress=None)

    assert not isinstance(raised.value, SessionResumeError)
    assert CLI_COMPLAINT in str(raised.value)


async def test_a_resume_that_fails_before_the_process_fails_the_turn(
        tmp_path, monkeypatch):
    """A load that fails before the CLI runs is not a process failure and
    not a lost session. Answering it from a thread rebuild would overwrite
    the session id with a new one, for a session that was never gone."""
    monkeypatch.setattr(agent_runner, 'ClaudeSDKClient',
                        refusing_client(RuntimeError('load timed out')))

    with pytest.raises(WorkspaceError, match='load timed out') as raised:
        await run_agent(prompt='do it', workspace_root=tmp_path, repos={},
                        on_progress=None, resume='sess')

    assert not isinstance(raised.value, SessionResumeError)
