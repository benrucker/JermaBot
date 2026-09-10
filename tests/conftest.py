"""Shared fixtures.

The agent service reads its directories from the environment at
construction time, so every test points them at a tmp_path first; nothing
here ever touches the real workspace or a network.
"""
import pytest

from cogs.utils import agent_config


@pytest.fixture
def agent_dirs(tmp_path, monkeypatch):
    """Point the workspace and conversations roots at a temp directory."""
    repos = tmp_path / 'repos'
    conversations = tmp_path / 'conversations'
    monkeypatch.setenv('JERMABOT_AGENT_REPOS_DIR', str(repos))
    monkeypatch.setenv('JERMABOT_AGENT_CONVERSATIONS_DIR', str(conversations))
    # Where the SDK would keep this host's transcripts: a turn asks
    # whether one is still there, so no test may go looking in the real
    # ~/.claude.
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude'))
    # No token: a service built here talks to no GitHub at all.
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    return (agent_config.get_workspace_root(),
            agent_config.get_conversations_root())
