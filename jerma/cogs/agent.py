"""Owner-only coding agent: ping JermaBot with a request, get answers or PRs.

When the owner pings the bot with something that isn't a command, the
message starts a coding-agent conversation. Replies live in a thread
created just before the first one, and further owner messages in that
thread continue the conversation — no ping needed, forever. Conversations
run concurrently, each keeping one branch and at most one pull request per
repo, updated turn by turn. This cog handles only the Discord side —
recognition, threads, message chunking, and reporting; conversations
execute behind AgentTaskService.

Agent threads are recognized from Discord itself, never from local state,
so a thread keeps working across restarts, evictions, and a wiped host: the
bot owns the thread, and the thread's id is the id of its starter message,
which is the owner's original ping. Checking that pair identifies an agent
thread with nothing but Discord. The service's conversation table is
consulted first as a fast path, and each answer is cached per thread.
Threads Discord auto-archived count too — a message unarchives one, and
that MESSAGE_CREATE can arrive before the thread is back in the library's
cache, leaving a PartialMessageable to be fetched.

A recognized thread the service has never heard of is a conversation whose
identity record this host lost. The thread itself remembers some of it:
every pull request the bot opened was announced in it, and those
announcements are parsed back out here and handed to the service, which
turns them into a branch (R3.3). A thread with no announcements is looked
up on GitHub by the message that started it.

The thread is also the last resort for what the agent knew (R2c). When a
conversation's transcript is gone from this host and from the backup, the
service asks for build_thread_history(), which reads the whole thread back
— the owner's messages, the agent's replies, and the images, re-downloaded
— and hands it over as prior history for a new session. The bot's own
fixed messages are not the agent's words and must not come back as them,
so every shape this cog posts (the muted subtext lines, pull request
announcements, timeouts, error replies) is written from a constant here
and read back as a plain "[harness] ..." fact.
"""
import asyncio
import functools
import math
import re
import traceback

import aiohttp
import discord
from discord.ext import commands

from jermabot import JermaBot
from .utils.agent_config import AGENT_TIMEOUT_SECONDS
from .utils.agent_service import (
    AgentTaskService,
    ReconstructedHistory,
    WorkspaceError,
)
from .utils.split_message import MESSAGE_LIMIT, split_message


PR_OPENED = 'Pull request'
PR_UPDATED = 'Updated the pull request'


def _pr_line(verb: str, repo: str, url: str) -> str:
    """The bot's pull request announcement. Written through here by
    _handle_prompt and read back through the pattern below, so rewording
    it cannot leave old announcements looking like the agent's words."""
    return f'{verb} for **{repo}**: {url}'


# The same line with its three fields opened up, built from the writer
# rather than typed out again.
_PR_ANNOUNCEMENT = re.compile(
    '^' + re.escape(_pr_line('\x00', '\x01', '\x02'))
    .replace('\x00', f'(?P<verb>{re.escape(PR_OPENED)}'
                     f'|{re.escape(PR_UPDATED)})')
    .replace('\x01', r'(?P<repo>[^*]+)')
    .replace('\x02', r'(?P<url>https://\S+)') + '$')
# A muted subtext line: everything the harness says about itself, from the
# recovery line to a merge conflict to a backup that would not push.
_MUTED_LINE = re.compile(r'^-# _(?P<text>.*)_$')

# The rest of the bot's fixed messages, named so that reading a thread back
# recognizes exactly what writing one produces.
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
# The writer's own sentence with only the minute count left open: the
# limit is configuration, so a thread holds notices naming other numbers.
_TIMEOUT_NOTICE = re.compile(
    '^' + re.escape(_timeout_notice('\x00')).replace('\x00', r'\d+') + '$')

HISTORY_HEADING = ('Prior history of this conversation (reconstructed from '
                   'the Discord thread; tool calls and results were lost):')
# A pasted image link the bot could not fetch, in the muted harness style
# so a thread read back later takes it as harness news, not as an answer.
UNFETCHED_LINK = "-# _Couldn't fetch the image link {url}: {cause}._"


def _one_line(text: str) -> str:
    return ' '.join(str(text).split())[:300]


