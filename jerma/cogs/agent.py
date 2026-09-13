"""Coding agent (owner) and whid chat mode (non-owner whid members).

Owner mode: Ping JermaBot, get answers or pull requests. When the owner
pings the bot with something that isn't a command, the message starts a
coding-agent conversation. Replies live in a thread created just before
the first one, and further owner messages in that thread continue the
conversation with no ping needed, forever. Conversations run concurrently,
each keeping one branch and at most one pull request per repo, updated
turn by turn. AgentTaskService runs the conversations themselves.

Whid chat mode: Non-owner members of the whid server may also ping the
bot. A fast intent classifier decides whether the ping looks like a
genuine request for a response before anything else runs — this guards
against accidental invocations such as misspelled commands. When the
classifier says yes, a conversational agent (no tools, no git, no PRs)
answers in a thread the same way the owner's mode does. Sessions are
in-memory only; there is no catch-up on restart and no mutation side
effects of any kind.

This cog recognizes owner agent threads from Discord itself, never from
local state, so a thread keeps working across restarts, evictions, and a
wiped host. The bot owns the thread, and the thread's id is the id of its
starter message, which is the owner's original ping. Checking that pair
identifies an agent thread with nothing but Discord. The service's
conversation table answers first as a fast path, and this cog caches each
thread's answer. Threads Discord auto-archived count too. A message
unarchives one, and that MESSAGE_CREATE can arrive before the thread is
back in the library's cache, leaving a PartialMessageable to fetch.

A recognized thread the service has never heard of is a conversation whose
identity record this host lost. The thread itself remembers some of it.
Every pull request the bot opened was announced in it, and this cog parses
those announcements back out and hands them to the service, which turns
them into a branch (R3.3). For a thread with no announcements, the service
looks the conversation up on GitHub by the message that started it.

The thread is also the last resort for what the agent knew (R2c). When a
conversation's transcript is gone from this host, the service asks for
build_thread_history(), which reads the whole thread back and hands it
over as prior history for a new session. That is the owner's messages, the
agent's replies, and the images, re-downloaded. The bot's own fixed
messages are not the agent's words and must not come back as them. Pull
request announcements, timeouts, and error replies are the shapes this cog
writes itself, and they are constants here, so rewording one cannot leave
the old wording looking like an answer. Everything else it posts comes
from the service or the workspace as a muted subtext line (`-# _..._`),
and that shape, not any particular wording, is what reading a thread back
recognizes. Either way the line returns as a plain "[harness] ..." fact.

The bot does not lose messages posted while it was down either (R6.4).
Once it is connected, a background job reads every text channel it can
see, active and archived threads alike, keeps the ones it recognizes as
ours, and runs whatever the owner said after the bot's last word in each
as ordinary turns. It finds those threads in Discord, so it works on a
host that has never heard of any of them (R6.5).
"""
import asyncio
import functools
import math
import re
import traceback
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands

from jermabot import JermaBot
from .utils.agent_config import (
    AGENT_TIMEOUT_SECONDS,
    WHID_GUILD_ID,
    get_conversations_root,
)
from .utils.agent_runner import SessionResumeError
from .utils.agent_service import (
    AgentTaskService,
    RELOADING_NOTE,
    ReconstructedHistory,
    WorkspaceError,
)
from .utils.agent_workspace import one_line
from .utils.chat_runner import (
    classify_intent,
    local_chat_transcript_path,
    run_chat_agent,
)
from .utils.split_message import MESSAGE_LIMIT, split_message


PR_OPENED = 'Pull request'
PR_UPDATED = 'Updated the pull request'


def _pr_line(verb: str, repo: str, url: str) -> str:
    """The bot's pull request announcement.

    _handle_prompt writes every one through here and the pattern below
    reads them back, so rewording it cannot leave old announcements
    looking like the agent's words.
    """
    return f'{verb} for **{repo}**: {url}'


# The same line with its three fields turned into capture groups, built
# from the writer above rather than typed out a second time.
_PR_ANNOUNCEMENT = re.compile(
    '^' + re.escape(_pr_line('\x00', '\x01', '\x02'))
    .replace('\x00', f'(?P<verb>{re.escape(PR_OPENED)}'
                     f'|{re.escape(PR_UPDATED)})')
    .replace('\x01', r'(?P<repo>[^*]+)')
    .replace('\x02', r'(?P<url>https://\S+)') + '$')
# A muted subtext line. The harness says everything about itself this way:
# the recovery note, a merge conflict, an image it could not fetch.
_MUTED_LINE = re.compile(r'^-# _(?P<text>.*)_$')

# The rest of the bot's fixed messages, named so that reading a thread
# back recognizes exactly what writing one produces.
NO_THREAD_HERE = "I can't make a thread here :("
UNREACHABLE_DISCORD = ("I couldn't reach Discord to work out where this "
                       'message lives: ')
UNREADABLE_THREAD = ("I couldn't read this thread's history to pick up where "
                     'it left off: ')
NOTHING_SAID = '(The agent finished without saying anything.)'
WORKSPACE_ERROR = 'Workspace error:'
UNEXPECTED_ERROR = 'Error:'


