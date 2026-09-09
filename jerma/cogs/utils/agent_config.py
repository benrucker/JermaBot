"""Configuration for the owner-only coding agent feature."""
import os
from dataclasses import dataclass
from pathlib import Path


AGENT_TIMEOUT_SECONDS = 900
AGENT_MAX_TURNS = 50


@dataclass(frozen=True)
class AgentRepo:
    """A repository the coding agent may work in.

    Its key in AGENT_REPOS is the repo's directory name inside the
    workspace; see agent_workspace for how the workspace is managed.
    """
    slug: str  # "owner/name" on GitHub
    base_branch: str


AGENT_REPOS: dict[str, AgentRepo] = {
    'jermabot': AgentRepo('benrucker/JermaBot', 'develop'),
    'shigure-js': AgentRepo('ecfidler/shigure-js', 'main'),
    'whid10': AgentRepo('benrucker/whid10', 'main'),
}

AGENT_BRANCH_PREFIX = 'jermabot'
AGENT_COMMIT_NAME = 'JermaBot'
# Deliberately non-resolving (RFC 2606 reserved TLD). A bare
# <username>@users.noreply.github.com address links commits to whoever owns that
# GitHub username -- github.com/Jermabot is a real, unrelated account.
AGENT_COMMIT_EMAIL = 'jermabot@jermabot.invalid'
AGENT_REQUEST_SOURCE = 'Discord'
# The last lines of every pull request the agent opens. The footer marks a
# pull request as ours when the identity record is gone and GitHub is all
# that is left to search (R3.3); the thread line then says which
# conversation it belongs to.
AGENT_PR_FOOTER = ("Opened by the JermaBot coding agent at the owner's "
                   f'request via {AGENT_REQUEST_SOURCE}.')


def pr_thread_line(thread_id: int) -> str:
    """The line in a pull request body naming the thread it came from."""
    return f'{AGENT_REQUEST_SOURCE} thread: {thread_id}'


def _env_dir(var: str, default: str) -> Path:
    return Path(os.environ.get(var, default)).expanduser().resolve()


def get_workspace_root() -> Path:
    """Directory containing the pre-cloned target repos."""
    return _env_dir('JERMABOT_AGENT_REPOS_DIR', '~/jermabot-agent/repos')


def get_conversations_root() -> Path:
    """Directory holding per-conversation checkouts and their state file."""
    return _env_dir('JERMABOT_AGENT_CONVERSATIONS_DIR',
                    '~/jermabot-agent/conversations')


def get_github_token() -> str | None:
    """Fine-grained PAT for GitHub access (git auth and pull requests)."""
    return os.environ.get('GITHUB_TOKEN')