def _harness_message(content: str) -> str | None:
    """What a whole message of the bot's own means, if it is one of its
    fixed shapes, so the agent hears it as something that happened rather
    than as something it said (R2c.1b)."""
    if content.startswith(WORKSPACE_ERROR):
        rest = _one_line(content[len(WORKSPACE_ERROR):])
        return f'The turn failed: {rest}'
    if content.startswith(f'{UNEXPECTED_ERROR}\n```py'):
        return 'The turn failed with an unexpected error.'
    if _TIMEOUT_NOTICE.match(content):
        return 'The turn timed out and published what it had finished.'
    if content == NOTHING_SAID:
        return 'The turn ended without a reply.'
    if content == NO_THREAD_HERE or content.startswith(
            (UNREACHABLE_DISCORD, UNREADABLE_THREAD)):
        return f'A message went unanswered: {_one_line(content)}'
    return None


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
    # Bounded: a rebuild fetches every link in the thread under the
    # conversation lock, and a stalled host must not hold it for long.
    async with session.get(url, timeout=IMAGE_LINK_TIMEOUT) as response:
        content_type = response.headers.get('Content-Type', '')
        if response.status != 200:
            raise ValueError(f'HTTP {response.status}')
        if not content_type.startswith('image/'):
            raise ValueError(f'not an image ({content_type or "no type"})')
        length = response.headers.get('Content-Length')
        if length is not None and int(length) > IMAGE_LINK_MAX_BYTES:
            raise ValueError(f'too large ({length} bytes)')
        data = await response.content.read(IMAGE_LINK_MAX_BYTES + 1)
        if len(data) > IMAGE_LINK_MAX_BYTES:
            raise ValueError(f'too large (over {IMAGE_LINK_MAX_BYTES} bytes)')
        filename = url.rstrip('/').split('/')[-1].split('?')[0] or 'image'
        return filename, data


def _stamp(message) -> str:
    return message.created_at.strftime('%Y-%m-%d %H:%M UTC')


