"""Lazy materialization, against real git repos.

A local bare repo stands in for GitHub: the point of these tests is that a
conversation's worktrees can be thrown away and rebuilt from its branch,
which is what makes a conversation survive losing its host.
"""
import os
import shutil
import stat
import subprocess

import pytest

from cogs.utils.agent_config import AgentRepo
from cogs.utils.agent_workspace import AgentWorkspace

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


@pytest.fixture
def origin(tmp_path):
    """A bare repo with one commit on `main`, playing the part of GitHub."""
    seed = tmp_path / 'seed'
    seed.mkdir()
    git(seed, '-c', 'init.defaultBranch=main', 'init')
    (seed / 'README.md').write_text('base\n', encoding='utf-8')
    git(seed, 'add', '-A')
    git(seed, 'commit', '-m', 'initial')
    bare = tmp_path / 'origin.git'
    git(tmp_path, 'clone', '--bare', str(seed), str(bare))
    return bare


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


def commit_and_push(repo, filename: str, text: str, ref: str):
    """One turn's work, published the way publish_turn publishes it."""
    (repo / filename).write_text(text, encoding='utf-8')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-m', f'add {filename}')
    git(repo, 'push', 'origin', f'HEAD:{ref}')


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
