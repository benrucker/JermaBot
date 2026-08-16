"""Coding agent for JermaBot: owner (full) and whid-member (read-only) modes.

Owner mode — ping or reply in a bot-owned thread — starts a coding-agent
conversation that can read, edit, and open pull requests. Conversations are
persistent across bot restarts.

Whid-member mode — ping only, in the whid Discord server — activates a
read-only agent that can answer questions about the code but cannot make
any edits or create pull requests. An intent classifier gates entry: a
message that looks like a misspelled command is silently ignored. Thread
continuations skip re-classification.
"""
import asyncio
import math
import traceback

import aiohttp
import discord
from discord.ext import commands

from jermabot import JermaBot
from .utils.agent_config import AGENT_TIMEOUT_SECONDS, WHID_GUILD_ID
from .utils.agent_runner import classify_intent
from .utils.agent_service import AgentTaskService, ReadonlyAgentTaskService, WorkspaceError
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
    def __init__(self, bot: JermaBot):
        self.bot = bot
        self.service = AgentTaskService()
        self.readonly_service = ReadonlyAgentTaskService()

    async def cog_load(self):
        self.service.start()
        self.readonly_service.start()

    async def cog_unload(self):
        self.service.close()
        self.readonly_service.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or self.bot.user is None:
            return

        stripped = self._strip_mention(message.content)

        if await self.bot.is_owner(message.author):
            # Owner path: thread continuations need no ping.
            if stripped is not None:
                raw = stripped
            elif (self.service.has_conversation(message.channel.id)
                  and self._is_own_thread(message.channel)):
                raw = message.content
            else:
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
            await self._handle_prompt(message, prompt, images, inline_urls)

        elif self._is_whid_member(message):
            # Whid-member path: read-only, intent-gated.
            # Thread continuations in an established readonly conversation skip
            # classification (the user is already in a deliberate session).
            is_ping = stripped is not None
            if is_ping:
                raw = stripped
            elif (self.readonly_service.has_conversation(message.channel.id)
                  and self._is_own_thread(message.channel)):
                raw = message.content
            else:
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
            if is_ping and not await classify_intent(prompt):
                return
            await self._handle_readonly_prompt(message, prompt, images, inline_urls)

    def _strip_mention(self, content: str) -> str | None:
        """The rest of a message that leads with a ping of the bot, else None."""
        assert self.bot.user is not None

        for mention in (f'<@{self.bot.user.id}>', f'<@!{self.bot.user.id}>'):
            if content.startswith(mention):
                return content.removeprefix(mention)

        return None

    def _is_whid_member(self, message: discord.Message) -> bool:
        return (message.guild is not None
                and message.guild.id == WHID_GUILD_ID)

    def _is_own_thread(self, channel) -> bool:
        assert self.bot.user is not None
        return (isinstance(channel, discord.Thread)
                and channel.owner_id == self.bot.user.id)

    async def _handle_prompt(self, message: discord.Message, prompt: str,
                              images: list[discord.Attachment] = (),
                              inline_urls: list[str] = ()):
        """Run one turn: a typing indicator shows the agent working, and a
        thread is created right before the first reply so the whole
        conversation — including any follow-ups — lives inside it."""
        # In a thread or DM the conversation is already contained; elsewhere
        # a thread is created when the first message needs a home.
        contained = isinstance(message.channel,
                               (discord.Thread, discord.DMChannel))
        if not contained and not self._can_create_thread(message):
            await message.reply("I can't make a thread here :(")
            return

        # Typing wherever the next reply will land: the prompt's channel
        # until a thread exists, the thread once it does.
        typing = _TypingIndicator(message.channel)

        # The conversation is keyed by the channel its replies live in. A
        # thread created from a message shares that message's id, so the
        # key is known before the thread exists.
        key = message.channel.id if contained else message.id

        channel = message.channel if contained else None
        status: discord.Message | None = None

        async def ensure_channel():
            nonlocal channel
            if channel is None:
                channel = await message.create_thread(
                    name=(prompt or 'image attachment')[:80])
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

    async def _handle_readonly_prompt(self, message: discord.Message,
                                       prompt: str,
                                       images: list[discord.Attachment] = (),
                                       inline_urls: list[str] = ()):
        """One turn of a whid-member readonly conversation.

        Identical Discord mechanics to _handle_prompt (typing, thread, chunked
        replies) but calls the readonly service — no edits, no PRs.
        """
        contained = isinstance(message.channel,
                               (discord.Thread, discord.DMChannel))
        if not contained and not self._can_create_thread(message):
            await message.reply("I can't make a thread here :(")
            return

        typing = _TypingIndicator(message.channel)
        key = message.channel.id if contained else message.id
        channel = message.channel if contained else None
        status: discord.Message | None = None

        async def ensure_channel():
            nonlocal channel
            if channel is None:
                channel = await message.create_thread(
                    name=(prompt or 'image attachment')[:80])
                typing.move_to(channel)
            return channel

        async def set_status(text: str):
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
                                        image_data.append((filename,
                                                           await resp.read()))
                            except Exception:
                                pass
                report = await self.readonly_service.run(
                    key, prompt,
                    on_progress=send_in_thread,
                    images=image_data,
                )
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
        elif not report.timed_out:
            await set_status('(The agent finished without saying anything.)')

        if report.timed_out:
            await set_status(
                f'(Hit the {AGENT_TIMEOUT_SECONDS // 60} minute limit.)')

    def _can_create_thread(self, message: discord.Message) -> bool:
        """Whether a response thread could be made, should the task need one."""
        return (isinstance(message.channel, discord.TextChannel)
                and message.channel.permissions_for(
                    message.channel.guild.me).create_public_threads)

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
