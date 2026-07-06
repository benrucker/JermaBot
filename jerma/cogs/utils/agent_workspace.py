"""Git and GitHub plumbing for the coding agent.

The agent itself has no Bash or network access; this module owns every git
operation. Repos live in a persistent workspace directory and are cloned
automatically on first use. Each run resets them to origin, and any repo the
agent leaves dirty becomes a branch + pull request.

GitHub access goes through the gh CLI, which reads GITHUB_TOKEN from the
environment: git authenticates via `gh auth git-credential` plugged in as a
per-command credential helper, and pull requests are opened with
`gh pr create`. The token is never written into the workspace (which the
agent can read). Without a token, git and gh fall back to whatever ambient
credentials the machine has (e.g. Git Credential Manager or a `gh auth
login` session on a dev box).
"""
import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .agent_config import (
    AGENT_BRANCH_PREFIX,
    AGENT_COMMIT_EMAIL,
    AGENT_COMMIT_NAME,
    AGENT_REQUEST_SOURCE,
    AgentRepo,
)

_CREDENTIAL_HELPER = '!gh auth git-credential'


class WorkspaceError(Exception):
    """A workspace problem the owner needs to hear about verbatim."""


@dataclass
class OpenedPullRequest:
    repo_name: str
    url: str


async def _run(argv: list[str], cwd: Path,
               env: dict[str, str] | None = None,
               stdin_data: str | None = None) -> str:
    """Run a command, returning stdout or raising WorkspaceError."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate(
        stdin_data.encode() if stdin_data is not None else None)
    if proc.returncode != 0:
        command = ' '.join(argv[:4]) + (' ...' if len(argv) > 4 else '')
        raise WorkspaceError(
            f'`{command}` failed in {cwd.name} '
            f'(exit {proc.returncode}): {stderr.decode(errors="replace").strip()}'
        )
    return stdout.decode(errors='replace')


async def _run_git(repo_dir: Path, *args: str) -> str:
    return await _run(['git', *args], cwd=repo_dir)


def slugify(text: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return slug[:40].rstrip('-') or 'task'


class AgentWorkspace:
    def __init__(self, root: Path, repos: dict[str, AgentRepo],
                 github_token: str | None):
        self.root = root
        self.repos = repos
        self.github_token = github_token
        if github_token:
            # The empty helper first clears any configured helpers (e.g. a
            # credential manager) so gh, holding our token, is the only one
            # consulted.
            self._auth_flags = ['-c', 'credential.helper=',
                                '-c', f'credential.helper={_CREDENTIAL_HELPER}']
            self._auth_env = {**os.environ,
                              'GITHUB_TOKEN': github_token,
                              'GIT_TERMINAL_PROMPT': '0'}
        else:
            self._auth_flags = []
            self._auth_env = None
            print('AgentWorkspace: GITHUB_TOKEN not set; using this '
                  'machine\'s ambient git/gh credentials.')

    async def _run_authed(self, argv: list[str], cwd: Path,
                          stdin_data: str | None = None) -> str:
        """Run a command that talks to GitHub, adding a hint on auth trouble."""
        try:
            return await _run(argv, cwd=cwd, env=self._auth_env,
                              stdin_data=stdin_data)
        except WorkspaceError as e:
            if self.github_token:
                raise
            raise WorkspaceError(
                f'{e} (GITHUB_TOKEN is not set — set it, or run '
                '`gh auth login` on this machine.)'
            ) from e

    async def _run_git_authed(self, repo_dir: Path, *args: str) -> str:
        """Authed twin of _run_git, for git commands that talk to GitHub."""
        return await self._run_authed(['git', *self._auth_flags, *args],
                                      cwd=repo_dir)

    def missing_repos(self) -> list[str]:
        """Names of configured repos not present in the workspace."""
        return [name for name in self.repos
                if not (self.root / name / '.git').exists()]

    async def ensure_repos(self) -> list[str]:
        """Clone any repos missing from the workspace; returns names cloned."""
        self.root.mkdir(parents=True, exist_ok=True)
        missing = self.missing_repos()
        await asyncio.gather(*(self._clone(name) for name in missing))
        return missing

    def _clone_url(self, name: str) -> str:
        return f'https://github.com/{self.repos[name].slug}.git'

    async def _clone(self, name: str):
        """Shallow-clone a repo, atomically: no half-cloned dir survives."""
        partial = self.root / f'{name}.cloning'
        if partial.exists():
            await asyncio.to_thread(shutil.rmtree, partial)
        await self._run_git_authed(
            self.root, 'clone', '--depth', '1', '--single-branch',
            '-b', self.repos[name].base_branch,
            self._clone_url(name), str(partial),
        )
        partial.rename(self.root / name)

    async def reset_all(self):
        """Bring every repo to a clean checkout of its base branch at origin."""
        await asyncio.gather(*(self._reset(name) for name in self.repos))

    async def _reset(self, name: str):
        repo_dir = self.root / name
        base = self.repos[name].base_branch
        await self._run_git_authed(repo_dir, 'fetch', 'origin', '--prune')
        await _run_git(repo_dir, 'checkout', '-B', base, f'origin/{base}')
        await _run_git(repo_dir, 'clean', '-fdx')

    async def dirty_repos(self) -> list[str]:
        """Names of repos with uncommitted changes (the agent's edits)."""
        names = list(self.repos)
        statuses = await asyncio.gather(
            *(_run_git(self.root / name, 'status', '--porcelain')
              for name in names))
        return [name for name, status in zip(names, statuses)
                if status.strip()]

    async def publish(self, name: str, prompt: str, summary: str) -> OpenedPullRequest:
        """Turn a dirty repo into a branch, commit, push, and pull request."""
        repo = self.repos[name]
        repo_dir = self.root / name
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        branch = f'{AGENT_BRANCH_PREFIX}/{slugify(prompt)}-{timestamp}'
        title = prompt.strip().splitlines()[0]

        await _run_git(repo_dir, 'checkout', '-b', branch)
        await _run_git(repo_dir, 'add', '-A')
        await _run_git(
            repo_dir,
            '-c', f'user.name={AGENT_COMMIT_NAME}',
            '-c', f'user.email={AGENT_COMMIT_EMAIL}',
            'commit', '-m', title[:72],
            '-m', f'Requested via {AGENT_REQUEST_SOURCE}:\n\n{prompt}',
        )
        await self._run_git_authed(repo_dir, 'push', 'origin', branch)
        try:
            url = await self._create_pull_request(
                repo, repo_dir, branch, title, summary)
        finally:
            await _run_git(repo_dir, 'checkout', repo.base_branch)
            await _run_git(repo_dir, 'branch', '-D', branch)
        return OpenedPullRequest(repo_name=name, url=url)

    async def _create_pull_request(self, repo: AgentRepo, repo_dir: Path,
                                   branch: str, title: str,
                                   summary: str) -> str:
        body = summary.strip()[:60000] or '(The agent did not leave a summary.)'
        body += ('\n\n---\nOpened by the JermaBot coding agent at the '
                 f'owner\'s request via {AGENT_REQUEST_SOURCE}.')
        # --body-file - takes the body on stdin, dodging argv size limits.
        output = await self._run_authed(
            ['gh', 'pr', 'create',
             '--repo', repo.slug,
             '--head', branch,
             '--base', repo.base_branch,
             '--title', title[:250],
             '--body-file', '-'],
            cwd=repo_dir,
            stdin_data=body,
        )
        # gh prints the new PR's URL as the last line of stdout.
        return output.strip().splitlines()[-1]