async def _owner_block(message, fetch
                       ) -> tuple[list[str], list[tuple[str, bytes]]]:
    """One owner message as history, with its images re-downloaded.

    Attachments come back from Discord; a pasted image link is an embed
    with nothing of ours behind it, so it goes through `fetch`, the
    caller's downloader. Either kind that cannot be had is named as
    missing rather than dropped, so the agent knows the message had a
    picture in it (R2c.2).
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
                         f'no longer has this file ({_one_line(error)})]')
            continue
        images.append((name, data))
        lines.append(f'[image attachment: {name}]')
    for url in _collect_embed_image_urls(message):
        try:
            filename, data = await fetch(url)
        except Exception as error:
            # Anything at all: this is somebody else's web server, and a
            # named gap is worth more to the agent than a lost picture.
            lines.append(f'[image link {url}: could not be fetched '
                         f'({_one_line(error)})]')
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

    Losses are the spec's (R2c.3): tool calls, tool results, and how the
    agent interleaved them with what it said.
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
                    (facts if fact else said).append(fact or line)
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


def parse_pr_announcements(contents: list[str]) -> dict[str, str]:
    """The pull request each repo has, read back out of the bot's own
    messages in a thread (R3.3).

    `contents` is the thread's messages in order, oldest first. The last
    announcement for a repo wins — a conversation whose branch was merged
    away opens a second pull request and announces that one too — and the
    dict keeps announcement order, so its last entry is the thread's most
    recent pull request.
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
    """Return image URLs from Discord embeds created for pasted image links."""
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

    async def cog_load(self):
        self.service.start()

    async def cog_unload(self):
        self.service.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or self.bot.user is None:
            return

        # A conversation in a thread the bot created hears every owner
        # message; anywhere else — channels, DMs, other people's threads —
        # it takes a ping to start or continue one.
        stripped = self._strip_mention(message.content)
        if stripped is None and not self._maybe_thread(message.channel):
            return

        try:
            # Everything past here can cost an API call, and nobody but the
            # owner is answered anyway.
            if not await self.bot.is_owner(message.author):
                return
            channel = await self._resolve_channel(message)
            if stripped is not None:
                raw = stripped
            elif (isinstance(channel, discord.Thread)
                    and await self._is_agent_thread(channel)):
                raw = message.content
            else:
                return
        except (discord.HTTPException, aiohttp.ClientError,
                asyncio.TimeoutError) as error:
            # Recognition needs Discord, and Discord can be down, forbid the
            # fetch, or time out. None of that may swallow the owner's
            # message: say what happened, and cache nothing, so the next
            # message tries again.
            await message.reply(f'{UNREACHABLE_DISCORD}{error}')
            return

        prompt = raw.strip()
        images = [a for a in message.attachments
                  if a.content_type and a.content_type.startswith('image/')]
        inline_urls = _collect_embed_image_urls(message)
        if not prompt and not images and not inline_urls:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        await self._handle_prompt(message, channel, prompt, images,
                                  inline_urls)

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

        The durable answer lives in Discord: the bot created the thread,
        and the thread's id is the id of the owner's ping that started it
        (a thread takes the id of the message it grew from). That holds
        with no local state at all, so a conversation survives anything
        that happens to this host.
        """
        assert self.bot.user is not None
        if thread.owner_id != self.bot.user.id:
            return False
        if self.service.has_conversation(thread.id):
            return True

        cached = self._agent_threads.get(thread.id)
        if cached is None:
            # Only a definite answer is cached; a failed fetch raises out
            # of here rather than being remembered as a "no".
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
            # request of ours. The one genuine "no" among the ways this
            # fetch can fail; the rest are the caller's to report.
            return None

    async def _recover_conversation(self, thread: discord.Thread, key: int):
        """Hand the service what Discord knows about a conversation it has
        no record of, so the turn continues the thread's branch and pull
        request instead of starting new ones (R3.3).

        Costs a read of the thread once per host: the service keeps the
        conversation afterwards, whether or not anything was found.
        """
        if self.service.has_conversation(key):
            return
        if not await self._is_agent_thread(thread):
            # The owner pinged the bot in some unrelated thread: there is
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
        channel rather than in the thread, so it is fetched separately and
        put in front; `upto` is the message being answered, which belongs
        at the end of the prompt as the new request, not in the history.
        """
        assert self.bot.user is not None
        starter = await self._fetch_starter(thread)
        messages = [] if starter is None else [starter]
        messages += [message async for message in thread.history(
            limit=None, oldest_first=True, before=upto)]
        async with aiohttp.ClientSession() as session:
            return await build_thread_history(
                messages, self.bot.user.id, upto.author.id,
                fetch=functools.partial(fetch_image_link, session))

    def _strip_mention(self, content: str) -> str | None:
        """The rest of a message that leads with a ping of the bot, else None."""
        assert self.bot.user is not None

        for mention in (f'<@{self.bot.user.id}>', f'<@!{self.bot.user.id}>'):
            if content.startswith(mention):
                return content.removeprefix(mention)

        return None

    async def _handle_prompt(self, message: discord.Message, source,
                             prompt: str,
                             images: list[discord.Attachment] = (),
                             inline_urls: list[str] = ()):
        """Run one turn: a typing indicator shows the agent working, and a
        thread is created right before the first reply so the whole
        conversation — including any follow-ups — lives inside it.

        `source` is the message's channel, resolved to a real channel
        object by the caller.
        """
        # In a thread or DM the conversation is already contained; elsewhere
        # a thread is created when the first message needs a home.
        contained = isinstance(source, (discord.Thread, discord.DMChannel))
        if not contained and not self._can_create_thread(source):
            await message.reply(NO_THREAD_HERE)
            return

        # Typing wherever the next reply will land: the prompt's channel
        # until a thread exists, the thread once it does.
        typing = _TypingIndicator(source)

        # The conversation is keyed by the channel its replies live in. A
        # thread created from a message shares that message's id, so the
        # key is known before the thread exists.
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
                                        url=url, cause=_one_line(error)))
                reconstruct = None
                if isinstance(source, discord.Thread):
                    try:
                        if await self._is_agent_thread(source):
                            await self._recover_conversation(source, key)
                            # Only ever called for a turn whose transcript
                            # is gone, and only in a thread of ours: any
                            # other thread's history is none of our
                            # business.
                            reconstruct = functools.partial(
                                self._thread_history, source, message)
                    except (discord.HTTPException, aiohttp.ClientError,
                            asyncio.TimeoutError) as error:
                        # Reading the thread is how its branch is found;
                        # starting a fresh one instead would quietly
                        # abandon the pull request it already has.
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

    def _can_create_thread(self, channel) -> bool:
        """Whether a response thread could be made, should the task need one."""
        return (isinstance(channel, discord.TextChannel)
                and channel.permissions_for(
                    channel.guild.me).create_public_threads)

    async def _send_message(self, channel, text: str):
        for chunk in split_message(text):
            await channel.send(chunk)


class _TypingIndicator:
    """A "typing…" indicator lit for the duration of an `async with` block —
    in the channel given here until move_to points it somewhere else. Dead
    once the block exits, so callers may move_to at any time without caring
    whether the run is still going."""

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
