"""Claude Agent SDK wrapper for the coding agent.

The agent gets read/edit tools only — no Bash, no network, no subagents —
and a PreToolUse hook confines every file operation to the workspace
directory. Git and pull requests are handled by agent_workspace, not the
agent, so the prompt-to-PR pipeline can't be steered by anything the agent
reads.
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
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

from .agent_config import (
    AGENT_MAX_TURNS,
    AGENT_REQUEST_SOURCE,
    AGENT_TIMEOUT_SECONDS,
    AgentRepo,
)

# Never let the SDK fall back to API billing; subscription auth comes from
# CLAUDE_CODE_OAUTH_TOKEN (server) or the CLI login (dev machine). Done once
# at import so no later feature sees the variable half-removed.
os.environ.pop('ANTHROPIC_API_KEY', None)

AGENT_TOOLS = ['Read', 'Edit', 'Write', 'Glob', 'Grep']
# notebook_path isn't used by any allowed tool today; it's defense in depth
# in case NotebookEdit ever joins AGENT_TOOLS.
_PATH_KEYS = ('file_path', 'path', 'notebook_path')

OnProgress = Callable[[str], Awaitable[None]]


@dataclass
class AgentRunResult:
    final_text: str
    timed_out: bool
    # Pass back as run_agent(resume=...) to continue this conversation.
    session_id: str | None = None


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
        f'- {name}/ — github.com/{repo.slug}, base branch {repo.base_branch}'
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
        'imperative title line (under 70 characters) — it becomes the '
        'commit message and pull request title — then, after a blank line, '
        'the pull request description. Plain Markdown, no preamble.\n'
        '- Off-topic requests are expected; just answer them.'
    )


def _build_options(workspace_root: Path,
                   repos: dict[str, AgentRepo],
                   resume: str | None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(workspace_root),
        resume=resume,
        tools=list(AGENT_TOOLS),
        disallowed_tools=['Bash', 'Task', 'WebFetch', 'WebSearch'],
        permission_mode='acceptEdits',
        # [] = ignore all filesystem settings; without this, a cloned repo's
        # .claude/settings.json could re-grant tools we've removed.
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


async def run_agent(prompt: str, workspace_root: Path,
                    repos: dict[str, AgentRepo],
                    on_progress: OnProgress,
                    resume: str | None = None) -> AgentRunResult:
    """Run one agent turn; interim narration streams, the answer returns.

    Pass a previous result's session_id as resume to continue that
    conversation with its context intact.

    Text sent alongside tool calls is narration about work in progress and
    goes to on_progress as it happens; a text-only assistant message ends the
    turn, so it is the final answer and comes back in AgentRunResult instead.

    Narration is handed to on_progress through a queue so that slow delivery
    (e.g. Discord rate limits) neither backpressures the SDK message stream
    nor counts against the session timeout.
    """
    result = AgentRunResult(final_text='', timed_out=False)
    outbox: asyncio.Queue[str | None] = asyncio.Queue()

    async def consume(client: ClaudeSDKClient):
        async for message in client.receive_response():
            if isinstance(message, SystemMessage):
                # The init message names the session up front, so it's known
                # even if a timeout cuts the run short of its ResultMessage.
                if message.subtype == 'init':
                    result.session_id = message.data.get('session_id')
            elif isinstance(message, AssistantMessage):
                texts = [block.text for block in message.content
                         if isinstance(block, TextBlock) and block.text.strip()]
                if any(isinstance(block, ToolUseBlock)
                       for block in message.content):
                    for text in texts:
                        outbox.put_nowait(text)
                elif texts:
                    result.final_text = '\n\n'.join(texts)
            elif isinstance(message, ResultMessage):
                result.session_id = message.session_id
                if message.result:
                    result.final_text = message.result

    async def deliver():
        while (text := await outbox.get()) is not None:
            await on_progress(text)

    options = _build_options(workspace_root, repos, resume)
    async with ClaudeSDKClient(options=options) as client:
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

    return result
