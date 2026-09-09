"""Finding a conversation's branch on GitHub when its record is gone (R3.3).

gh is stubbed at run_authed, the one place the workspace shells out to it;
the matching itself — the agent's branch prefix, the pull request footer,
the thread line, and the starter prompt quoted in the first commit — is the
real thing.

The listing deliberately does not ask gh for commits: GitHub prices that
per pull request listed and rejects the query well below the hundred the
search asks for, so the commits of a candidate are fetched one at a time,
and only for the candidates that need them.
"""
import json

import pytest

from cogs.utils.agent_config import (
    AGENT_PR_FOOTER,
    AgentRepo,
    pr_thread_line,
)
from cogs.utils.agent_workspace import AgentWorkspace

THREAD_ID = 42
STARTER = 'fix the thing that is broken'
BRANCH = 'jermabot/fix-the-thing-that-is-broken-20260816-101500'
URL = 'https://github.com/local/demo/pull/7'


def pull_request(head=BRANCH, body=None, url=URL,
                 created='2026-08-16T10:15:00Z') -> dict:
    """One entry, as `gh pr list --json url,headRefName,body,createdAt`
    gives it."""
    return {
        'url': url,
        'headRefName': head,
        'createdAt': created,
        'body': f'A summary.\n\n---\n{AGENT_PR_FOOTER}' if body is None
                else body,
    }


def commits_quoting(prompt: str) -> list[dict]:
    """A branch's commits, as `gh pr view --json commits` gives them."""
    return [{'messageHeadline': 'Fix the thing',
             'messageBody': f'Requested via Discord:\n\n{prompt}'}]


@pytest.fixture
def workspace(tmp_path):
    """A workspace with one repo, whose `gh pr list` is answered by
    `listed` and whose `gh pr view` is answered by `commits` (keyed by
    url), and which records the argv it was called with."""
    workspace = AgentWorkspace(
        root=tmp_path / 'repos',
        repos={'demo': AgentRepo('local/demo', 'main')},
        github_token=None)
    workspace.listed = []
    workspace.commits: dict = {}
    workspace.calls = []

    async def stubbed(argv, cwd, stdin_data=None):
        workspace.calls.append(argv)
        if argv[2] == 'view':
            return json.dumps({'commits': workspace.commits.get(argv[3], [])})
        return json.dumps(workspace.listed)

    workspace.run_authed = stubbed
    return workspace


def viewed(workspace) -> list[str]:
    """The pull requests whose commits were fetched one by one."""
    return [argv[3] for argv in workspace.calls if argv[2] == 'view']


async def test_a_pull_request_naming_the_thread_matches(workspace):
    workspace.listed = [pull_request(
        body=f'Summary.\n\n---\n{AGENT_PR_FOOTER}\n'
             f'{pr_thread_line(THREAD_ID)}')]

    found = await workspace.find_conversation_on_github(THREAD_ID, STARTER)

    assert found == (BRANCH, {'demo': URL})


async def test_another_threads_pull_request_does_not_match(workspace):
    workspace.listed = [pull_request(
        body=f'Summary.\n\n---\n{AGENT_PR_FOOTER}\n{pr_thread_line(1)}')]

    assert await workspace.find_conversation_on_github(THREAD_ID, '') is None


async def test_an_older_pull_request_matches_by_its_first_commit(workspace):
    """Pull requests opened before the thread line have only the request
    quoted in the commit the agent made for it."""
    workspace.listed = [pull_request()]
    workspace.commits = {URL: commits_quoting(STARTER)}

    found = await workspace.find_conversation_on_github(THREAD_ID, STARTER)

    assert found == (BRANCH, {'demo': URL})
    assert viewed(workspace) == [URL]


async def test_a_pull_request_without_the_footer_is_not_ours(workspace):
    """Someone else's branch could carry the prefix; the footer is what
    says the agent opened it."""
    workspace.listed = [pull_request(body='Hand-written.')]
    workspace.commits = {URL: commits_quoting(STARTER)}

    assert await workspace.find_conversation_on_github(
        THREAD_ID, STARTER) is None
    assert viewed(workspace) == []


async def test_a_branch_without_the_agent_prefix_is_not_ours(workspace):
    workspace.listed = [pull_request(
        head='feature/something',
        body=f'---\n{AGENT_PR_FOOTER}\n{pr_thread_line(THREAD_ID)}')]

    assert await workspace.find_conversation_on_github(
        THREAD_ID, STARTER) is None


