"""Claude Agent SDK wrapper for the coding agent.

The agent gets read/edit tools only. No Bash, no network, no subagents,
and a PreToolUse hook confines every file operation to the workspace
directory. agent_workspace runs git and opens the pull requests, not the
agent, so nothing the agent reads can steer the path from prompt to pull
request.

Resuming a session the SDK cannot find is a failure of its own kind, so it
comes back as SessionResumeError and the caller falls through to another
source of context (R2.4). Checked against SDK 0.2.110: `--resume <id>` for
a session that is not on disk exits the CLI with "No conversation found
with session ID: <id>" on its stderr, and connect() then raises a
ProcessError saying only "Check stderr output for details" unless an
`options.stderr` callback is set. One is set, so what this module reports
and what the thread sees are the CLI's own words.

That callback runs on a detached task, so the CLI's last line may not have
reached it by the time connect() raises. This module therefore never reads
the stderr text to decide what happened. Whenever a turn asked for a
resume, any ProcessError out of connect counts as a lost session. A
process failure with some other cause happens again on the rebuilt
attempt and ends the turn there, carrying the same stderr. Anything that
is not a ProcessError is the turn failing, and it propagates untouched.
"""
import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookJSONOutput,
    HookMatcher,
    ProcessError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)
# A private import, deliberately. The transcript's path on disk is not
# part of the SDK's public API, and reimplementing its naming rules here
# would break without a word the day they change.
from claude_agent_sdk._internal.sessions import (
    _get_projects_dir,
    project_key_for_directory,
)

from .agent_config import (
    AGENT_MAX_TURNS,
    AGENT_REQUEST_SOURCE,
    AGENT_TIMEOUT_SECONDS,
    AgentRepo,
)
from .agent_workspace import WorkspaceError, one_line

# Never let the SDK fall back to API billing; subscription auth comes from
# CLAUDE_CODE_OAUTH_TOKEN (server) or the CLI login (dev machine). Popped
# once at import, so no later feature sees the variable half-removed.
os.environ.pop('ANTHROPIC_API_KEY', None)

AGENT_TOOLS = ['Read', 'Edit', 'Write', 'Glob', 'Grep']
# No allowed tool takes notebook_path today. The guard checks it anyway,
# in case NotebookEdit ever joins AGENT_TOOLS.
_PATH_KEYS = ('file_path', 'path', 'notebook_path')

OnProgress = Callable[[str], Awaitable[None]]


class SessionResumeError(Exception):
    """A resume the SDK could not load.

    The turn can still run, from another source of context (R2.4), so
    this is separate from the failures that end a turn.
    """


def local_transcript_path(workspace_root: Path, session_id: str) -> Path:
    """Where this host keeps a session's transcript.

    The SDK files a session under the checkout it ran in, so the
    conversation's identity is enough to compute the path. No local state
    needed to find out whether the transcript survived. Both halves of the
    name come from the SDK itself rather than a copy of its rules, so a
    layout change is an ImportError at startup instead of a conversation
    that rebuilds itself from its thread every turn and never says why.
    """
    return (_get_projects_dir() / project_key_for_directory(workspace_root)
            / f'{session_id}.jsonl')


@dataclass
class AgentRunResult:
    final_text: str
    timed_out: bool
    # The reply parsed into a PR/commit title and PR body, for turns that
    # edited files.
    title: str = ''
    body: str = ''
    # Pass back as run_agent(resume=...) to continue this conversation.
    session_id: str | None = None


def _split_reply(prompt: str, reply: str) -> tuple[str, str]:
    """Split a file-editing turn's reply into a title and a PR body.

    _build_instructions asks the agent for that shape. When the reply is
    missing, as after a timed-out turn, the prompt's first line stands in.
    """
    first, _, rest = reply.strip().partition('\n')
    title = first.strip('#*` ')  # tolerate heading/bold markup
    if title:
        return title, rest.strip()
    lines = (prompt.strip() or reply.strip()).splitlines()
    return (lines[0] if lines else 'untitled'), reply.strip()


