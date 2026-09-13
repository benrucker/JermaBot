"""How a turn's edits get their name: what the naming call is given,
what it may do, and what it is not allowed to make up.
"""
import pytest

from claude_agent_sdk import ProcessError, ResultMessage

from cogs.utils import agent_title
from cogs.utils.agent_config import AgentRepo
from cogs.utils.agent_runner import AGENT_TOOLS
from cogs.utils.agent_title import TITLE_TOOLS, generate_title
from cogs.utils.agent_workspace import WorkspaceError

REPOS = {'jermabot': AgentRepo('benrucker/JermaBot', 'develop')}


def result_message(text, **fields) -> ResultMessage:
    return ResultMessage(**{
        'subtype': 'success', 'duration_ms': 1, 'duration_api_ms': 1,
        'is_error': False, 'num_turns': 1, 'session_id': 's',
        'result': text, **fields})


def scripted_query(*messages, calls: list):
    """A stand-in for the SDK's query(): records the call, yields the
    script."""
    async def fake_query(*, prompt, options):
        calls.append((prompt, options))
        for message in messages:
            yield message
    return fake_query


async def name_change(tmp_path, monkeypatch, *messages, reply='Changed it',
                      request='do it'):
    calls = []
    monkeypatch.setattr(agent_title, 'query',
                        scripted_query(*messages, calls=calls))
    title = await generate_title(request=request, reply=reply,
                                 edited_repos=['jermabot'],
                                 workspace_root=tmp_path, repos=REPOS)
    return title, calls


async def test_the_title_is_the_naming_calls_structured_answer(tmp_path,
                                                                monkeypatch):
    title, [(prompt, options)] = await name_change(
        tmp_path, monkeypatch,
        result_message('{"title": "Add a thing"}',
                       structured_output={'title': ' Add a thing\n'}))

    assert title == 'Add a thing'
    # The call sees the owner's words, the reply, and which repo changed.
    assert 'do it' in prompt and 'Changed it' in prompt
    assert 'The agent edited: jermabot/' in prompt
    assert 'github.com/benrucker/JermaBot' in prompt
    assert str(tmp_path) in prompt  # a real path to read under


async def test_the_naming_call_is_a_cheap_read_only_opus_call(tmp_path,
                                                              monkeypatch):
    _, [(_, options)] = await name_change(
        tmp_path, monkeypatch,
        result_message('', structured_output={'title': 'Add a thing'}))

    assert options.model == agent_title.AGENT_TITLE_MODEL
    assert options.effort == 'low'
    assert options.tools == TITLE_TOOLS
    assert 'Bash' in options.disallowed_tools
    assert options.setting_sources == []
    assert options.output_format['type'] == 'json_schema'
    assert str(tmp_path) == options.cwd


async def test_the_naming_calls_guard_allows_reading_only_inside(
        tmp_path, monkeypatch):
    """Same workspace confinement as the agent, minus the editing tools."""
    _, [(_, options)] = await name_change(
        tmp_path, monkeypatch,
        result_message('', structured_output={'title': 'Add a thing'}))
    [matcher] = options.hooks['PreToolUse']
    [guard] = matcher.hooks

    async def decision(tool, **tool_input):
        out = await guard({'tool_name': tool, 'tool_input': tool_input},
                          None, None)
        return out['hookSpecificOutput']['permissionDecision']

    assert await decision('Read', file_path=str(tmp_path / 'a.py')) == 'allow'
    assert await decision('Read', file_path='/etc/passwd') == 'deny'
    assert await decision('Edit', file_path=str(tmp_path / 'a.py')) == 'deny'
    assert await decision('Bash', command='ls') == 'deny'
    # The answer arrives as a tool call the CLI adds for output_format.
    # Checked against CLI 2.1.191: deny it and the title never comes.
    assert await decision('StructuredOutput', title='Add x') == 'allow'


def test_the_agent_still_gets_its_editing_tools():
    assert 'Edit' in AGENT_TOOLS and 'Edit' not in TITLE_TOOLS


async def test_a_missing_title_is_a_failure_not_a_stand_in(tmp_path,
                                                            monkeypatch):
    with pytest.raises(WorkspaceError, match='no title'):
        await name_change(tmp_path, monkeypatch,
                          result_message('', structured_output={'title': ''}))
    with pytest.raises(WorkspaceError, match='no title'):
        await name_change(tmp_path, monkeypatch,
                          result_message('prose, not the schema'))
    with pytest.raises(WorkspaceError, match='without a result'):
        await name_change(tmp_path, monkeypatch)


async def test_an_error_result_carries_the_clis_reason(tmp_path,
                                                       monkeypatch):
    with pytest.raises(WorkspaceError, match='over budget'):
        await name_change(
            tmp_path, monkeypatch,
            result_message(None, subtype='error_max_turns', is_error=True,
                           errors=['over budget']))


async def test_a_process_failure_carries_stderr(tmp_path, monkeypatch):
    async def dying_query(*, prompt, options):
        options.stderr('Not logged in')
        raise ProcessError('Command failed with exit code 1', exit_code=1,
                           stderr='Check stderr output for details')
        yield  # noqa: an async generator

    monkeypatch.setattr(agent_title, 'query', dying_query)

    with pytest.raises(WorkspaceError, match='Not logged in'):
        await generate_title(request='do it', reply='', edited_repos=['x'],
                             workspace_root=tmp_path, repos=REPOS)


async def test_a_reply_that_never_came_is_still_named(tmp_path, monkeypatch):
    """A timed-out turn has edits and no reply; the call gets the request
    and a placeholder, never an empty section."""
    _, [(prompt, _)] = await name_change(
        tmp_path, monkeypatch,
        result_message('', structured_output={'title': 'Add a thing'}),
        reply='')

    assert "The agent's reply:\n(no reply)" in prompt
