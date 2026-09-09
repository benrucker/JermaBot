"""Lazy materialization and per-turn git continuity, against real git repos.

A local bare repo stands in for GitHub: the point of these tests is that a
conversation's worktrees can be thrown away and rebuilt from its branch,
which is what makes a conversation survive losing its host. The one thing
these repos cannot play is a pull request, so the two prepare_for_turn
tests that need one stub gh at run_authed, the single place the workspace
shells out to it.
"""
import json
import os
import shutil
import stat
import subprocess

import pytest

from cogs.utils.agent_config import AgentRepo
from cogs.utils.agent_workspace import (
    AgentWorkspace,
    ConversationCheckout,
    WorkspaceError,
    _run,
)

# An old branch, so a restart's fresh timestamp cannot collide with it.
BRANCH = 'jermabot/do-a-thing-20260101-000000'
PR_URL = 'https://github.com/local/demo/pull/1'
MERGED = {'state': 'MERGED', 'mergedAt': '2026-09-01T12:00:00Z'}

GIT_IDENTITY = ['-c', 'user.name=Test', '-c', 'user.email=test@test.invalid']


def rmtree(path):
    """shutil.rmtree, minus Windows' read-only pack files."""
    def force(func, target, _):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onerror=force)


def git(cwd, *args) -> str:
    result = subprocess.run(['git', *GIT_IDENTITY, *args], cwd=str(cwd),
                            capture_output=True, text=True)
    assert result.returncode == 0, f'git {args} failed: {result.stderr}'
    return result.stdout


def land_on_main(tmp_path, origin, filename: str = 'later.txt'):
    """Someone else's commit on the base branch."""
    clone = tmp_path / f'clone-{filename}'
    git(tmp_path, 'clone', str(origin), str(clone))
    commit_and_push(clone, filename, 'later\n', 'refs/heads/main')


def make_origin(tmp_path, name: str = 'origin'):
    """A bare repo with one commit on `main`, playing the part of GitHub."""
    seed = tmp_path / f'seed-{name}'
    seed.mkdir()
    git(seed, '-c', 'init.defaultBranch=main', 'init')
    (seed / 'README.md').write_text('base\n', encoding='utf-8')
    git(seed, 'add', '-A')
    git(seed, 'commit', '-m', 'initial')
    bare = tmp_path / f'{name}.git'
    git(tmp_path, 'clone', '--bare', str(seed), str(bare))
    return bare


@pytest.fixture
def origin(tmp_path):
    return make_origin(tmp_path)


def stub_gh(workspace, payload: dict):
    """Answer every gh call with one canned payload, leaving git alone."""
    real = workspace.run_authed

    async def stubbed(argv, cwd, stdin_data=None):
        if argv[0] == 'gh':
            return json.dumps(payload)
        return await real(argv, cwd, stdin_data)

    workspace.run_authed = stubbed


def remote_tip(tmp_path, origin, branch: str) -> str:
    """What origin has for a branch, or '' if it has none."""
    line = git(tmp_path, 'ls-remote', str(origin), f'refs/heads/{branch}')
    return line.split()[0] if line.strip() else ''


def make_workspace(tmp_path, origin, *names) -> AgentWorkspace:
    workspace = AgentWorkspace(
        root=tmp_path / 'repos',
        repos={name: AgentRepo(f'local/{name}', 'main') for name in names},
        github_token=None)
    # A file:// URL, not a plain path: git quietly ignores clone flags on a
    # local path, and these tests exist to exercise the real clone.
    workspace._clone_url = lambda name: origin.as_uri()
    return workspace


@pytest.fixture
def workspace(tmp_path, origin):
    return make_workspace(tmp_path, origin, 'demo')


@pytest.fixture
def two_repo_workspace(tmp_path, origin):
    return make_workspace(tmp_path, origin, 'demo', 'other')


def commit(repo, filename: str, text: str):
    """A commit that stays on this host, as a failed push would leave it."""
    (repo / filename).write_text(text, encoding='utf-8')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-m', f'add {filename}')


def commit_and_push(repo, filename: str, text: str, ref: str):
    """One turn's work, published the way publish_turn publishes it."""
    commit(repo, filename, text)
    git(repo, 'push', 'origin', f'HEAD:{ref}')