def _timeout_notice(minutes) -> str:
    return (f'(Hit the {minutes} minute limit. '
            'Published whatever it finished.)')


TIMEOUT_NOTICE = _timeout_notice(AGENT_TIMEOUT_SECONDS // 60)
# The writer's own sentence with only the minute count left open. The
# limit is configuration, so a thread can hold notices naming other
# numbers.
_TIMEOUT_NOTICE = re.compile(
    '^' + re.escape(_timeout_notice('\x00')).replace('\x00', r'\d+') + '$')

HISTORY_HEADING = ('Prior history of this conversation (reconstructed from '
                   'the Discord thread; tool calls and results were lost):')
# A pasted image link the bot could not fetch, in the muted harness style
# so a thread read back later takes it as harness news, not as an answer.
UNFETCHED_LINK = "-# _Couldn't fetch the image link {url}: {cause}._"


def _harness_message(content: str) -> str | None:
    """What a whole message of the bot's own means, if it is a fixed shape.

    The agent then hears it as something that happened rather than as
    something it said (R2c.1b).
    """
    if content.startswith(WORKSPACE_ERROR):
        rest = one_line(content[len(WORKSPACE_ERROR):])
        return f'The turn failed: {rest}'
    if content.startswith(f'{UNEXPECTED_ERROR}\n```py'):
        return 'The turn failed with an unexpected error.'
    if _TIMEOUT_NOTICE.match(content):
        return 'The turn timed out and published what it had finished.'
    if content == NOTHING_SAID:
        return 'The turn ended without a reply.'
    if content == NO_THREAD_HERE or content.startswith(
            (UNREACHABLE_DISCORD, UNREADABLE_THREAD)):
        return f'A message went unanswered: {one_line(content)}'
    return None


def _all_muted(content: str) -> bool:
    """Whether a message of the bot's is nothing but muted harness lines.

    Those lines are the reloading note, a merge conflict, and an image the
    bot could not fetch. A message of only those is news about the turn,
    not its answer.
    """
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return bool(lines) and all(_MUTED_LINE.match(line) for line in lines)


def _harness_line(line: str) -> str | None:
    """The same for one line of a message: the muted subtext the harness
    posts, and the pull request announcements _handle_prompt writes."""
    match = _MUTED_LINE.match(line)
    if match is not None:
        return match['text']
    match = _PR_ANNOUNCEMENT.match(line)
    if match is not None:
        verb = 'opened' if match['verb'] == PR_OPENED else 'updated'
        return (f'Pull request {verb} for {match["repo"]}: {match["url"]}')
    return None


IMAGE_LINK_TIMEOUT = aiohttp.ClientTimeout(total=20)
IMAGE_LINK_MAX_BYTES = 25 * 1024 * 1024  # Discord's own attachment limit


async def fetch_image_link(session, url: str) -> tuple[str, bytes]:
    """One pasted image link, downloaded through an aiohttp session.

    Raises with the reason when the link is not an image this bot can
    have, so both callers can say why rather than dropping it in silence.
    """
    # The timeout is deliberate. A rebuild fetches every link in the
    # thread under the conversation lock, and a stalled web server must
    # not hold that lock for long.
    async with session.get(url, timeout=IMAGE_LINK_TIMEOUT) as response:
        content_type = response.headers.get('Content-Type', '')
        if response.status != 200:
            raise ValueError(f'HTTP {response.status}')
        if not content_type.startswith('image/'):
            raise ValueError(f'not an image ({content_type or "no type"})')
        length = response.headers.get('Content-Length')
        if length is not None and int(length) > IMAGE_LINK_MAX_BYTES:
            raise ValueError(f'too large ({length} bytes)')
        # Read the body chunk by chunk. read(n) returns only what has
        # already been buffered, which silently truncates anything the
        # server sends in pieces. One join at the end keeps a 25MB
        # picture from being copied a few hundred times on the way in.
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.content.iter_chunked(65536):
            chunks.append(chunk)
            size += len(chunk)
            if size > IMAGE_LINK_MAX_BYTES:
                raise ValueError(
                    f'too large (over {IMAGE_LINK_MAX_BYTES} bytes)')
        data = b''.join(chunks)
        filename = url.rstrip('/').split('/')[-1].split('?')[0] or 'image'
        return filename, data


def _stamp(message) -> str:
    return message.created_at.strftime('%Y-%m-%d %H:%M UTC')


async def _owner_block(message, fetch
                       ) -> tuple[list[str], list[tuple[str, bytes]]]:
    """One owner message as history, with its images re-downloaded.

    Attachments come back from Discord. A pasted image link is an embed
    with nothing of ours behind it, so it goes through `fetch`, the
    caller's downloader. When either kind cannot be fetched, the block
    names it as missing instead of dropping it, so the agent knows the
    message had a picture in it (R2c.2).
    """
    lines = [message.content] if message.content else []
    images = []
    for attachment in message.attachments:
        content_type = attachment.content_type or ''
        if not content_type.startswith('image/'):
            continue
        # The name is the agent's only handle on the file, and two
        # messages may well both have attached "image.png".
        name = f'{message.id}-{attachment.filename}'
        try:
            data = await attachment.read()
        except (discord.HTTPException, aiohttp.ClientError,
                asyncio.TimeoutError) as error:
            lines.append(f'[image attachment {attachment.filename}: Discord '
                         f'no longer has this file ({one_line(error)})]')
            continue
        images.append((name, data))
        lines.append(f'[image attachment: {name}]')
    for url in _collect_embed_image_urls(message):
        try:
            filename, data = await fetch(url)
        except Exception as error:
            # Anything at all. This is somebody else's web server, and a
            # named gap is worth more to the agent than a lost picture.
            lines.append(f'[image link {url}: could not be fetched '
                         f'({one_line(error)})]')
            continue
        name = f'{message.id}-{filename}'
        images.append((name, data))
        lines.append(f'[image link {url}: {name}]')
    return lines, images


async def build_thread_history(messages, bot_id: int, owner_id: int,
                               fetch) -> ReconstructedHistory:
    """A conversation's thread as prior history for a new session (R2c).

    `messages` are the thread's messages oldest first, the starter message
    (which lives in the parent channel, not the thread) in front. The
    owner's messages and the agent's replies become labelled blocks; the
    harness's own messages become "[harness]" facts, so nothing the bot
    posted on its own behalf comes back as the agent's words (R2c.1b).
    Anyone else in the thread is not part of the conversation.

    `fetch` downloads one pasted image link (see fetch_image_link).

    The spec allows these losses (R2c.3): tool calls, tool results, and
    how the agent mixed them into what it said.
    """
    blocks: list[str] = []
    images: list[tuple[str, bytes]] = []
    for message in messages:
        stamp = _stamp(message)
        if message.author.id == bot_id:
            summary = _harness_message(message.content)
            said, facts = [], ([summary] if summary else [])
            if summary is None:
                for line in message.content.splitlines():
                    fact = _harness_line(line.strip())
                    if fact is None:
                        said.append(line)
                    else:
                        facts.append(fact)
            if '\n'.join(said).strip():
                blocks.append(f'[{stamp}] You (agent):\n'
                              + '\n'.join(said).strip())
            blocks += [f'[{stamp}] [harness] {fact}' for fact in facts]
        elif message.author.id == owner_id:
            lines, message_images = await _owner_block(message, fetch)
            images += message_images
            if lines:
                blocks.append(f'[{stamp}] Owner:\n' + '\n'.join(lines))
    if not blocks:
        return ReconstructedHistory(text='')
    return ReconstructedHistory(text=f'{HISTORY_HEADING}\n\n'
                                     + '\n\n'.join(blocks),
                                images=images)


def build_chat_thread_history(messages, bot_id: int) -> str:
    """Prior messages in a chat thread, as labelled text for the agent.

    Unlike build_thread_history, chat threads have no harness messages
    (no PR announcements, no workspace errors) — only user turns and
    agent answers. Bot messages come back as the agent's own words;
    everyone else is labelled by display name.
    """
    blocks: list[str] = []
    for message in messages:
        content = message.content.strip() if message.content else ''
        if not content:
            continue
        if message.author.id == bot_id:
            blocks.append(f'JermaBot:\n{content}')
        else:
            name = getattr(message.author, 'display_name',
                           getattr(message.author, 'name', 'User'))
            blocks.append(f'{name}:\n{content}')
    if not blocks:
        return ''
    return ('Prior conversation history (reconstructed from the Discord '
            'thread; some context may be missing):\n\n'
            + '\n\n'.join(blocks))


def parse_pr_announcements(contents: list[str]) -> dict[str, str]:
    """The pull request each repo has, read back out of the bot's own
    messages in a thread (R3.3).

    `contents` is the thread's messages in order, oldest first. The last
    announcement for a repo wins, because a conversation whose branch was
    merged away opens a second pull request and announces that one too.
    The dict keeps announcement order, so its last entry is the thread's
    most recent pull request.
    """
    urls: dict[str, str] = {}
    for content in contents:
        for line in content.splitlines():
            match = _PR_ANNOUNCEMENT.match(line.strip())
            if match is not None:
                urls.pop(match['repo'], None)
                urls[match['repo']] = match['url']
    return urls


def _collect_embed_image_urls(message: discord.Message) -> list[str]:
    """The image URLs of the embeds Discord makes for pasted image links."""
    urls: list[str] = []
    seen: set[str] = set()
    for embed in message.embeds:
        url = None
        if embed.type == 'image' and embed.url:
            url = embed.url
        elif embed.image and embed.image.url:
            url = embed.image.url
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


async def setup(bot):
    await bot.add_cog(Agent(bot))


class Agent(commands.Cog):
    # The longest auto-archive Discord allows (a week). The bot never
    # archives, locks, or deletes an agent thread itself.
    THREAD_ARCHIVE_MINUTES = 10080

    def __init__(self, bot: JermaBot):
        self.bot = bot
        self.service = AgentTaskService()
        # thread id -> whether it is one of our agent threads. Each answer
        # costs a message fetch, and a thread's answer never changes.
        self._agent_threads: dict[int, bool] = {}
        # The startup catch-up, kept so it never runs twice at once and
        # dies with the cog.
        self._catch_up_task: asyncio.Task | None = None
        # Messages this path has taken, so the catch-up cannot take them
        # again. Only the owner's own turns land here, and they stay for
        # the life of the process (see the end of _catch_up).
        self._live_ids: set[int] = set()
        # Whid chat mode state. Both caches are in-memory only: chat
        # sessions do not survive restarts (no durability requirement for
        # the untrusted mode).
        self._chat_threads: dict[int, bool] = {}   # thread id -> is_chat_thread
        self._chat_sessions: dict[int, str] = {}   # thread id -> session_id

    async def cog_load(self):
        self.service.start()

    async def cog_unload(self):
        if self._catch_up_task is not None:
            self._catch_up_task.cancel()
        self.service.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or self.bot.user is None:
            return

        # A conversation in a thread the bot created hears every message
        # from its participants without a ping. Anywhere else it takes a
        # ping to start or continue one.
        stripped = self._strip_mention(message.content)
        if stripped is None and not self._maybe_thread(message.channel):
            return

        # Determine the caller: owner (coding agent) or whid member (chat).
        # Recognition can cost Discord round-trips; errors on the owner's
        # path are reported back, errors on the chat path are swallowed.
        is_owner = False
        try:
            is_owner = await self.bot.is_owner(message.author)
            channel = await self._resolve_channel(message)

            if is_owner:
                if stripped is not None:
                    raw = stripped
                elif (isinstance(channel, discord.Thread)
                        and await self._is_agent_thread(channel)):
                    raw = message.content
                else:
                    return
            elif self._is_whid_message(message):
                if stripped is not None:
                    # Intent classifier guards against accidental invocations
                    # (e.g. misspelled commands). Only a clear yes proceeds.
                    if not await self._has_chat_intent(stripped):
                        return
                    raw = stripped
                elif (isinstance(channel, discord.Thread)
                      and await self._is_chat_thread(channel)):
                    raw = message.content
                else:
                    return
            else:
                return

        except (discord.HTTPException, aiohttp.ClientError,
                asyncio.TimeoutError) as error:
            # Recognition needs Discord, and Discord can be down, forbid
            # the fetch, or time out. The owner's message must not vanish
            # into a console traceback; non-owner failures are silent.
            if is_owner:
                await message.reply(f'{UNREACHABLE_DISCORD}{error}')
            return

        if is_owner:
            # Recorded before the turn rather than after it. A catch-up
            # running right now must not replay a message this path has
            # already taken (R6.4).
            self._live_ids.add(message.id)
            await self._run_turn(message, channel, raw)
        else:
            await self._run_chat_turn(message, channel, raw)

    async def _run_turn(self, message: discord.Message, channel,
                        raw: str) -> bool:
        """The tail of the message path: what the owner asked for, its
        pictures, and the turn itself.

        Shared with the startup catch-up so a message the bot missed goes
        through exactly what a live one does. False when there was nothing
        to run.
        """
        prompt = raw.strip()
        images = [a for a in message.attachments
                  if a.content_type and a.content_type.startswith('image/')]
        inline_urls = _collect_embed_image_urls(message)
        if not prompt and not images and not inline_urls:
            return False

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return False

        await self._handle_prompt(message, channel, prompt, images,
                                  inline_urls)
        return True

    @commands.Cog.listener()
    async def on_ready(self):
        """Start the catch-up for whatever arrived while the bot was down.

        on_ready fires on every fresh IDENTIFY, never on a RESUME, where
        the gateway replays the events of the gap itself. So a second
        on_ready is exactly the case where messages went missing, and it
        gets its own catch-up. Only one runs at a time.
        """
        if self._catch_up_task is None or self._catch_up_task.done():
            self._catch_up_task = asyncio.create_task(self._catch_up())

    async def _catch_up(self):
        """Answer the messages the bot was not running to hear (R6.4).

        Nothing waits for this. The bot is online and answering live
        messages throughout, so this job is also the only thing that
        reports its own failures.

        Threads go one at a time. A turn is minutes of git and agent work,
        and a restart with a backlog has no reason to start a dozen
        recoveries at once. The turn itself reports a failure in its own
        thread, and that failure never reaches the next thread.

        This job never replays a message on_message has taken. The live
        path records its id, and this one skips both those ids and
        anything posted since it started. Recording rather than inferring
        is the point. on_ready arrives seconds after the gateway starts
        delivering messages, so timing alone would replay what the bot
        already answered while it was booting. The conversation lock then
        orders whatever the two paths hand over.

        Guild threads only, which is where conversations live, and the
        spec asks for no more. A ping in a channel with no thread, or a
        DM, keeps the durability it always had.
        """
        started_at = discord.utils.utcnow()
        try:
            try:
                threads = await self._discover_agent_threads()
            except Exception:
                # Said out loud. The usual summary would report zero
                # threads scanned, which reads as a bot that had nothing
                # to catch up on.
                traceback.print_exc()
                print('Agent catch-up: could not work out which threads to '
                      'scan, so nothing was caught up on.')
                return
            scanned = replayed = 0
            for thread in threads:
                scanned += 1
                try:
                    messages = await self._unanswered_messages(
                        thread, started_at)
                except Exception as error:
                    # A thread that stopped being readable halfway
                    # through hears about it (R6.4). That reply is a
                    # bot message, so the next catch-up takes the
                    # thread as answered rather than repeating this
                    # every restart; the owner posts again to retry.
                    traceback.print_exc()
                    await self._say_in_thread(
                        thread, f'{UNREADABLE_THREAD}{error}')
                    continue
                for message in messages:
                    # Checked again here. A message can sit behind
                    # minutes of earlier turns, and on_message may have
                    # taken it in the meantime.
                    if self._is_live(message, started_at):
                        continue
                    try:
                        if await self._replay(message, thread):
                            replayed += 1
                    except Exception:
                        # A turn reports its own trouble in its own
                        # thread; this is only for what escapes that.
                        traceback.print_exc()
            print(f'Agent catch-up: scanned {scanned} agent thread(s), '
                  f'replayed {replayed} message(s).')
        except Exception:
            traceback.print_exc()
        # The ids stay. A turn taken during this run may still be going
        # when a later catch-up scans its thread, and forgetting its id
        # would replay it. A process sees a handful of owner messages a
        # day, so the set stays small.

    async def _say_in_thread(self, thread: discord.Thread, text: str):
        """Tell a thread what went wrong in it, if it will still take one.

        A thread the bot cannot write to is no reason to lose the rest of
        the catch-up.
        """
        try:
            await thread.send(text[:MESSAGE_LIMIT])
        except (discord.HTTPException, aiohttp.ClientError,
                asyncio.TimeoutError) as error:
            print(f'Agent catch-up: could not report the trouble in thread '
                  f'{thread.id}: {error}')

    async def _discover_agent_threads(self) -> list[discord.Thread]:
        """Every agent thread the bot can see, active or archived (R6.5).

        This scans Discord, so a host that has lost its conversations
        table still finds them all. The table only shortcuts recognizing
        one (see _is_agent_thread). A channel the bot cannot read is
        printed and skipped, because it must not cost the other channels
        their catch-up.
        """
        found: list[discord.Thread] = []
        seen: set[int] = set()
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                candidates = [thread for thread in guild.threads
                              if thread.parent_id == channel.id]
                try:
                    async for thread in channel.archived_threads(limit=None):
                        candidates.append(thread)
                except (discord.HTTPException, aiohttp.ClientError,
                        asyncio.TimeoutError) as error:
                    # Forbidden included. The bot loses history
                    # permission in a channel now and then, and whatever
                    # the library already cached as active is still worth
                    # looking at.
                    print(f'Agent catch-up: could not list the archived '
                          f'threads of #{channel} in {guild}: {error}')
                for thread in candidates:
                    if thread.id in seen:
                        continue
                    seen.add(thread.id)
                    try:
                        if await self._is_agent_thread(thread):
                            found.append(thread)
                    except (discord.HTTPException, aiohttp.ClientError,
                            asyncio.TimeoutError) as error:
                        print(f'Agent catch-up: could not tell whether '
                              f'thread {thread.id} is one of ours: {error}')
        return found

    async def _unanswered_messages(self, thread: discord.Thread,
                                   started_at) -> list[discord.Message]:
        """The owner's messages this thread never got an answer to, oldest
        first (R6.4).

        Read from the newest end back to the bot's last message. Whatever
        the owner said after it is what went unanswered. A pull request
        announcement or an error notice is the bot having spoken, and the
        owner's reply to one still needs running. A message of nothing but
        muted harness lines is not the bot having spoken. It is news about
        a turn, and a turn that posted one and then died left the request
        before it unanswered. Anything the bot narrated on its own way to
        an answer is indistinguishable from the answer, so a turn
        interrupted after it started talking stays lost (R6.3). Anyone
        else in the thread is not part of the conversation.

        Skipped either way: what on_message has already taken, by id, and
        anything posted since this catch-up started.
        """
        assert self.bot.user is not None
        unanswered: list[discord.Message] = []
        answered = False
        async for message in thread.history(limit=None, oldest_first=False):
            if self._is_live(message, started_at):
                continue
            if message.author.id == self.bot.user.id:
                if _all_muted(message.content):
                    continue
                answered = True
                break
            if await self.bot.is_owner(message.author):
                unanswered.append(message)
        if not answered:
            # The message a thread grew from lives in the parent channel,
            # so the loop above never sees it. A thread the bot never got
            # a word into is one whose first turn died with the bot, and
            # that starter is the message to run. The exception is a
            # thread made after this job started, where the turn that made
            # it is running right now.
            starter = await self._fetch_starter(thread)
            if starter is not None and not self._is_live(starter, started_at):
                unanswered.append(starter)
        unanswered.reverse()
        return unanswered

    def _is_live(self, message, started_at) -> bool:
        """Whether this message is on_message's rather than the catch-up's:
        one it has taken, or one that arrived after the catch-up began."""
        return (message.id in self._live_ids
                or message.created_at >= started_at)

    async def _replay(self, message: discord.Message,
                      thread: discord.Thread) -> bool:
        """One missed message, run as a turn of its thread. A message that
        leads with a ping (the one that started the thread) loses it,
        exactly as the live path does."""
        # Recorded like a live message, so a later catch-up that scans
        # this thread while the turn is still running leaves it alone.
        self._live_ids.add(message.id)
        stripped = self._strip_mention(message.content)
        raw = message.content if stripped is None else stripped
        return await self._run_turn(message, thread, raw)

    @staticmethod
    def _maybe_thread(channel) -> bool:
        """Whether this channel could be a thread. A PartialMessageable is
        all the library hands over for a channel it isn't caching, which is
        what an archived thread looks like the moment a message revives
        it."""
        return isinstance(channel,
                          (discord.Thread, discord.PartialMessageable))

    async def _resolve_channel(self, message: discord.Message):
        """The message's channel as a real channel object, fetched when the
        gateway only gave us a stub."""
        channel = message.channel
        if isinstance(channel, discord.PartialMessageable):
            return await self.bot.fetch_channel(channel.id)
        return channel

    async def _is_agent_thread(self, thread: discord.Thread) -> bool:
        """Whether this thread holds one of our conversations.

        The durable answer lives in Discord. The bot created the thread,
        and the thread's id is the id of the owner's ping that started it,
        because a thread takes the id of the message it grew from. That
        holds with no local state at all, so a conversation survives
        anything that happens to this host.
        """
        assert self.bot.user is not None
        if thread.owner_id != self.bot.user.id:
            return False
        if self.service.has_conversation(thread.id):
            return True

        cached = self._agent_threads.get(thread.id)
        if cached is None:
            # Only a definite answer goes in the cache. A failed fetch
            # raises out of here rather than landing there as a "no".
            cached = await self._starter_is_agent_request(thread)
            self._agent_threads[thread.id] = cached
        return cached

    async def _starter_is_agent_request(self, thread: discord.Thread) -> bool:
        """Whether the message the thread grew from is an owner's ping."""
        starter = await self._fetch_starter(thread)
        if starter is None:
            return False
        return (self._strip_mention(starter.content) is not None
                and await self.bot.is_owner(starter.author))

    async def _fetch_starter(self, thread: discord.Thread):
        """The message the thread grew from, or None if it is gone."""
        parent = thread.parent
        if parent is None:
            parent = await self.bot.fetch_channel(thread.parent_id)
        try:
            return await parent.fetch_message(thread.id)
        except discord.NotFound:
            # No starter message left, so nothing ties the thread to a
            # request of ours. This is the one genuine "no" among the ways
            # the fetch can fail. The rest are the caller's to report.
            return None

    async def _recover_conversation(self, thread: discord.Thread, key: int):
        """Hand the service what Discord knows about a conversation it has
        no record of, so the turn continues the thread's branch and pull
        request instead of starting new ones (R3.3).

        Costs a read of the thread once per host. The service keeps the
        conversation afterwards, whether or not it found anything.
        """
        if self.service.has_conversation(key):
            return
        if not await self._is_agent_thread(thread):
            # The owner pinged the bot in some unrelated thread. There is
            # no conversation of ours to put back, and its history is
            # none of our business.
            return
        assert self.bot.user is not None
        announcements = [message.content
                         async for message in thread.history(
                             limit=None, oldest_first=True)
                         if message.author.id == self.bot.user.id]
        pr_urls = parse_pr_announcements(announcements)
        starter = await self._fetch_starter(thread)
        prompt = '' if starter is None else (
            self._strip_mention(starter.content) or '')
        await self.service.recover(key, prompt.strip(), pr_urls)

    async def _thread_history(self, thread: discord.Thread,
                              upto: discord.Message
                              ) -> ReconstructedHistory:
        """This thread's conversation, as prior history for a turn that
        has no transcript left to resume (R2c).

        The message that starts an agent thread lives in the parent
        channel rather than in the thread, so this fetches it separately
        and puts it in front. `upto` is the message being answered, which
        belongs at the end of the prompt as the new request, not in the
        history.
        """
        assert self.bot.user is not None
        starter = await self._fetch_starter(thread)
        # The starter is the first thing said in the conversation, except
        # when it is the message being answered, which is a first turn
        # replayed at startup. Then there is no prior history at all.
        messages = ([] if starter is None or starter.id == upto.id
                    else [starter])
        messages += [message async for message in thread.history(
            limit=None, oldest_first=True, before=upto)]
        async with aiohttp.ClientSession() as session:
            return await build_thread_history(
                messages, self.bot.user.id, upto.author.id,
                fetch=functools.partial(fetch_image_link, session))

    def _strip_mention(self, content: str) -> str | None:
        """What a message says after a leading ping of the bot, else None."""
        assert self.bot.user is not None

        for mention in (f'<@{self.bot.user.id}>', f'<@!{self.bot.user.id}>'):
            if content.startswith(mention):
                return content.removeprefix(mention)

        return None

    async def _handle_prompt(self, message: discord.Message, source,
                             prompt: str,
                             images: list[discord.Attachment] = (),
                             inline_urls: list[str] = ()):
        """Run one turn of a conversation.

        A typing indicator shows the agent working, and the bot creates a
        thread right before the first reply so the whole conversation,
        follow-ups included, lives inside it.

        `source` is the message's channel, which the caller has already
        resolved to a real channel object.
        """
        # In a thread or DM the conversation is already contained.
        # Elsewhere the bot creates a thread when the first message needs
        # a home.
        contained = isinstance(source, (discord.Thread, discord.DMChannel))
        if not contained and not self._can_create_thread(source):
            await message.reply(NO_THREAD_HERE)
            return

        # Typing wherever the next reply will land: the prompt's channel
        # until a thread exists, the thread once it does.
        typing = _TypingIndicator(source)

        # The channel its replies live in keys the conversation. A thread
        # created from a message shares that message's id, so the key
        # exists before the thread does.
        key = source.id if contained else message.id

        channel = source if contained else None
        status: discord.Message | None = None

        async def ensure_channel():
            nonlocal channel
            if channel is None:
                channel = await message.create_thread(
                    name=(prompt or 'image attachment')[:80],
                    auto_archive_duration=self.THREAD_ARCHIVE_MINUTES)
                self._agent_threads[channel.id] = True
                typing.move_to(channel)
            return channel

        async def set_status(text: str):
            """Harness news: one message, edited in place, threaded only if
            the conversation already is."""
            nonlocal status
            text = text[:MESSAGE_LIMIT]
            if status is not None:
                await status.edit(content=text)
            else:
                send = channel.send if channel else message.reply
                status = await send(text)

        async def send_in_thread(text: str):
            await self._send_message(await ensure_channel(), text)

        try:
            async with typing:
                image_data = []
                for a in images:
                    image_data.append((a.filename, await a.read()))
                if inline_urls:
                    async with aiohttp.ClientSession() as session:
                        for url in inline_urls:
                            try:
                                image_data.append(
                                    await fetch_image_link(session, url))
                            except Exception as error:
                                # The agent is about to answer without
                                # having seen it, which the owner should
                                # hear rather than guess at.
                                await send_in_thread(
                                    UNFETCHED_LINK.format(
                                        url=url, cause=one_line(error)))
                reconstruct = None
                if isinstance(source, discord.Thread):
                    try:
                        if await self._is_agent_thread(source):
                            await self._recover_conversation(source, key)
                            # Only ever called for a turn whose transcript
                            # is gone, and only in a thread of ours. Any
                            # other thread's history is none of our
                            # business.
                            reconstruct = functools.partial(
                                self._thread_history, source, message)
                    except (discord.HTTPException, aiohttp.ClientError,
                            asyncio.TimeoutError) as error:
                        # Reading the thread is how the bot finds its
                        # branch. Starting a fresh one instead would
                        # quietly abandon the pull request it already has.
                        await set_status(
                            f'{UNREADABLE_THREAD}{error}')
                        return
                report = await self.service.run(key, prompt,
                                                on_progress=send_in_thread,
                                                images=image_data,
                                                reconstruct=reconstruct)
        except WorkspaceError as error:
            await set_status(f'{WORKSPACE_ERROR}\n{error}')
            return
        except Exception:
            trace = traceback.format_exc()[-1500:]
            await set_status(f'{UNEXPECTED_ERROR}\n```py\n{trace}\n```')
            return

        answer = report.answer.strip()
        if answer:
            await send_in_thread(answer)
        elif not report.pull_requests and not report.timed_out:
            await set_status(NOTHING_SAID)

        if report.timed_out:
            await set_status(TIMEOUT_NOTICE)

        if report.pull_requests:
            target = await ensure_channel()
            for pull_request in report.pull_requests:
                await target.send(_pr_line(
                    PR_OPENED if pull_request.created else PR_UPDATED,
                    pull_request.repo_name, pull_request.url))

    # ------------------------------------------------------------------ #
    # Whid chat mode                                                      #
    # ------------------------------------------------------------------ #

    def _is_whid_message(self, message) -> bool:
        """Whether the message comes from the whid guild."""
        guild = getattr(message, 'guild', None)
        return guild is not None and guild.id == WHID_GUILD_ID

    async def _has_chat_intent(self, text: str) -> bool:
        """Whether a ping from a non-owner whid member wants a response.

        Runs the intent classifier in a dedicated subdirectory so its SDK
        session state never mixes with any chat conversation's state.
        """
        cwd = get_conversations_root() / 'chats' / '_classify'
        cwd.mkdir(parents=True, exist_ok=True)
        return await classify_intent(text, cwd)

    async def _is_chat_thread(self, thread: discord.Thread) -> bool:
        """Whether this thread holds a whid chat conversation.

        A chat thread is one the bot owns in the whid guild whose starter
        message is a non-owner member's ping. The answer is cached per
        thread, since like agent threads it can never change.
        """
        assert self.bot.user is not None
        if thread.owner_id != self.bot.user.id:
            return False
        guild = getattr(thread, 'guild', None)
        if guild is None or guild.id != WHID_GUILD_ID:
            return False
        cached = self._chat_threads.get(thread.id)
        if cached is None:
            cached = await self._starter_is_chat_request(thread)
            self._chat_threads[thread.id] = cached
        return cached

    async def _starter_is_chat_request(self, thread: discord.Thread) -> bool:
        """Whether the thread's starter is a non-owner whid member's ping."""
        starter = await self._fetch_starter(thread)
        if starter is None:
            return False
        if self._strip_mention(starter.content) is None:
            return False
        return not await self.bot.is_owner(starter.author)

    def _chat_session_dir(self, key: int) -> Path:
        """Working directory for the SDK when running a chat session."""
        return get_conversations_root() / 'chats' / str(key)

    async def _chat_thread_history(self, thread: discord.Thread,
                                    upto: discord.Message) -> str:
        """Prior messages in a chat thread, for a turn with no transcript.

        The starter message lives in the parent channel (not the thread),
        so it is fetched separately and prepended. `upto` is the message
        being answered and is excluded — it belongs at the end of the
        prompt as the new request, not in the history.
        """
        assert self.bot.user is not None
        starter = await self._fetch_starter(thread)
        messages = ([] if starter is None or starter.id == upto.id
                    else [starter])
        messages += [m async for m in thread.history(
            limit=None, oldest_first=True, before=upto)]
        return build_chat_thread_history(messages, self.bot.user.id)

    async def _run_chat_turn(self, message: discord.Message, source,
                             raw: str) -> None:
        """One turn of a whid chat conversation.

        Like _handle_prompt but without git, pull requests, or image
        handling. When the session transcript is gone the thread itself
        is read back as prior history, the same way the owner mode does
        it. Errors are printed to the console and swallowed rather than
        surfaced as detailed tracebacks to non-owner users.
        """
        prompt = raw.strip()
        if not prompt:
            return

        contained = isinstance(source, (discord.Thread, discord.DMChannel))
        if not contained and not self._can_create_thread(source):
            return

        typing = _TypingIndicator(source)
        key = source.id if contained else message.id
        channel = source if contained else None

        async def ensure_channel():
            nonlocal channel
            if channel is None:
                channel = await message.create_thread(
                    name=prompt[:80],
                    auto_archive_duration=self.THREAD_ARCHIVE_MINUTES)
                self._chat_threads[channel.id] = True
                typing.move_to(channel)
            return channel

        async def send_in_thread(text: str):
            await self._send_message(await ensure_channel(), text)

        try:
            async with typing:
                session_dir = self._chat_session_dir(key)
                session_dir.mkdir(parents=True, exist_ok=True)
                resume = self._chat_sessions.get(key)
                had_session = resume is not None
                if resume is not None and not local_chat_transcript_path(
                        session_dir, resume).exists():
                    resume = None

                # When there is no transcript to resume from, read the
                # thread back as prior history — same idea as the owner
                # mode's reconstruct path.
                history = None
                if resume is None and isinstance(source, discord.Thread):
                    try:
                        if await self._is_chat_thread(source):
                            history = await self._chat_thread_history(
                                source, message)
                    except (discord.HTTPException, aiohttp.ClientError,
                            asyncio.TimeoutError):
                        pass  # unreadable thread; agent starts fresh
                if history or (had_session and resume is None):
                    await send_in_thread(RELOADING_NOTE)

                try:
                    result = await run_chat_agent(prompt, session_dir,
                                                  send_in_thread, resume,
                                                  history)
                except SessionResumeError:
                    result = await run_chat_agent(prompt, session_dir,
                                                  send_in_thread,
                                                  history=history)
                if result.session_id is not None:
                    self._chat_sessions[key] = result.session_id
        except Exception:
            traceback.print_exc()
            return

        if result.final_text.strip():
            await send_in_thread(result.final_text.strip())

    def _can_create_thread(self, channel) -> bool:
        """Whether the bot could make a thread here, should a turn need one."""
        return (isinstance(channel, discord.TextChannel)
                and channel.permissions_for(
                    channel.guild.me).create_public_threads)

    async def _send_message(self, channel, text: str):
        for chunk in split_message(text):
            await channel.send(chunk)


class _TypingIndicator:
    """Discord's typing indicator, lit for an `async with` block.

    It shows in the channel given here until move_to points it somewhere
    else. It is dead once the block exits, so callers may move_to at any
    time without caring whether the run is still going.
    """

    def __init__(self, channel: discord.abc.Messageable):
        self._channel = channel
        self._task: asyncio.Task | None = None
        self._closed = False

    async def __aenter__(self):
        self._start()

    async def __aexit__(self, *_):
        self._closed = True
        self._cancel()

    def move_to(self, channel: discord.abc.Messageable):
        if self._closed:
            return
        self._channel = channel
        self._start()

    def _start(self):
        self._cancel()
        self._task = asyncio.create_task(self._type_in(self._channel))

    def _cancel(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None

    @staticmethod
    async def _type_in(channel: discord.abc.Messageable):
        try:
            # typing() refreshes the indicator itself until the context exits.
            async with channel.typing():
                await asyncio.sleep(math.inf)
        except discord.HTTPException:
            pass  # the indicator is a nicety; never abort the task over it
