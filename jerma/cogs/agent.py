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
"""
import asyncio
import math
import traceback

import aiohttp
import discord
from discord.ext import commands

from jermabot import JermaBot
from .utils.agent_config import AGENT_TIMEOUT_SECONDS
from .utils.agent_service import AgentTaskService, WorkspaceError
from .utils.split_message import MESSAGE_LIMIT, split_message


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
            await message.reply("I couldn't reach Discord to work out where "
                                f"this message lives: {error}")
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
        parent = thread.parent
        if parent is None:
            parent = await self.bot.fetch_channel(thread.parent_id)
        try:
            starter = await parent.fetch_message(thread.id)
        except discord.NotFound:
            # No starter message left, so nothing ties the thread to a
            # request of ours. The one genuine "no" among the ways this
            # fetch can fail; the rest are the caller's to report.
            return False
        return (self._strip_mention(starter.content) is not None
                and await self.bot.is_owner(starter.author))

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
            await message.reply("I can't make a thread here :(")
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
                                async with session.get(url) as resp:
                                    ct = resp.headers.get('Content-Type', '')
                                    if resp.status == 200 and ct.startswith('image/'):
                                        filename = (url.rstrip('/').split('/')[-1]
                                                    .split('?')[0] or 'image')
                                        image_data.append((filename, await resp.read()))
                            except Exception:
                                pass
                report = await self.service.run(key, prompt,
                                                on_progress=send_in_thread,
                                                images=image_data)
        except WorkspaceError as error:
            await set_status(f'Workspace error:\n{error}')
            return
        except Exception:
            trace = traceback.format_exc()[-1500:]
            await set_status(f'Error:\n```py\n{trace}\n```')
            return

        answer = report.answer.strip()
        if answer:
            await send_in_thread(answer)
        elif not report.pull_requests and not report.timed_out:
            await set_status('(The agent finished without saying anything.)')

        if report.timed_out:
            await set_status(
                f'(Hit the {AGENT_TIMEOUT_SECONDS // 60} minute limit. '
                'Published whatever it finished.)')

        if report.pull_requests:
            target = await ensure_channel()
            for pull_request in report.pull_requests:
                verb = ('Pull request' if pull_request.created
                        else 'Updated the pull request')
                await target.send(f'{verb} for '
                                  f'**{pull_request.repo_name}**: '
                                  f'{pull_request.url}')

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
