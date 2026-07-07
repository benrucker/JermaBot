"""Owner-only coding agent: ping JermaBot with a request, get answers or PRs.

When the owner pings the bot with something that isn't a command, the
message is forwarded to a coding-agent task. This cog handles only the
Discord side — detection, threads, message chunking, and reporting; how
tasks execute lives behind AgentTaskService.
"""
import traceback

import discord
from discord.ext import commands

from jermabot import JermaBot
from .utils.agent_config import AGENT_TIMEOUT_SECONDS
from .utils.agent_service import AgentBusyError, AgentTaskService, WorkspaceError
from .utils.split_message import MESSAGE_LIMIT, split_message

BUSY_MESSAGE = "Hold up, gamer, I'm busy."
CHECKING_EMOJI = discord.PartialEmoji.from_str('<a:jermaDetective:863205690764165160>')


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

        prompt = self._extract_prompt(message.content)
        if prompt is None:
            return

        if not await self.bot.is_owner(message.author):
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        if self.service.busy:
            await message.reply(BUSY_MESSAGE)
            return

        await self._handle_prompt(message, prompt)

    def _extract_prompt(self, content: str) -> str | None:
        """Return the prompt if the message is a direct ping, else None."""
        assert self.bot.user is not None

        for mention in (f'<@{self.bot.user.id}>', f'<@!{self.bot.user.id}>'):
            if content.startswith(mention):
                return content.removeprefix(mention).strip() or None

        return None

    async def _handle_prompt(self, message: discord.Message, prompt: str):
        """Run the task: a reaction acknowledges the ping, and a thread is
        created only once there's more than a single reply's worth of
        conversation to hold. A lone message-sized answer with nothing to
        publish is just a reply in the channel."""
        # In a thread or DM the conversation is already contained; elsewhere
        # a thread is created when the first message needs a home.
        contained = isinstance(message.channel,
                               (discord.Thread, discord.DMChannel))
        if not contained and not self._can_create_thread(message):
            await message.reply("I can't make a thread here :(")
            return

        await self._acknowledge(message)

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
            report = await self.service.run(prompt, on_progress=send_in_thread)
        except AgentBusyError:
            await set_status(BUSY_MESSAGE)
            return
        except WorkspaceError as error:
            await set_status(f'Workspace error:\n{error}')
            return
        except Exception:
            trace = traceback.format_exc()[-1500:]
            await set_status(f'Error:\n```py\n{trace}\n```')
            return

        answer = report.answer.strip()
        if answer:
            if (channel is None and not report.pull_requests
                    and len(answer) <= MESSAGE_LIMIT):
                await message.reply(answer)
            else:
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
                await target.send(f'Pull request for **{pull_request.repo_name}**: {pull_request.url}')

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
