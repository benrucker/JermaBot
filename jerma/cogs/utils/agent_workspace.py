"""Git and GitHub plumbing for the coding agent.

The agent itself has no Bash or network access, so this module owns every
git operation. Pristine clones live in a persistent workspace directory,
and ensure_repos() creates any that are missing. No conversation edits
those clones directly. Each one gets its own checkout of every repo
instead, a git worktree on a branch dedicated to that conversation, so
conversations can run concurrently and edits accumulate across turns.
Each turn's changes become a commit pushed to the conversation's branch,
which opens a pull request the first time and updates it every time after.

Checkouts are disposable. Naming a conversation's branch is separate from
putting worktrees on disk, and materialize() rebuilds them at any later
time. Materializing starts from the conversation's branch whenever origin
still has it, and falls back to the repo's base branch when it does not (a
brand new conversation, or one starting over because GitHub finished with
its branch).

prepare_for_turn() runs before every turn and holds the git continuity
rules (R3). It restarts a branch GitHub no longer has from the base,
rebuilds the worktrees, takes in whatever origin has on the branch that
this host does not (the owner resolving a conflict on GitHub, an "Update
branch" click, a push from anywhere else), and merges each repo's base
branch in so the agent edits current code and the pull request stays
mergeable. Catching up never fails a turn. A branch that will not fetch,
merge, or push comes back as a muted note for the thread (R3.7). Building
the checkout is stricter. A worktree that cannot be put on disk raises,
since there is then nothing for the agent to edit.

A conversation has one branch name across every repo but a pull request
per repo, so "the branch is gone" is ambiguous when repos disagree. The
conversation only takes a new branch name when no repo has the branch any
more. When some repos still have it the name stands, and only the repos
that lost it start over from their base. Their next push recreates the
branch there and opens a new pull request, while the repos still carrying
work keep theirs.

find_conversation_on_github() goes the other way, for a conversation whose
identity record this host no longer holds. The agent's pull requests carry
a fixed footer, and its first commit on a branch quotes the request that
started the thread, so a thread can find its own branch with nothing but
GitHub.

GitHub access goes through the gh CLI, which reads GITHUB_TOKEN from the
environment. Git authenticates with `gh auth git-credential` plugged in as
a per-command credential helper, and `gh pr create` opens the pull
requests. Nothing writes the token into the workspace, which the agent can
read. Without a token, git and gh fall back to whatever ambient
credentials the machine has (e.g. Git Credential Manager or a `gh auth
login` session on a dev box).
"""
import asyncio
import json
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
    AGENT_PR_FOOTER,
    AGENT_REQUEST_SOURCE,
    AgentRepo,
)

_CREDENTIAL_HELPER = '!gh auth git-credential'


class WorkspaceError(Exception):
    """A workspace problem the owner needs to hear about verbatim."""


@dataclass
class TurnPreparation:
    """What getting a checkout ready for one turn turned up.

    `notes` are finished Discord subtext lines, in the muted R4.3 style,
    for the thread to show as-is. `finished_repos` are repos whose pull
    request GitHub is done with, so the conversation forgets its url and
    opens a new one with its next edit (R3.4).
    """
    notes: list[str]
    finished_repos: list[str]


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
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as error:
        # A directory that went away under us, or a missing executable.
        # The process never started, so there is no exit code to report.
        # Wrapping it here keeps the cause out of a bare traceback.
        raise WorkspaceError(
            f'`{argv[0]}` could not be run in {cwd}: '
            f'{one_line(error)}') from error
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


def one_line(error: object) -> str:
    """An error as one short line.

    Notes go out as Discord subtext, and git likes to answer in
    paragraphs.
    """
    return ' '.join(str(error).split())[:300]


async def _merge_in_progress(repo_dir: Path) -> bool:
    """Whether a merge is half-finished here (git left MERGE_HEAD)."""
    try:
        await _run_git(repo_dir, 'rev-parse', '-q', '--verify', 'MERGE_HEAD')
    except WorkspaceError:
        return False
    return True


async def _is_ancestor(repo_dir: Path, older: str, newer: str) -> bool:
    """Whether older is reachable from newer (so newer fast-forwards)."""
    try:
        await _run_git(repo_dir, 'merge-base', '--is-ancestor', older, newer)
    except WorkspaceError:
        return False
    return True


def _timestamp() -> str:
    return datetime.now().strftime('%Y%m%d-%H%M%S')


