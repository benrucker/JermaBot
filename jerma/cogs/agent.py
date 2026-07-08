"""Owner-only coding agent: ping JermaBot with a request, get answers or PRs.

When the owner pings the bot with something that isn't a command, the
message starts a coding-agent conversation. Replies live in a thread
created just before the first one, and further owner messages in that
thread continue the conversation — no ping needed. Conversations run
concurrently, each keeping one branch and at most one pull request per
repo, updated turn by turn. This cog handles only the Discord side —
detection, threads, message chunking, and reporting; conversations execute
behind AgentTaskService.
"""
import traceback

import discord
from discord.ext import commands

from jermabot import JermaBot
from .utils.agent_config import AGENT_TIMEOUT_SECONDS
from .utils.agent_service import AgentTaskService, WorkspaceError
from .utils.split_message import MESSAGE_LIMIT, split_message

CHECKING_EMOJI = '👋'


async def setup(bot):
    await bot.add_cog(Agent(bot))


class Agent(commands.Cog):
    def __init__(self, bot: JermaBot):
        self.bot = bot
        self.service = AgentTaskService()

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
        if stripped is not None:
            raw = stripped
        elif (self.service.has_conversation(message.channel.id)
                and self._is_own_thread(message.channel)):
            raw = message.content
        else:
            return
        prompt = raw.strip() or None
        if prompt is None:
            return

        if not await self.bot.is_owner(message.author):
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        await self._handle_prompt(message, prompt)

    def _strip_mention(self, content: str) -> str | None:
        """The rest of a message that leads with a ping of the bot, else None."""
        assert self.bot.user is not None

        for mention in (f'<@{self.bot.user.id}>', f'<@!{self.bot.user.id}>'):
            if content.startswith(mention):
                return content.removeprefix(mention)

        return None

    def _is_own_thread(self, channel) -> bool:
        assert self.bot.user is not None
        return (isinstance(channel, discord.Thread)
                and channel.owner_id == self.bot.user.id)

    async def _handle_prompt(self, message: discord.Message, prompt: str):
        """Run one turn: a reaction acknowledges the message, and a thread
        is created right before the first reply so the whole conversation —
        including any follow-ups — lives inside it."""
        # In a thread or DM the conversation is already contained; elsewhere
        # a thread is created when the first message needs a home.
        contained = isinstance(message.channel,
                               (discord.Thread, discord.DMChannel))
        if not contained and not self._can_create_thread(message):
            await message.reply("I can't make a thread here :(")
            return

        await self._acknowledge(message)

        # The conversation is keyed by the channel its replies live in. A
        # thread created from a message shares that message's id, so the
        # key is known before the thread exists.
        key = message.channel.id if contained else message.id

        channel = message.channel if contained else None
        status: discord.Message | None = None

        async def ensure_channel():
            nonlocal channel
            if channel is None:
                channel = await message.create_thread(name=prompt[:80])
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
            report = await self.service.run(key, prompt,
                                            on_progress=send_in_thread)
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

    async def _acknowledge(self, message: discord.Message):
        try:
            await message.add_reaction(CHECKING_EMOJI)
        except discord.HTTPException:
            pass  # the ack is a nicety; never abort the task over it

    def _can_create_thread(self, message: discord.Message) -> bool:
        """Whether a response thread could be made, should the task need one."""
        return (isinstance(message.channel, discord.TextChannel)
                and message.channel.permissions_for(
                    message.channel.guild.me).create_public_threads)

    async def _send_message(self, channel, text: str):
        for chunk in split_message(text):
            await channel.send(chunk)