def clone_of_the_branch(tmp_path, origin, name: str = 'elsewhere'):
    """Someone else's checkout of the conversation's branch — the owner
    resolving a conflict on GitHub, another host, a manual push."""
    clone = tmp_path / name
    git(tmp_path, 'clone', str(origin), str(clone))
    git(clone, 'checkout', BRANCH)
    return clone


async def test_a_command_that_never_starts_names_its_cause(tmp_path):
    """A worktree deleted under a running turn takes the working directory
    with it, and the process then never starts — no exit code to report.
    The owner still gets a cause rather than a raw OSError."""
    with pytest.raises(WorkspaceError, match='`git` could not be run'):
        await _run(['git', 'status'], cwd=tmp_path / 'not-a-directory')


async def test_materialize_starts_a_new_branch_from_the_base(workspace,
                                                             tmp_path):
    await workspace.ensure_repos()
    checkout = workspace.new_checkout(tmp_path / 'conv' / '1', 'do a thing')

    assert not checkout.is_materialized()
    await checkout.ensure_materialized()

    assert checkout.is_materialized()
    repo = checkout.root / 'demo'
    assert (repo / 'README.md').read_text(encoding='utf-8') == 'base\n'
    assert git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip() \
        == checkout.branch


async def test_materialize_resumes_the_conversations_own_branch(workspace,
                                                                tmp_path):
    """The scenario this feature exists for: the checkout is gone, the
    branch is not. The rebuilt worktree must carry the earlier turn's work
    rather than starting over from the base."""
    await workspace.ensure_repos()
    checkout = workspace.new_checkout(tmp_path / 'conv' / '1', 'do a thing')
    await checkout.ensure_materialized()

    # An earlier turn's edit, committed and pushed as publish_turn would.
    repo = checkout.root / 'demo'
    commit_and_push(repo, 'turn.txt', 'turn one\n',
                    f'refs/heads/{checkout.branch}')

    # The host loses the checkout: swept, reimaged, whatever.
    subprocess.run(['git', 'worktree', 'remove', '--force', str(repo)],
                   cwd=str(workspace.root / 'demo'), capture_output=True)
    assert not checkout.is_materialized()

    await checkout.ensure_materialized()

    assert (repo / 'turn.txt').read_text(encoding='utf-8') == 'turn one\n'
    assert git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip() \
        == checkout.branch


async def test_materialize_survives_a_directory_deleted_under_it(workspace,
                                                                 tmp_path):
    """A deleted directory leaves git's worktree metadata behind; the
    rebuild has to prune it rather than fail."""
    await workspace.ensure_repos()
    checkout = workspace.new_checkout(tmp_path / 'conv' / '1', 'do a thing')
    await checkout.ensure_materialized()
    rmtree(checkout.root)

    await checkout.ensure_materialized()

    assert checkout.is_materialized()


async def test_a_branch_gone_from_origin_falls_back_to_the_base(workspace,
                                                                tmp_path,
                                                                origin):
    """Nothing was ever pushed for this branch (a conversation of questions
    only, or a merged-and-deleted branch), so the base is the start point —
    including commits landed since."""
    await workspace.ensure_repos()
    checkout = workspace.new_checkout(tmp_path / 'conv' / '1', 'do a thing')

    # Someone lands a commit on main after the conversation started.
    land_on_main(tmp_path, origin)

    await checkout.ensure_materialized()

    assert (checkout.root / 'demo' / 'later.txt').exists()


async def test_materialize_is_a_no_op_when_the_worktrees_are_there(workspace,
                                                                   tmp_path):
    """A live conversation's uncommitted edits are not blown away by the
    next turn."""
    await workspace.ensure_repos()
    checkout = workspace.new_checkout(tmp_path / 'conv' / '1', 'do a thing')
    await checkout.ensure_materialized()
    scratch = checkout.root / 'demo' / 'scratch.txt'
    scratch.write_text('in progress\n', encoding='utf-8')

    await checkout.ensure_materialized()

    assert scratch.exists()


