"""Git and GitHub plumbing for the coding agent.

The agent itself has no Bash or network access; this module owns every git
operation. Pristine clones live in a persistent workspace directory and are
cloned automatically on first use; they are never worked in directly. Each
conversation instead gets its own checkout of every repo — a git worktree
on a branch dedicated to that conversation — so conversations can run
concurrently and edits accumulate across turns. Each turn's changes become
a commit pushed to the conversation's branch, which opens a pull request
the first time and updates it every time after.

Checkouts are disposable: naming a conversation's branch is separate from
putting worktrees on disk, and materialize() rebuilds them at any later
time. A conversation never restarts its branch, so materializing starts
from the branch itself whenever it still exists on origin, and only falls
back to the repo's base branch when it does not (a brand new conversation,
or one whose branch was merged and deleted).

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


def _worktree_is_live(path: Path) -> bool:
    """Whether path is a git worktree whose repository is still there."""
    link = path / '.git'  # a worktree's .git is a file, not a directory
    if not link.is_file():
        return False
    gitdir = link.read_text(encoding='utf-8').partition('gitdir:')[2].strip()
    if not gitdir:
        return False
    # git writes the gitdir absolute by default, but relative to the worktree
    # under worktree.useRelativePaths; never resolve it against the CWD.
    gitdir_path = Path(gitdir)
    if not gitdir_path.is_absolute():
        gitdir_path = path / gitdir_path
    return gitdir_path.exists()


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
        # Serializes clone-mutating operations (fetch, worktree add)
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
        """Clone a repo, atomically: no half-cloned dir survives.

        Full depth on purpose: conversation branches are fetched into these
        clones later, and a shallow base has no ancestor in common with
        them, so nothing could ever be merged. Disk is not a concern.
        """
        partial = self.root / f'{name}.cloning'
        if partial.exists():
            await asyncio.to_thread(shutil.rmtree, partial)
        await self.run_git_authed(
            self.root, 'clone', '--single-branch',
            '-b', self.repos[name].base_branch,
            self._clone_url(name), str(partial),
        )
        partial.rename(self.root / name)

    def new_checkout(self, root: Path, prompt: str) -> 'ConversationCheckout':
        """Name a new conversation's checkout: a branch of its own, under
        root. Nothing is on disk yet; materialize() puts it there."""
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        branch = f'{AGENT_BRANCH_PREFIX}/{slugify(prompt)}-{timestamp}'
        return ConversationCheckout(root, branch, self)

    async def materialize(self, checkout: 'ConversationCheckout'):
        """(Re)create only the worktrees the checkout is missing.

        Repo by repo: a half-built checkout (one repo failed, or the
        pristine clone of one repo was re-cloned) must not cost the repos
        that are fine — worse, rebuilding a healthy worktree would throw
        away edits the agent has not committed yet.
        """
        checkout.root.mkdir(parents=True, exist_ok=True)
        await asyncio.gather(*(
            self._add_worktree(name, checkout.root / name, checkout.branch)
            for name in checkout.stale_repos()))

    async def _add_worktree(self, name: str, path: Path, branch: str):
        repo_dir = self.root / name
        async with self._repo_locks[name]:
            if path.exists():
                # Wreckage: a crash mid-creation, or a directory orphaned
                # by its pristine clone. The branch on origin is the truth,
                # so the directory is rebuilt from it.
                await asyncio.to_thread(shutil.rmtree, path)
            await _run_git(repo_dir, 'worktree', 'prune')
            start_point = await self._fetch_start_point(name, repo_dir, branch)
            await _run_git(repo_dir, 'worktree', 'add', '-B', branch,
                           str(path), start_point)

    async def _fetch_start_point(self, name: str, repo_dir: Path,
                                 branch: str) -> str:
        """Fetch, and return the ref the worktree should start at: the
        conversation's own branch when origin still has it, else the base.

        The pristine clones are single-branch, so the conversation's branch
        needs an explicit refspec to reach them.
        """
        base = self.repos[name].base_branch
        await self.run_git_authed(repo_dir, 'fetch', 'origin', '--prune')
        heads = await self.run_git_authed(repo_dir, 'ls-remote', '--heads',
                                          'origin', f'refs/heads/{branch}')
        if not heads.strip():
            return f'origin/{base}'
        await self.run_git_authed(
            repo_dir, 'fetch', 'origin',
            f'+refs/heads/{branch}:refs/remotes/origin/{branch}')
        return f'origin/{branch}'


@dataclass
class ConversationCheckout:
    """One conversation's working copies: a worktree per repo, all on the
    conversation's branch. Edits accumulate here across turns — nothing is
    reset — and every turn's changes are pushed to the same branch, so each
    repo accrues at most one pull request per conversation.

    The directories are a cache: this object is meaningful without them
    (root and branch are all the conversation record keeps) and
    ensure_materialized rebuilds them from origin before a turn runs."""
    root: Path
    branch: str
    workspace: AgentWorkspace

    def is_materialized(self) -> bool:
        """Whether every repo has a usable worktree on disk right now."""
        return not self.stale_repos()

    def stale_repos(self) -> list[str]:
        """Repos whose worktree has to be (re)built: never created, deleted,
        or orphaned. A worktree's `.git` is a file pointing back into its
        pristine clone, so a clone that was wiped (and possibly re-cloned)
        leaves directories here pointing at nothing."""
        return [name for name in self.workspace.repos
                if not _worktree_is_live(self.root / name)]

    async def ensure_materialized(self):
        if not self.is_materialized():
            await self.workspace.materialize(self)

    async def publish_turn(self, prompt: str, title: str, body: str,
                           pr_urls: dict[str, str]) -> list[PullRequestUpdate]:
        """Commit, push, and open or update a pull request for every repo
        the agent edited, in parallel."""
        return await asyncio.gather(*(
            self._publish_repo(name, prompt, title, body,
                               pr_url=pr_urls.get(name))
            for name in await self._dirty_repos()))

    async def _dirty_repos(self) -> list[str]:
        """Names of repos with uncommitted changes (the agent's edits)."""
        names = list(self.workspace.repos)
        statuses = await asyncio.gather(
            *(_run_git(self.root / name, 'status', '--porcelain')
              for name in names))
        return [name for name, status in zip(names, statuses)
                if status.strip()]

    async def _publish_repo(self, name: str, prompt: str, title: str,
                            body: str, pr_url: str | None) -> PullRequestUpdate:
        """Commit and push one repo's edits; open the pull request if the
        conversation doesn't have one for this repo yet."""
        repo_dir = self.root / name

        await _run_git(repo_dir, 'add', '-A')
        await _run_git(
            repo_dir,
            '-c', f'user.name={AGENT_COMMIT_NAME}',
            '-c', f'user.email={AGENT_COMMIT_EMAIL}',
            'commit', '-m', title[:72],
            '-m', f'Requested via {AGENT_REQUEST_SOURCE}:\n\n{prompt}',
        )
        await self.workspace.run_git_authed(
            repo_dir, 'push', '-u', 'origin', self.branch)

        if pr_url is not None:
            return PullRequestUpdate(repo_name=name, url=pr_url,
                                     created=False)
        url = await self._create_pull_request(self.workspace.repos[name],
                                              repo_dir, title, body)
        return PullRequestUpdate(repo_name=name, url=url, created=True)

    async def _create_pull_request(self, repo: AgentRepo, repo_dir: Path,
                                   title: str, body: str) -> str:
        body = body[:60000] or '(The agent did not leave a summary.)'
        body += ('\n\n---\nOpened by the JermaBot coding agent at the '
                 f'owner\'s request via {AGENT_REQUEST_SOURCE}.')
        # --body-file - takes the body on stdin, dodging argv size limits.
        output = await self.workspace.run_authed(
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
