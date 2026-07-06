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
    'shigure-js': AgentRepo('ecfilder/shigure-js', 'main'),
    'whid10': AgentRepo('benrucker/whid10', 'main'),
}

AGENT_BRANCH_PREFIX = 'jermabot'
AGENT_COMMIT_NAME = 'JermaBot'
AGENT_COMMIT_EMAIL = 'jermabot@users.noreply.github.com'
AGENT_REQUEST_SOURCE = 'Discord'


def get_workspace_root() -> Path:
    """Directory containing the pre-cloned target repos."""
    raw = os.environ.get('JERMABOT_AGENT_REPOS_DIR', '~/jermabot-agent/repos')
    return Path(raw).expanduser().resolve()


def get_github_token() -> str | None:
    """Fine-grained PAT for GitHub access (git auth and pull requests)."""
    return os.environ.get('GITHUB_TOKEN')