async def test_the_base_merges_into_a_recovered_branch(workspace, tmp_path,
                                                       origin):
    """What every turn does before running (R3.5), in the shape recovery
    hands it: a branch pushed by a host that is gone, a base that moved on
    since, and a clone made after both. A shallow pristine clone made this
    impossible — its base tip is grafted to no parents, so the branch and
    the base share no ancestor git can see and the merge is refused as
    unrelated histories."""
    checkout = workspace.new_checkout(tmp_path / 'conv' / '1', 'do a thing')

    # The conversation's earlier turn, from a host that no longer exists.
    old_host = tmp_path / 'old-host'
    git(tmp_path, 'clone', str(origin), str(old_host))
    commit_and_push(old_host, 'turn.txt', 'turn one\n',
                    f'refs/heads/{checkout.branch}')
    land_on_main(tmp_path, origin)

    # This host has never seen the repo before.
    await workspace.ensure_repos()
    await checkout.ensure_materialized()

    repo = checkout.root / 'demo'
    git(repo, 'fetch', 'origin')
    git(repo, 'merge', 'origin/main', '-m', 'merge main')

    assert (repo / 'turn.txt').exists()
    assert (repo / 'later.txt').exists()


async def test_only_the_missing_repos_are_rebuilt(two_repo_workspace,
                                                  tmp_path):
    """A repo that needs rebuilding must not cost the others the work they
    are holding: gather does not cancel its siblings, so half-built
    checkouts happen."""
    workspace = two_repo_workspace
    await workspace.ensure_repos()
    checkout = workspace.new_checkout(tmp_path / 'conv' / '1', 'do a thing')
    await checkout.ensure_materialized()
    in_progress = checkout.root / 'demo' / 'uncommitted.txt'
    in_progress.write_text('the agent was mid-turn\n', encoding='utf-8')
    rmtree(checkout.root / 'other')

    assert checkout.stale_repos() == ['other']
    await checkout.ensure_materialized()

    assert checkout.is_materialized()
    assert in_progress.exists()


async def test_a_wiped_clone_makes_the_checkout_stale(workspace, tmp_path):
    """The worktrees point into the pristine clones; losing those leaves
    directories that look present and are not usable."""
    await workspace.ensure_repos()
    checkout = workspace.new_checkout(tmp_path / 'conv' / '1', 'do a thing')
    await checkout.ensure_materialized()

    rmtree(workspace.root)

    assert (checkout.root / 'demo' / '.git').exists()
    assert not checkout.is_materialized()

    await workspace.ensure_repos()
    await checkout.ensure_materialized()

    assert checkout.is_materialized()
    assert git(checkout.root / 'demo', 'status', '--porcelain') == ''


async def test_the_base_is_merged_into_the_branch_and_pushed(workspace,
                                                             tmp_path,
                                                             origin):
    """R3.5: every turn starts with the base merged in, and a merge that
    made a commit goes to origin at once so the pull request stays
    mergeable rather than waiting for the turn to edit something."""
    await workspace.ensure_repos()
    checkout = ConversationCheckout(tmp_path / 'conv' / '1', BRANCH,
                                    workspace, 1)
    await checkout.ensure_materialized()
    repo = checkout.root / 'demo'
    commit_and_push(repo, 'turn.txt', 'turn one\n', f'refs/heads/{BRANCH}')
    land_on_main(tmp_path, origin)

    preparation = await checkout.prepare_for_turn({})

    assert preparation.notes == []
    assert preparation.finished_repos == []
    assert (repo / 'later.txt').exists()  # the agent edits current code
    parents = git(repo, 'rev-list', '--parents', '-n', '1', 'HEAD').split()
    assert len(parents) == 3  # commit, its branch parent, and the base
    assert remote_tip(tmp_path, origin, BRANCH) == parents[0]


async def test_a_merge_is_not_pushed_before_the_branch_exists(workspace,
                                                              tmp_path,
                                                              origin):
    """A conversation that has never pushed has nothing to keep mergeable,
    and its first push is the publish step's to make."""
    await workspace.ensure_repos()
    checkout = ConversationCheckout(tmp_path / 'conv' / '1', BRANCH,
                                    workspace, 1)
    await checkout.ensure_materialized()
    repo = checkout.root / 'demo'
    # Work only this host has: a publish that failed after committing.
    commit(repo, 'turn.txt', 'turn one\n')
    land_on_main(tmp_path, origin)

    preparation = await checkout.prepare_for_turn({})

    assert preparation.notes == []
    # The base really was merged — this is a push that chose not to happen.
    parents = git(repo, 'rev-list', '--parents', '-n', '1', 'HEAD').split()
    assert len(parents) == 3
    assert (repo / 'later.txt').exists()
    assert remote_tip(tmp_path, origin, BRANCH) == ''