def _deny(reason: str) -> HookJSONOutput:
    return {
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': reason,
        }
    }


def _allow() -> HookJSONOutput:
    return {
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'allow',
        }
    }


def _make_path_guard(root: Path):
    """PreToolUse hook: only the allowed tools, only inside the workspace."""
    async def guard(input_data, tool_use_id, context) -> HookJSONOutput:
        tool_name = input_data.get('tool_name', '')
        tool_input = input_data.get('tool_input') or {}
        if tool_name not in AGENT_TOOLS:
            return _deny(f'The {tool_name} tool is not permitted.')
        for key in _PATH_KEYS:
            raw = tool_input.get(key)
            if not isinstance(raw, str) or not raw:
                continue
            path = Path(raw)
            resolved = (path if path.is_absolute() else root / path).resolve()
            if not resolved.is_relative_to(root):
                return _deny(
                    f'{raw} is outside the agent workspace. '
                    f'Only paths under {root} are permitted.'
                )
        return _allow()

    return guard


def _build_instructions(repos: dict[str, AgentRepo]) -> str:
    repo_lines = '\n'.join(
        f'- {name}/ is github.com/{repo.slug}, base branch '
        f'{repo.base_branch}'
        for name, repo in repos.items()
    )
    return (
        'You are JermaBot, handling requests your owner sends over '
        f'{AGENT_REQUEST_SOURCE}. Your working directory contains checkouts '
        'of:\n'
        f'{repo_lines}\n\n'
        '- Touch only the repositories the request concerns; questions get '
        'answers, not edits.\n'
        '- You have no Bash, network, or git. After each reply the harness '
        'commits your edits to this conversation\'s branch and opens or '
        'updates its pull request. Edits persist across requests.\n'
        '- If you edited files, start your reply with a commit-style '
        'imperative title line (under 70 characters), which becomes the '
        'commit message and pull request title. After a blank line, write '
        'the pull request description. Plain Markdown, no preamble.\n'
        '- Off-topic requests are expected; just answer them.'
    )


def _build_options(workspace_root: Path,
                   repos: dict[str, AgentRepo],
                   resume: str | None,
                   stderr=None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(workspace_root),
        resume=resume,
        # Without this the SDK never pipes the CLI's stderr, and every
        # process failure arrives as ProcessError's placeholder text
        # instead of the reason.
        stderr=stderr,
        tools=list(AGENT_TOOLS),
        disallowed_tools=['Bash', 'Task', 'WebFetch', 'WebSearch'],
        permission_mode='acceptEdits',
        # [] = ignore all filesystem settings. Without it, a cloned repo's
        # .claude/settings.json could re-grant the tools removed above.
        setting_sources=[],
        max_turns=AGENT_MAX_TURNS,
        system_prompt={
            'type': 'preset',
            'preset': 'claude_code',
            'append': _build_instructions(repos),
        },
        hooks={
            'PreToolUse': [HookMatcher(hooks=[_make_path_guard(workspace_root)])],
        },
    )


def handle_message(message, result: AgentRunResult,
                   outbox: asyncio.Queue) -> None:
    """Fold one SDK message into the turn's result.

    Narration goes to the outbox; a text-only assistant message is the
    answer. Module level rather than a closure, so a test can drive a
    turn's message handling without an SDK subprocess.
    """
    if isinstance(message, SystemMessage):
        # The init message names the session up front, so the id survives
        # a timeout that cuts the run short of its ResultMessage.
        if message.subtype == 'init':
            result.session_id = message.data.get('session_id')
    elif isinstance(message, AssistantMessage):
        texts = [block.text for block in message.content
                 if isinstance(block, TextBlock) and block.text.strip()]
        if any(isinstance(block, ToolUseBlock) for block in message.content):
            for text in texts:
                outbox.put_nowait(text)
        elif texts:
            result.final_text = '\n\n'.join(texts)
    elif isinstance(message, ResultMessage):
        if message.result:
            result.final_text = message.result


