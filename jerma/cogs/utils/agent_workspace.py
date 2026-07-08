"""Git and GitHub plumbing for the coding agent.

The agent itself has no Bash or network access; this module owns every git
operation. Pristine clones live in a persistent workspace directory and are
cloned automatically on first use; they are never worked in directly. Each
conversation instead gets its own checkout of every repo — a git worktree
on a branch dedicated to that conversation — so conversations can run
concurrently and edits accumulate across turns. Each turn's changes become
a commit pushed to the conversation's branch, which opens a pull request
the first time and updates it every time after.

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
class PullRequestUpdate:
    """A pull request opened or updated by one turn's edits."""
    repo_name: str
    url: str
    created: bool  # False when the turn added commits to an existing PR


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


def split_summary(prompt: str, summary: str) -> tuple[str, str]:
    """Split the agent's summary into a PR title and body.

    The agent is instructed to lead its summary with a commit-subject-style
    title line; the rest is the description. When the summary is missing
    (e.g. a timed-out turn), the prompt's first line stands in.
    """
    first, _, rest = summary.strip().partition('\n')
    title = first.strip('#*` ')  # tolerate heading/bold markup
    if title:
        return title, rest.strip()
    return prompt.strip().splitlines()[0], summary.strip()


def slugify(text: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return slug[:40].rstrip('-') or 'task'


class AgentWorkspace:
    """The pristine clones, and the conversation checkouts spawned off them."""

    def __init__(self, root: Path, repos: dict[str, AgentRepo],
                 github_token: str | None):
        self.root = root
        self.repos = repos
        self.github_token = github_token
        # Serializes clone-mutating operations (fetch, worktree add/remove)
        # per repo; conversations otherwise run fully in parallel.
        self._repo_locks = {name: asyncio.Lock() for name in repos}
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

    async def run_authed(self, argv: list[str], cwd: Path,
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

    async def run_git_authed(self, repo_dir: Path, *args: str) -> str:
        """Authed twin of _run_git, for git commands that talk to GitHub."""
        return await self.run_authed(['git', *self._auth_flags, *args],
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
        await self.run_git_authed(
            self.root, 'clone', '--depth', '1', '--single-branch',
            '-b', self.repos[name].base_branch,
            self._clone_url(name), str(partial),
        )
        partial.rename(self.root / name)

    async def create_checkout(self, root: Path,
                              prompt: str) -> 'ConversationCheckout':
        """Check out every repo under root as worktrees on a fresh branch
        named for the prompt."""
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        branch = f'{AGENT_BRANCH_PREFIX}/{slugify(prompt)}-{timestamp}'
        root.mkdir(parents=True, exist_ok=True)
        await asyncio.gather(*(
            self._add_worktree(name, root / name, branch)
            for name in self.repos))
        return ConversationCheckout(root, branch, self)

    def reopen_checkout(self, root: Path,
                        branch: str) -> 'ConversationCheckout':
        """Rebind a checkout that already exists on disk (state reload)."""
        return ConversationCheckout(root, branch, self)

    async def _add_worktree(self, name: str, path: Path, branch: str):
        repo_dir = self.root / name
        base = self.repos[name].base_branch
        async with self._repo_locks[name]:
            if path.exists():
                # Wreckage the conversation state doesn't know about (e.g.
                # a crash mid-creation); clear it and start over.
                await asyncio.to_thread(shutil.rmtree, path)
            await _run_git(repo_dir, 'worktree', 'prune')
            await self.run_git_authed(repo_dir, 'fetch', 'origin', '--prune')
            await _run_git(repo_dir, 'worktree', 'add', '-B', branch,
                           str(path), f'origin/{base}')

    async def remove_checkout(self, checkout: 'ConversationCheckout'):
        """Drop a conversation's worktrees and local branches.

        The branch and any pull request live on GitHub; only local state
        goes. Best-effort: a repo that fails to clean up is reported and
        skipped rather than stopping the rest.
        """
        for name in self.repos:
            repo_dir = self.root / name
            path = checkout.root / name
            async with self._repo_locks[name]:
                try:
                    if path.exists():
                        await _run_git(repo_dir, 'worktree', 'remove',
                                       '--force', str(path))
                    await _run_git(repo_dir, 'branch', '-D', checkout.branch)
                except WorkspaceError as error:
                    print(f'AgentWorkspace: cleaning up {path}: {error}')
        if checkout.root.exists():
            await asyncio.to_thread(shutil.rmtree, checkout.root,
                                    ignore_errors=True)


class ConversationCheckout:
    """One conversation's working copies: a worktree per repo, all on the
    conversation's branch. Edits accumulate here across turns — nothing is
    reset — and every turn's changes are pushed to the same branch, so each
    repo accrues at most one pull request per conversation."""

    def __init__(self, root: Path, branch: str, workspace: AgentWorkspace):
        self.root = root
        self.branch = branch
        self._workspace = workspace

    async def dirty_repos(self) -> list[str]:
        """Names of repos with uncommitted changes (the agent's edits)."""
        names = list(self._workspace.repos)
        statuses = await asyncio.gather(
            *(_run_git(self.root / name, 'status', '--porcelain')
              for name in names))
        return [name for name, status in zip(names, statuses)
                if status.strip()]

    async def publish_turn(self, name: str, prompt: str, summary: str,
                           pr_url: str | None) -> PullRequestUpdate:
        """Commit and push one repo's edits; open the pull request if the
        conversation doesn't have one for this repo yet."""
        repo = self._workspace.repos[name]
        repo_dir = self.root / name
        title, body = split_summary(prompt, summary)

        await _run_git(repo_dir, 'add', '-A')
        await _run_git(
            repo_dir,
            '-c', f'user.name={AGENT_COMMIT_NAME}',
            '-c', f'user.email={AGENT_COMMIT_EMAIL}',
            'commit', '-m', title[:72],
            '-m', f'Requested via {AGENT_REQUEST_SOURCE}:\n\n{prompt}',
        )
        await self._workspace.run_git_authed(
            repo_dir, 'push', '-u', 'origin', self.branch)

        if pr_url is not None:
            return PullRequestUpdate(repo_name=name, url=pr_url,
                                     created=False)
        url = await self._create_pull_request(repo, repo_dir, title, body)
        return PullRequestUpdate(repo_name=name, url=url, created=True)

    async def _create_pull_request(self, repo: AgentRepo, repo_dir: Path,
                                   title: str, body: str) -> str:
        body = body[:60000] or '(The agent did not leave a summary.)'
        body += ('\n\n---\nOpened by the JermaBot coding agent at the '
                 f'owner\'s request via {AGENT_REQUEST_SOURCE}.')
        # --body-file - takes the body on stdin, dodging argv size limits.
        output = await self._workspace.run_authed(
            ['gh', 'pr', 'create',
             '--repo', repo.slug,
             '--head', self.branch,
             '--base', repo.base_branch,
             '--title', title[:250],
             '--body-file', '-'],
            cwd=repo_dir,
            stdin_data=body,
        )
        # gh prints the new PR's URL as the last line of stdout.
        return output.strip().splitlines()[-1]