async def test_a_conflicting_base_is_noted_and_abandoned(workspace, tmp_path,
                                                         origin):
    """R3.6: the turn goes ahead on the stale branch, the thread gets one
    muted line, and no half-finished merge is left for the agent to trip
    over."""
    await workspace.ensure_repos()
    checkout = ConversationCheckout(tmp_path / 'conv' / '1', BRANCH,
                                    workspace, 1)
    await checkout.ensure_materialized()
    repo = checkout.root / 'demo'
    commit_and_push(repo, 'README.md', 'the branch version\n',
                    f'refs/heads/{BRANCH}')
    conflicting = tmp_path / 'someone-else'
    git(tmp_path, 'clone', str(origin), str(conflicting))
    commit_and_push(conflicting, 'README.md', 'the base version\n',
                    'refs/heads/main')

    preparation = await checkout.prepare_for_turn({})

    assert preparation.notes == [
        "-# _Couldn't merge main into this branch: conflicts in README.md._"]
    assert (repo / 'README.md').read_text(encoding='utf-8') \
        == 'the branch version\n'
    assert git(repo, 'status', '--porcelain') == ''
    merge_head = subprocess.run(
        ['git', 'rev-parse', '--verify', '-q', 'MERGE_HEAD'],
        cwd=str(repo), capture_output=True)
    assert merge_head.returncode != 0


async def test_a_merged_away_branch_starts_over(workspace, tmp_path, origin,
                                                monkeypatch):
    """R3.4: GitHub merged the pull request and deleted the branch with it,
    so the turn continues on a fresh branch off the base and the thread is
    told a new pull request is coming."""
    await workspace.ensure_repos()
    checkout = ConversationCheckout(tmp_path / 'conv' / '1', BRANCH,
                                    workspace, 1)
    await checkout.ensure_materialized()
    repo = checkout.root / 'demo'
    commit_and_push(repo, 'turn.txt', 'turn one\n', f'refs/heads/{BRANCH}')
    git(origin, 'update-ref', '-d', f'refs/heads/{BRANCH}')
    stub_gh(workspace, MERGED)

    preparation = await checkout.prepare_for_turn({'demo': PR_URL})

    assert preparation.finished_repos == ['demo']
    assert preparation.notes == [
        '-# _The pull request for **demo** was merged; starting a fresh '
        'branch, and a new pull request with the next edit._']
    assert checkout.branch != BRANCH
    assert checkout.branch.startswith('jermabot/do-a-thing-')
    # A fresh branch off the base: the merged work is not carried over.
    assert not (repo / 'turn.txt').exists()
    assert git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip() \
        == checkout.branch


async def test_a_branch_gone_from_one_repo_keeps_its_name(tmp_path):
    """One branch spans every repo, one pull request per repo. When only
    some repos lost the branch, the conversation keeps the name the others
    still carry and only the finished repo starts over."""
    origins = {'demo': make_origin(tmp_path, 'demo'),
               'other': make_origin(tmp_path, 'other')}
    workspace = AgentWorkspace(
        root=tmp_path / 'repos',
        repos={name: AgentRepo(f'local/{name}', 'main') for name in origins},
        github_token=None)
    workspace._clone_url = lambda name: origins[name].as_uri()
    await workspace.ensure_repos()
    checkout = ConversationCheckout(tmp_path / 'conv' / '1', BRANCH,
                                    workspace, 1)
    await checkout.ensure_materialized()
    for name in origins:
        commit_and_push(checkout.root / name, 'turn.txt', f'{name}\n',
                        f'refs/heads/{BRANCH}')
    # demo's pull request is merged and its branch deleted; other's is open.
    git(origins['demo'], 'update-ref', '-d', f'refs/heads/{BRANCH}')
    stub_gh(workspace, MERGED)

    preparation = await checkout.prepare_for_turn(
        {'demo': PR_URL, 'other': 'https://github.com/local/other/pull/2'})

    assert checkout.branch == BRANCH
    assert preparation.finished_repos == ['demo']
    assert not (checkout.root / 'demo' / 'turn.txt').exists()
    assert (checkout.root / 'other' / 'turn.txt').exists()