async def run_agent(prompt: str, workspace_root: Path,
                    repos: dict[str, AgentRepo],
                    on_progress: OnProgress,
                    resume: str | None = None,
                    image_paths: list[Path] = (),
                    request: str | None = None) -> AgentRunResult:
    """Run one agent turn; interim narration streams, the answer returns.

    Pass a previous result's session_id as resume to continue that
    conversation with its context intact; a resume the SDK cannot load
    raises SessionResumeError before the turn starts, so the caller can
    run it again from another source of context.

    Text the agent sends alongside tool calls is narration about work in
    progress, and it reaches on_progress as it happens. A text-only
    assistant message ends the turn, so that one is the final answer and
    comes back in AgentRunResult instead.

    A queue carries narration to on_progress, so slow delivery (Discord
    rate limits, say) neither backpressures the SDK message stream nor
    counts against the session timeout.

    `request` is the owner's own words, for prompts that carry more than
    them (a history rebuilt from the thread). A turn that ends without a
    reply takes its commit title from there rather than from the harness's
    framing.
    """
    result = AgentRunResult(final_text='', timed_out=False)
    outbox: asyncio.Queue[str | None] = asyncio.Queue()
    # The CLI's stderr lines, in the order it wrote them.
    stderr_lines: list[str] = []

    def process_failure(error: Exception) -> str:
        """What actually went wrong with the CLI process.

        ProcessError's own message is a placeholder. The cause is on
        stderr, "No conversation found with session ID: ..." included.
        """
        return one_line('\n'.join(stderr_lines) or str(error))

    async def consume(client: ClaudeSDKClient):
        async for message in client.receive_response():
            handle_message(message, result, outbox)

    async def deliver():
        while (text := await outbox.get()) is not None:
            await on_progress(text)

    if image_paths:
        paths_str = '\n'.join(f'- {p}' for p in image_paths)
        prompt = (f'{prompt}\n\nImage attachment(s) saved in the workspace:\n'
                  f'{paths_str}\nUse the Read tool to view them.')

    options = _build_options(workspace_root, repos, resume,
                             stderr_lines.append)
    client = ClaudeSDKClient(options=options)
    try:
        # connect() loads a resume from disk, and fails right here if the
        # session is gone. It cleans up after itself, so there is nothing
        # to disconnect in that case.
        await client.connect()
    except ProcessError as error:
        detail = process_failure(error)
        if resume is None:
            # Nothing to fall through to, so the turn ends here. The
            # message quotes the CLI rather than "check stderr".
            raise WorkspaceError(
                f'The agent process failed: {detail}') from error
        # This turn asked for a resume, so it has somewhere else to go
        # (R2.4). The stderr text does not decide that. The reader is
        # detached and may not have caught up, and a failure with nothing
        # to do with the session happens again on the rebuilt attempt
        # below, where it ends the turn with this same detail.
        raise SessionResumeError(detail) from error
    except RuntimeError as error:
        # The SDK's own failures loading a resume, before it even reaches
        # the CLI. Not a missing session, so not a fall-through. The
        # transcript is there, and the owner should hear why the SDK could
        # not read it.
        raise WorkspaceError(
            f'The agent could not load this conversation: '
            f'{one_line(error)}') from error
    try:
        await client.query(prompt)
        sender = asyncio.create_task(deliver())
        try:
            await asyncio.wait_for(consume(client), timeout=AGENT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            result.timed_out = True
            try:
                await client.interrupt()
            except Exception:
                pass
        finally:
            outbox.put_nowait(None)  # deliver queued text, then stop
            await sender
    except ProcessError as error:
        # The CLI fell over mid-turn. Same treatment, since whatever this
        # says goes straight to the thread.
        raise WorkspaceError(
            f'The agent process failed: {process_failure(error)}') from error
    finally:
        await client.disconnect()

    # `request` can be empty for good reason (an image with no words),
    # and the prompt it would fall back to can carry a rebuilt history.
    result.title, result.body = _split_reply(
        prompt if request is None else request, result.final_text)
    return result
