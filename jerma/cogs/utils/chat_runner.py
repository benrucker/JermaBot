"""Claude Agent SDK wrapper for the whid conversational chat mode.

No tools, no workspace, no git. Used when non-owner whid members ping
JermaBot and the intent classifier confirms they want a response.
The intent classifier is a single-turn call that answers "yes" or "no";
the chat runner is a stateful multi-turn session with no mutation side
effects.
"""
import asyncio
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ProcessError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)
from claude_agent_sdk._internal.sessions import (
    _get_projects_dir,
    project_key_for_directory,
)

from .agent_config import AGENT_MAX_TURNS, AGENT_TIMEOUT_SECONDS
from .agent_runner import OnProgress, SessionResumeError
from .agent_workspace import WorkspaceError, one_line

_CHAT_INSTRUCTIONS = (
    'You are JermaBot, a friendly Discord bot in the whid server. '
    'Be conversational, casual, and helpful. '
    'This is a general chat context — you are not a coding assistant here. '
    'Do not use any tools; respond with plain text only.'
)

_CLASSIFIER_INSTRUCTIONS = (
    'You are a message intent classifier. '
    'Reply with exactly "yes" or "no" and nothing else.'
)

# Tools are actively disallowed for both the classifier and the chat
# agent. tools=[] tells the CLI to expose nothing; disallowed_tools is a
# belt-and-suspenders guard in case the preset re-enables something.
_NO_TOOLS: list[str] = []
_ALL_TOOLS_BLOCKED = [
    'Bash', 'Task', 'WebFetch', 'WebSearch',
    'Read', 'Edit', 'Write', 'Glob', 'Grep',
]


@dataclass
class ChatRunResult:
    final_text: str
    timed_out: bool
    session_id: str | None = None


def local_chat_transcript_path(session_dir: Path, session_id: str) -> Path:
    """Where the SDK stores a chat session's transcript on this host."""
    return (_get_projects_dir() / project_key_for_directory(session_dir)
            / f'{session_id}.jsonl')


def _chat_options(cwd: Path, resume: str | None,
                  stderr=None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(cwd),
        resume=resume,
        stderr=stderr,
        tools=_NO_TOOLS,
        disallowed_tools=_ALL_TOOLS_BLOCKED,
        permission_mode='acceptEdits',
        setting_sources=[],
        max_turns=AGENT_MAX_TURNS,
        system_prompt={
            'type': 'preset',
            'preset': 'claude_code',
            'append': _CHAT_INSTRUCTIONS,
        },
    )


def _classifier_options(cwd: Path) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(cwd),
        tools=_NO_TOOLS,
        disallowed_tools=_ALL_TOOLS_BLOCKED,
        permission_mode='acceptEdits',
        setting_sources=[],
        max_turns=1,
        system_prompt={
            'type': 'preset',
            'preset': 'claude_code',
            'append': _CLASSIFIER_INSTRUCTIONS,
        },
    )


async def classify_intent(message: str, cwd: Path) -> bool:
    """Return True if the ping message intends a conversational response.

    Catches all errors and returns False — the safe default is not to
    invoke the chat agent on an ambiguous or accidental ping.
    """
    if not message.strip():
        return False

    prompt = (
        'A Discord user pinged a bot named JermaBot with this message:\n'
        f'"""\n{message.strip()}\n"""\n\n'
        'Does the user want a natural-language conversational response '
        'from JermaBot? Answer with exactly "yes" or "no" and nothing else.'
    )
    client = ClaudeSDKClient(options=_classifier_options(cwd))
    answer = ''
    try:
        await client.connect()
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                texts = [b.text for b in msg.content
                         if isinstance(b, TextBlock) and b.text.strip()]
                if texts:
                    answer = texts[-1].strip().lower()
            elif isinstance(msg, ResultMessage) and msg.result:
                answer = msg.result.strip().lower()
    except Exception:
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return answer.startswith('yes')


async def run_chat_agent(
        prompt: str,
        session_dir: Path,
        on_progress: OnProgress,
        resume: str | None = None,
        history: str | None = None) -> ChatRunResult:
    """One conversational turn. No tools, no workspace mutations.

    Pass a previous result's session_id as resume to continue that
    conversation; a resume the SDK cannot load raises SessionResumeError
    so the caller can retry without one.

    `history` is prior thread conversation text, prepended to the prompt
    when the session transcript is gone and the thread is the only source
    of context left.
    """
    if history:
        prompt = f'{history}\n\nNew message:\n{prompt}'
    result = ChatRunResult(final_text='', timed_out=False)
    outbox: asyncio.Queue[str | None] = asyncio.Queue()
    stderr_lines: list[str] = []

    def process_failure(error: Exception) -> str:
        return one_line('\n'.join(stderr_lines) or str(error))

    async def consume():
        async for msg in client.receive_response():
            if isinstance(msg, SystemMessage):
                if msg.subtype == 'init':
                    result.session_id = msg.data.get('session_id')
            elif isinstance(msg, AssistantMessage):
                texts = [b.text for b in msg.content
                         if isinstance(b, TextBlock) and b.text.strip()]
                if any(isinstance(b, ToolUseBlock) for b in msg.content):
                    for text in texts:
                        outbox.put_nowait(text)
                elif texts:
                    result.final_text = '\n\n'.join(texts)
            elif isinstance(msg, ResultMessage) and msg.result:
                result.final_text = msg.result

    async def deliver():
        while (text := await outbox.get()) is not None:
            await on_progress(text)

    client = ClaudeSDKClient(options=_chat_options(session_dir, resume,
                                                    stderr_lines.append))
    try:
        await client.connect()
    except ProcessError as error:
        detail = process_failure(error)
        if resume is None:
            raise WorkspaceError(
                f'The chat agent failed to start: {detail}') from error
        raise SessionResumeError(detail) from error
    except RuntimeError as error:
        raise WorkspaceError(
            f'The chat agent could not load this session: '
            f'{one_line(error)}') from error

    try:
        await client.query(prompt)
        sender = asyncio.create_task(deliver())
        try:
            await asyncio.wait_for(consume(), timeout=AGENT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            result.timed_out = True
            try:
                await client.interrupt()
            except Exception:
                pass
        finally:
            outbox.put_nowait(None)
            await sender
    except ProcessError as error:
        raise WorkspaceError(
            f'The chat agent failed: {process_failure(error)}') from error
    finally:
        await client.disconnect()

    return result