async def test_the_branch_catches_up_with_origin_before_the_turn(workspace,
                                                                 tmp_path,
                                                                 origin):
    """R3.5: whatever origin has on the branch that this host does not —
    the owner resolving a conflict on GitHub, an "Update branch" click —
    is taken in before the agent runs, or every later push is rejected."""
    await workspace.ensure_repos()
    checkout = ConversationCheckout(tmp_path / 'conv' / '1', BRANCH,
                                    workspace, 1)
    await checkout.ensure_materialized()
    repo = checkout.root / 'demo'
    commit_and_push(repo, 'turn.txt', 'turn one\n', f'refs/heads/{BRANCH}')
    elsewhere = clone_of_the_branch(tmp_path, origin)
    commit_and_push(elsewhere, 'owner-fix.txt', 'fixed by hand\n',
                    f'refs/heads/{BRANCH}')

    preparation = await checkout.prepare_for_turn({})

    assert preparation.notes == []
    assert (repo / 'owner-fix.txt').exists()

    # And the next publish lands, rather than being told to fetch first.
    (repo / 'agent.txt').write_text('turn two\n', encoding='utf-8')
    await checkout.publish_turn('do a thing', 'Do a thing', 'body',
                                {'demo': PR_URL})

    assert remote_tip(tmp_path, origin, BRANCH) == \
        git(repo, 'rev-parse', 'HEAD').strip()


async def test_a_diverged_branch_is_merged_not_reset(workspace, tmp_path,
                                                     origin):
    """Both sides moved: the remote's commits come in as a merge, and the
    host's own work is still there."""
    await workspace.ensure_repos()
    checkout = ConversationCheckout(tmp_path / 'conv' / '1', BRANCH,
                                    workspace, 1)
    await checkout.ensure_materialized()
    repo = checkout.root / 'demo'
    commit_and_push(repo, 'turn.txt', 'turn one\n', f'refs/heads/{BRANCH}')
    elsewhere = clone_of_the_branch(tmp_path, origin)
    commit_and_push(elsewhere, 'owner-fix.txt', 'fixed by hand\n',
                    f'refs/heads/{BRANCH}')
    # A commit this host made meanwhile: a publish that pushed nothing.
    commit(repo, 'stranded.txt', 'not pushed yet\n')

    preparation = await checkout.prepare_for_turn({})

    assert preparation.notes == []
    assert (repo / 'owner-fix.txt').exists()
    assert (repo / 'stranded.txt').exists()
    assert remote_tip(tmp_path, origin, BRANCH) == \
        git(repo, 'rev-parse', 'HEAD').strip()


async def test_uncommitted_edits_survive_preparation(workspace, tmp_path,
                                                     origin):
    """Edits with no commit are a publish that failed; the next one still
    owes them to the pull request, so nothing here may reset them away."""
    await workspace.ensure_repos()
    checkout = ConversationCheckout(tmp_path / 'conv' / '1', BRANCH,
                                    workspace, 1)
    await checkout.ensure_materialized()
    repo = checkout.root / 'demo'
    commit_and_push(repo, 'turn.txt', 'turn one\n', f'refs/heads/{BRANCH}')
    elsewhere = clone_of_the_branch(tmp_path, origin)
    commit_and_push(elsewhere, 'owner-fix.txt', 'fixed by hand\n',
                    f'refs/heads/{BRANCH}')
    land_on_main(tmp_path, origin)
    (repo / 'turn.txt').write_text('edited, never committed\n',
                                   encoding='utf-8')

    preparation = await checkout.prepare_for_turn({})

    assert preparation.notes == []
    assert (repo / 'turn.txt').read_text(encoding='utf-8') \
        == 'edited, never committed\n'
    assert 'turn.txt' in git(repo, 'status', '--porcelain')
    assert (repo / 'owner-fix.txt').exists()  # and the catch-up still ran
    assert (repo / 'later.txt').exists()
