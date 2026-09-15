"""Shared fixtures.

The agent service reads its directories from the environment at
construction time, so every test points them at a tmp_path first;
nothing here ever touches the real workspace or the network.
"""
import pytest

from claude_agent_sdk import ResultMessage

from cogs.utils import agent_config


def result_message(text: str, **fields) -> ResultMessage:
    """The SDK's end-of-turn message, with only what a test cares about."""
    return ResultMessage(**{
        'subtype': 'success', 'duration_ms': 1, 'duration_api_ms': 1,
        'is_error': False, 'num_turns': 1, 'session_id': 's',
        'result': text, **fields})


@pytest.fixture
def agent_dirs(tmp_path, monkeypatch):
    """Point the workspace and conversations roots at a temp directory."""
    repos = tmp_path / 'repos'
    conversations = tmp_path / 'conversations'
    monkeypatch.setenv('JERMABOT_AGENT_REPOS_DIR', str(repos))
    monkeypatch.setenv('JERMABOT_AGENT_CONVERSATIONS_DIR', str(conversations))
    # Where the SDK would keep this host's transcripts. A turn asks
    # whether one is still there, so no test may go looking in the real
    # ~/.claude.
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude'))
    # With no token, a service built here talks to no GitHub at all.
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    return (agent_config.get_workspace_root(),
            agent_config.get_conversations_root())