def _quotes_prompt(commits: list[dict], prompt: str) -> bool:
    """Whether the branch's first commit quotes this prompt.

    Every turn commits the request that caused it (`Requested via
    Discord:`), so the first commit on the branch carries the message that
    started the thread. For pull requests opened before the thread line,
    that is the only tie back to the thread.
    """
    if not commits:
        return False
    return prompt in (commits[0].get('messageBody') or '')


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
        # per repo; conversations otherwise run in parallel.
        self._repo_locks = {name: asyncio.Lock() for name in repos}
        if github_token:
            # The empty helper clears any helpers already configured
            # (e.g. a credential manager) so git asks only gh, which
            # holds the token.
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
        """Run a command that talks to GitHub, with a hint on auth trouble."""
        try:
            return await _run(argv, cwd=cwd, env=self._auth_env,
                              stdin_data=stdin_data)
        except WorkspaceError as e:
            if self.github_token:
                raise
            raise WorkspaceError(
                f'{e} (GITHUB_TOKEN is not set. Set it, or run '
                '`gh auth login` on this machine.)'
            ) from e

    async def run_git_authed(self, repo_dir: Path, *args: str) -> str:
        """Authed twin of _run_git, for git commands that talk to GitHub."""
        return await self.run_authed(['git', *self._auth_flags, *args],
                                     cwd=repo_dir)

    async def fetch(self, name: str, cwd: Path, *refspecs: str):
        """Fetch into a repo, one caller at a time.

        Worktrees share their pristine clone's refs, so two conversations
        fetching the same repo at once race over them. Callers already
        holding the repo's lock (the worktree builder) must not come
        through here.
        """
        async with self._repo_locks[name]:
            await self.run_git_authed(cwd, 'fetch', 'origin', *refspecs)

    async def branch_on_origin(self, name: str, branch: str) -> bool:
        """Whether origin still has this branch.

        Both resuming a conversation and noticing that GitHub merged it
        away come down to this question.
        """
        heads = await self.run_git_authed(
            self.root / name, 'ls-remote', '--heads', 'origin',
            f'refs/heads/{branch}')
        return bool(heads.strip())

    async def pull_request_state(self, url: str) -> dict:
        """A pull request's state, as gh reports it.

        mergedAt holds a time once GitHub merges it; state is OPEN,
        CLOSED or MERGED.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        output = await self.run_authed(
            ['gh', 'pr', 'view', url, '--json', 'state,mergedAt'],
            cwd=self.root)
        return json.loads(output)

    async def branch_for_pull_request(self, url: str) -> str:
        """The head branch of a pull request.

        A conversation recovers its branch this way, from a pull request
        announced in its thread (R3.3).
        """
        self.root.mkdir(parents=True, exist_ok=True)
        output = await self.run_authed(
            ['gh', 'pr', 'view', url, '--json', 'headRefName'],
            cwd=self.root)
        return json.loads(output).get('headRefName') or ''

    async def pull_request_commits(self, url: str) -> list[dict]:
        """The commits on a pull request's branch, newest last.

        One pull request at a time, on purpose. `gh pr list` can return
        commits too, but GitHub prices that per pull request listed and
        refuses the query long before the hundred the search asks for.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        output = await self.run_authed(
            ['gh', 'pr', 'view', url, '--json', 'commits'], cwd=self.root)
        return json.loads(output).get('commits') or []

    async def find_conversation_on_github(
            self, starter_prompt: str) -> tuple[str, dict[str, str]] | None:
        """The branch and pull requests of a conversation, from GitHub.

        This is how a host holding no record of a conversation finds it
        again (R3.3). None if nothing matches.

        Candidates are the agent's own pull requests, the ones whose head
        branch carries the agent's prefix and whose body carries the
        agent's footer. One belongs to this thread if the first commit the
        agent made on the branch quotes the request that started the
        thread. That test needs the branch's commits, asked for one
        candidate at a time. A listing that includes them costs GitHub a
        commit query per pull request, and GitHub rejects the query
        outright over about forty-five of them.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        matches = await asyncio.gather(*(
            self._find_pull_request(name, starter_prompt)
            for name in self.repos))
        found = [(name, match) for name, match in zip(self.repos, matches)
                 if match is not None]
        if not found:
            return None
        pr_urls = {name: match['url'] for name, match in found}
        # Repos can disagree when the conversation restarted its branch in
        # one of them (R3.4); the newest pull request has the live name.
        newest = max(found, key=lambda item: item[1]['createdAt'])
        return newest[1]['headRefName'], pr_urls

    async def _find_pull_request(self, name: str,
                                 starter_prompt: str) -> dict | None:
        """This thread's most recent pull request in one repo, per gh."""
        output = await self.run_authed(
            ['gh', 'pr', 'list',
             '--repo', self.repos[name].slug,
             '--state', 'all',
             '--search', f'head:{AGENT_BRANCH_PREFIX}/ sort:created-desc',
             '--json', 'url,headRefName,body,createdAt',
             '--limit', '100'],
            cwd=self.root,
        )
        prompt = starter_prompt.strip()
        # The search asks for newest first, so the first match is the
        # live one even when the branch has restarted (R3.4).
        for pull_request in json.loads(output):
            head = pull_request.get('headRefName') or ''
            body = pull_request.get('body') or ''
            if not head.startswith(f'{AGENT_BRANCH_PREFIX}/'):
                continue
            if AGENT_PR_FOOTER not in body:
                continue
            if prompt and _quotes_prompt(
                    await self.pull_request_commits(pull_request['url']),
                    prompt):
                return pull_request
        return None

    def restart_branch(self, branch: str) -> str:
        """A fresh branch for a conversation GitHub is done with.

        Same slug, stamped with the current time, so a thread's branches
        sort together.
        """
        stem = re.sub(r'-[0-9]{8}-[0-9]{6}$', '', branch)
        return f'{stem}-{_timestamp()}'

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
        """Clone a repo atomically, leaving no half-cloned directory.

        Full depth on purpose. Conversation branches get fetched into
        these clones later, and a shallow base shares no ancestor with
        them, so no merge could ever succeed. Disk is not a concern.
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
        """Name a new conversation's checkout, with a branch of its own.

        The checkout lives under root. Nothing is on disk yet;
        materialize() puts it there.
        """
        branch = f'{AGENT_BRANCH_PREFIX}/{slugify(prompt)}-{_timestamp()}'
        return ConversationCheckout(root, branch, self)

    async def materialize(self, checkout: 'ConversationCheckout',
                          on_origin: dict[str, bool] | None = None):
        """(Re)create only the worktrees the checkout is missing.

        Repo by repo. A half-built checkout (one repo failed, or
        ensure_repos re-cloned one repo's pristine clone) must not cost
        the repos that are fine, and rebuilding a healthy worktree would
        throw away edits the agent has not committed yet.

        on_origin says, per repo, whether origin already has the branch. A
        caller that has just asked (prepare_for_turn) passes its answer to
        save a second ls-remote.
        """
        checkout.root.mkdir(parents=True, exist_ok=True)
        await asyncio.gather(*(
            self._add_worktree(name, checkout.root / name, checkout.branch,
                               (on_origin or {}).get(name))
            for name in checkout.stale_repos()))

    async def _add_worktree(self, name: str, path: Path, branch: str,
                            on_origin: bool | None = None):
        repo_dir = self.root / name
        async with self._repo_locks[name]:
            if path.exists():
                # Wreckage from a crash mid-creation, or a directory
                # orphaned by its pristine clone. The branch on origin is
                # the truth, so rebuild the directory from it.
                await asyncio.to_thread(shutil.rmtree, path)
            await _run_git(repo_dir, 'worktree', 'prune')
            start_point = await self._fetch_start_point(name, repo_dir,
                                                        branch, on_origin)
            await _run_git(repo_dir, 'worktree', 'add', '-B', branch,
                           str(path), start_point)

    async def _fetch_start_point(self, name: str, repo_dir: Path,
                                 branch: str,
                                 on_origin: bool | None = None) -> str:
        """Fetch, and return the ref the worktree should start at.

        That is the conversation's own branch when origin still has it,
        otherwise the repo's base. The pristine clones are single-branch,
        so the conversation's branch needs an explicit refspec to reach
        them. on_origin, when the caller already knows it, saves an
        ls-remote.
        """
        base = self.repos[name].base_branch
        # The caller already holds the repo's lock, so this fetch goes
        # straight out rather than through fetch().
        await self.run_git_authed(repo_dir, 'fetch', 'origin', '--prune')
        if on_origin is None:
            on_origin = await self.branch_on_origin(name, branch)
        if not on_origin:
            return f'origin/{base}'
        await self.run_git_authed(
            repo_dir, 'fetch', 'origin',
            f'+refs/heads/{branch}:refs/remotes/origin/{branch}')
        return f'origin/{branch}'


@dataclass
class ConversationCheckout:
    """One conversation's working copies, one worktree per repo.

    Every worktree sits on the conversation's branch. Edits accumulate
    here across turns. Nothing resets them, and every turn pushes its
    changes to the same branch, so a repo keeps the same pull request for
    as long as that branch lives. A repo whose pull request GitHub has
    finished with starts a fresh branch, and its next edit opens a new one
    (R3.4).

    The directories are only a cache. This object means something without
    them (root and branch are all the conversation record keeps), and
    prepare_for_turn rebuilds them from origin before a turn runs.
    """
    root: Path
    branch: str
    workspace: AgentWorkspace

    def is_materialized(self) -> bool:
        """Whether every repo has a usable worktree on disk right now."""
        return not self.stale_repos()

    def stale_repos(self) -> list[str]:
        """Repos whose worktree has to be (re)built.

        That covers a worktree never created, one deleted, and one
        orphaned. A worktree's `.git` is a file pointing back into its
        pristine clone, so wiping that clone (and maybe re-cloning it)
        leaves directories here pointing at nothing.
        """
        return [name for name in self.workspace.repos
                if not _worktree_is_live(self.root / name)]

    async def ensure_materialized(self,
                                  on_origin: dict[str, bool] | None = None):
        if not self.is_materialized():
            await self.workspace.materialize(self, on_origin)

    async def prepare_for_turn(self,
                               pr_urls: dict[str, str]) -> TurnPreparation:
        """Get the checkout ready for one turn, and say what happened.

        In order: restart a branch GitHub no longer has from the base
        (R3.4), rebuild any missing worktrees, then catch every repo up,
        first with its own branch as origin has it and then with its base
        branch (R3.5). The caller applies the result, dropping the pull
        request urls of `finished_repos` so the next edit opens new ones,
        and posts the notes.

        This asks once whether origin has the branch, then reuses the
        answer. A restart already leaves every repo without the new branch
        on origin, so the map stays true for the name the rest of the turn
        uses.
        """
        on_origin = await self._branch_on_origin()
        notes, finished = await self._restart_finished_branch(on_origin,
                                                              pr_urls)
        await self.ensure_materialized(on_origin)
        notes += await self._catch_up(on_origin)
        return TurnPreparation(notes=notes, finished_repos=finished)

    async def _branch_on_origin(self) -> dict[str, bool]:
        """Which repos still have this conversation's branch on origin."""
        names = list(self.workspace.repos)
        found = await asyncio.gather(*(
            self.workspace.branch_on_origin(name, self.branch)
            for name in names))
        return dict(zip(names, found))

    async def _restart_finished_branch(
            self, on_origin: dict[str, bool],
            pr_urls: dict[str, str]) -> tuple[list[str], list[str]]:
        """Start over from the base where GitHub finished with the branch.

        Finished means GitHub merged or closed the pull request and
        deleted the branch with it (R3.4). A repo that never had a pull
        request is not finished, only never pushed, so this leaves it
        alone and lets it start from the base as it always would. The
        branch name itself changes only when no repo has it any more.
        While any repo still carries the conversation's work, the name it
        knows stands and only the finished repos start over; their next
        push recreates the branch there and opens a new pull request.
        """
        finished = [name for name, present in on_origin.items()
                    if not present and pr_urls.get(name)]
        if not finished:
            return [], []

        notes = [await self._finished_note(name, pr_urls[name])
                 for name in finished]
        nowhere_left = not any(on_origin.values())
        restart = list(self.workspace.repos) if nowhere_left else finished
        if nowhere_left:
            self.branch = self.workspace.restart_branch(self.branch)
        # Drop the worktrees that must start over. The branch they hold
        # is either finished or renamed, and ensure_materialized rebuilds
        # a stale worktree from the base.
        for name in restart:
            path = self.root / name
            if path.exists():
                await asyncio.to_thread(shutil.rmtree, path)
        return notes, finished

    async def _finished_note(self, name: str, pr_url: str) -> str:
        """One muted line explaining why this repo starts over (R3.4)."""
        try:
            state = await self.workspace.pull_request_state(pr_url)
        except (WorkspaceError, ValueError) as error:
            # Never worth failing a turn over. The branch is gone either
            # way, so say so and start over.
            return (f'-# _Couldn\'t check the pull request for **{name}** '
                    f'({one_line(error)}); starting a fresh branch._')
        if state.get('mergedAt'):
            outcome = 'was merged'
        elif state.get('state') == 'CLOSED':
            outcome = 'was closed'
        else:
            outcome = 'is gone from GitHub'
        return (f'-# _The pull request for **{name}** {outcome}; starting a '
                'fresh branch, and a new pull request with the next edit._')

    async def _catch_up(self, on_origin: dict[str, bool]) -> list[str]:
        """Bring every repo up to date before the agent runs (R3.5).

        Returns a note for each repo that would not catch up. Never
        raises. A stale branch is worth a muted line, not a dropped
        message (R3.7).
        """
        notes = await asyncio.gather(*(
            self._catch_up_repo(name, on_origin.get(name, False))
            for name in self.workspace.repos))
        return [note for note in notes if note]

    async def _catch_up_repo(self, name: str, on_origin: bool) -> str | None:
        """Catch one repo up, then push what the merges produced.

        First with the branch as origin has it, then with the base branch.
        The push happens only when origin has the branch, and it keeps the
        pull request mergeable.
        """
        repo_dir = self.root / name
        base = self.workspace.repos[name].base_branch
        try:
            refspecs = [f'+refs/heads/{base}:refs/remotes/origin/{base}']
            if on_origin:
                refspecs.append(f'+refs/heads/{self.branch}'
                                f':refs/remotes/origin/{self.branch}')
            await self.workspace.fetch(name, repo_dir, *refspecs)
            before = await _run_git(repo_dir, 'rev-parse', 'HEAD')
            if on_origin:
                conflicts = await self._take_in_origin_branch(repo_dir)
                if conflicts:
                    return (f"-# _Couldn't catch **{name}** up with its "
                            f'branch on GitHub: conflicts in '
                            f'{", ".join(conflicts)}._')
            conflicts = await self._merge(repo_dir, f'origin/{base}')
            if conflicts:
                return (f"-# _Couldn't merge {base} into this branch: "
                        f'conflicts in {", ".join(conflicts)}._')
            after = await _run_git(repo_dir, 'rev-parse', 'HEAD')
            if on_origin and after != before:
                await self.workspace.run_git_authed(
                    repo_dir, 'push', 'origin',
                    f'HEAD:refs/heads/{self.branch}')
        except WorkspaceError as error:
            # Either phase can land here, so the wording names neither.
            return (f"-# _Couldn't catch **{name}** up: "
                    f'{one_line(error)}._')
        return None

    async def _take_in_origin_branch(self, repo_dir: Path) -> list[str]:
        """Take in what origin has on this branch and this worktree lacks.

        That covers the owner resolving a conflict on GitHub, an "Update
        branch" click, and a push from another host. Skip it and git
        rejects the next push, leaving the owner's own fix invisible.

        Never a reset. Uncommitted edits here are a publish that failed
        earlier, and the next one still owes them to the pull request. So
        this fast-forwards when it can and merges when the two diverged,
        with the same conflict handling as the base merge.
        """
        remote = f'origin/{self.branch}'
        head = (await _run_git(repo_dir, 'rev-parse', 'HEAD')).strip()
        tip = (await _run_git(repo_dir, 'rev-parse', remote)).strip()
        if head == tip or await _is_ancestor(repo_dir, tip, head):
            return []  # nothing there this worktree hasn't got
        if await _is_ancestor(repo_dir, head, tip):
            await _run_git(repo_dir, 'merge', '--ff-only', remote)
            return []
        return await self._merge(repo_dir, remote)

    async def _merge(self, repo_dir: Path, ref: str) -> list[str]:
        """Merge ref into the checkout with the agent's identity.

        Returns the conflicting paths, having left the branch exactly as
        the merge found it (R3.6). An empty list means the merge landed.
        """
        try:
            await _run_git(
                repo_dir,
                '-c', f'user.name={AGENT_COMMIT_NAME}',
                '-c', f'user.email={AGENT_COMMIT_EMAIL}',
                'merge', '--no-edit', ref)
        except WorkspaceError as error:
            return await self._abort_merge(repo_dir, error)
        return []

    async def _abort_merge(self, repo_dir: Path,
                           error: WorkspaceError) -> list[str]:
        """Undo a merge that failed, and name the files it stumbled on.

        A failure that is not a conflict goes back to the caller as
        itself, but only once the merge is unwound, since the next commit
        would otherwise conclude a half-finished merge.
        """
        conflicts = [line for line in (await _run_git(
            repo_dir, 'diff', '--name-only', '--diff-filter=U')).splitlines()
            if line.strip()]
        if await _merge_in_progress(repo_dir):
            await _run_git(repo_dir, 'merge', '--abort')
        if not conflicts:
            raise error
        return conflicts

    async def publish_turn(self, prompt: str, title: str, body: str,
                           pr_urls: dict[str, str]) -> list[PullRequestUpdate]:
        """Publish every repo the agent edited, in parallel.

        Each one gets a commit, a push, and a pull request opened or
        updated.
        """
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
        """Commit and push one repo's edits.

        Opens the pull request as well when the conversation has none for
        this repo yet.
        """
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
        body += f'\n\n---\n{AGENT_PR_FOOTER}'
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