async def test_the_newest_match_wins(workspace):
    """gh lists newest first, and a conversation whose branch was merged
    away has more than one pull request (R3.4)."""
    newest = 'https://github.com/local/demo/pull/9'
    workspace.listed = [
        pull_request(head='jermabot/fix-the-thing-20260901-000000',
                     url=newest, created='2026-09-01T00:00:00Z',
                     body=f'---\n{AGENT_PR_FOOTER}\n'
                          f'{pr_thread_line(THREAD_ID)}'),
        pull_request(body=f'---\n{AGENT_PR_FOOTER}\n'
                          f'{pr_thread_line(THREAD_ID)}'),
    ]

    found = await workspace.find_conversation_on_github(THREAD_ID, STARTER)

    assert found == ('jermabot/fix-the-thing-20260901-000000',
                     {'demo': newest})


async def test_nothing_found_is_not_an_error(workspace):
    """A conversation whose turns never edited code has no pull request,
    and simply starts a branch when one finally does."""
    assert await workspace.find_conversation_on_github(
        THREAD_ID, STARTER) is None


async def test_the_search_asks_gh_for_the_agents_pull_requests(workspace):
    await workspace.find_conversation_on_github(THREAD_ID, STARTER)

    argv = workspace.calls[0]
    assert argv[:3] == ['gh', 'pr', 'list']
    assert '--repo' in argv and 'local/demo' in argv
    assert argv[argv.index('--state') + 1] == 'all'
    assert argv[argv.index('--search') + 1] == \
        'head:jermabot/ sort:created-desc'
    assert argv[argv.index('--json') + 1] == \
        'url,headRefName,body,createdAt'
    assert argv[argv.index('--limit') + 1] == '100'


async def test_the_branch_comes_from_the_newest_repos_pull_request(tmp_path):
    """Repos disagree once one of them has restarted the branch (R3.4):
    the live name is the one on the most recently opened pull request, not
    whichever repo happens to be listed last."""
    workspace = AgentWorkspace(
        root=tmp_path / 'repos',
        repos={'demo': AgentRepo('local/demo', 'main'),
               'other': AgentRepo('local/other', 'main')},
        github_token=None)
    thread_body = f'---\n{AGENT_PR_FOOTER}\n{pr_thread_line(THREAD_ID)}'
    restarted = 'jermabot/fix-the-thing-20260901-000000'
    listed = {
        'local/demo': [pull_request(
            head=restarted, url='https://github.com/local/demo/pull/9',
            created='2026-09-01T00:00:00Z', body=thread_body)],
        'local/other': [pull_request(
            url='https://github.com/local/other/pull/2',
            created='2026-08-16T10:15:00Z', body=thread_body)],
    }

    async def stubbed(argv, cwd, stdin_data=None):
        return json.dumps(listed[argv[argv.index('--repo') + 1]])

    workspace.run_authed = stubbed

    found = await workspace.find_conversation_on_github(THREAD_ID, STARTER)

    assert found == (restarted,
                     {'demo': 'https://github.com/local/demo/pull/9',
                      'other': 'https://github.com/local/other/pull/2'})


async def test_commits_are_fetched_only_where_they_could_decide_it(workspace):
    """One `gh pr view` per candidate is the price of the old matching
    rule, so it is paid only for the agent's own pull requests that have
    no thread line to match on."""
    footerless = 'https://github.com/local/demo/pull/4'
    old = 'https://github.com/local/demo/pull/5'
    thread_body = f'---\n{AGENT_PR_FOOTER}\n{pr_thread_line(THREAD_ID)}'
    workspace.listed = [
        pull_request(head='feature/something', url='.../3',
                     created='2026-09-04T00:00:00Z', body=thread_body),
        pull_request(url=footerless, created='2026-09-03T00:00:00Z',
                     body='Hand-written.'),
        pull_request(url=old, created='2026-09-02T00:00:00Z'),
        pull_request(url=URL, created='2026-09-01T00:00:00Z',
                     body=thread_body),
    ]
    workspace.commits = {old: commits_quoting('some other request')}

    found = await workspace.find_conversation_on_github(THREAD_ID, STARTER)

    assert found == (BRANCH, {'demo': URL})
    # Not the foreign branch, not the pull request without the footer, and
    # not the one the thread line already settled.
    assert viewed(workspace) == [old]
