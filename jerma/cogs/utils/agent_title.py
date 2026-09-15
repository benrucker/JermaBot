"""Naming a turn's edits: the commit subject and pull request title.

The title comes from a model call of its own rather than from the agent's
reply. A reply's first line is whatever the agent felt like opening with,
and asking it to lead with a title produced malformed ones often enough.
This call sees the owner's request and the agent's reply, can read the
edited checkouts, and answers through a schema, so the title is the whole
of what it returns.

Same workspace, same confinement as the agent (agent_runner's path
guard), minus the editing tools. Checked against CLI 2.1.191: output_format
works by adding a StructuredOutput tool whose call carries the answer, so
the guard has to let that tool through or the title never arrives.
"""
import asyncio
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    ProcessError,
    ResultMessage,
    query,
)

from .agent_config import (
    AGENT_TITLE_MAX_TURNS,
    AGENT_TITLE_MODEL,
    AGENT_TITLE_TIMEOUT_SECONDS,
    AgentRepo,
)
from .agent_runner import (
    BLOCKED_TOOLS,
    make_path_guard,
    process_failure,
    repo_lines,
)
from .agent_workspace import WorkspaceError, one_line

# May look at the edits, never touch them.
TITLE_TOOLS = ['Read', 'Glob', 'Grep']

_INSTRUCTIONS = (
    'You name code changes. You are given what the owner asked a coding '
    'agent for and how the agent replied after editing the repositories '
    'in your working directory. Answer with one line to serve as the '
    'commit subject and pull request title: imperative mood, under 70 '
    'characters, naming what changed rather than what was asked or '
    'discussed, in the style of "Add X to Y" or "Fix Z". If the reply '
    'leaves the change unclear, read the edited files. Nothing else in '
    'your answer.'
)

_SCHEMA = {
    'type': 'json_schema',
    'schema': {
        'type': 'object',
        'properties': {'title': {'type': 'string'}},
        'required': ['title'],
        'additionalProperties': False,
    },
}


def _prompt(request: str, reply: str, edited_repos: list[str],
            workspace_root: Path, repos: dict[str, AgentRepo]) -> str:
    edited = ', '.join(f'{name}/' for name in edited_repos)
    return (
        f'Repositories in the working directory, {workspace_root}:\n'
        f'{repo_lines(repos)}\n\n'
        f'The agent edited: {edited}\n\n'
        f"The owner's request:\n{request.strip() or '(no text)'}\n\n"
        f"The agent's reply:\n{reply.strip() or '(no reply)'}"
    )


def _options(workspace_root: Path, stderr) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(workspace_root),
        stderr=stderr,
        model=AGENT_TITLE_MODEL,
        # A one-line answer from a short prompt. Deep reasoning buys
        # nothing here and costs a wait on every edit.
        effort='low',
        tools=list(TITLE_TOOLS),
        disallowed_tools=list(BLOCKED_TOOLS),
        setting_sources=[],
        max_turns=AGENT_TITLE_MAX_TURNS,
        system_prompt=_INSTRUCTIONS,
        # The title arrives as structured output, never as prose to parse.
        output_format=_SCHEMA,
        hooks={
            # StructuredOutput is the CLI's own tool for output_format.
            'PreToolUse': [HookMatcher(hooks=[make_path_guard(
                workspace_root, [*TITLE_TOOLS, 'StructuredOutput'])])],
        },
    )


async def generate_title(request: str, reply: str, edited_repos: list[str],
                         workspace_root: Path,
                         repos: dict[str, AgentRepo]) -> str:
    """Name a turn's edits, for its commit subject and pull request title.

    A one-shot call, separate from the agent's session. `reply` may be
    empty (a timed-out turn); the request and the edits themselves are
    still there to name from. Any failure raises WorkspaceError with the
    cause; there is no stand-in title.
    """
    stderr_lines: list[str] = []
    options = _options(workspace_root, stderr_lines.append)
    prompt = _prompt(request, reply, edited_repos, workspace_root, repos)

    async def last_result() -> ResultMessage | None:
        final = None
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                final = message
        return final

    try:
        final = await asyncio.wait_for(last_result(),
                                       timeout=AGENT_TITLE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise WorkspaceError(
            'Naming the change took longer than '
            f'{AGENT_TITLE_TIMEOUT_SECONDS} seconds.') from None
    except ProcessError as error:
        raise WorkspaceError(
            'Naming the change failed: '
            f'{process_failure(error, stderr_lines)}') from error

    if final is None:
        raise WorkspaceError('Naming the change ended without a result.')
    if final.is_error:
        detail = one_line('; '.join(final.errors or []) or final.result
                          or final.subtype)
        raise WorkspaceError(f'Naming the change failed: {detail}')
    title = one_line((final.structured_output or {}).get('title', ''))
    if not title:
        raise WorkspaceError(
            f'Naming the change returned no title ({final.subtype}).')
    return title
