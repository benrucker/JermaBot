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
        """Run the task in a thread: a reaction acknowledges the ping, harness
        news shares one editable status message in the thread, and the agent's
        own output and PR links are plain messages there."""
        try:
            channel = await self._make_response_thread(message, prompt)
        except discord.HTTPException:
            await message.reply(
                "I can't make a thread here :("
            )
            return

        await self._acknowledge(message)

        status: discord.Message | None = None

        async def set_status(text: str):
            nonlocal status
            text = text[:MESSAGE_LIMIT]
            if status is None:
                status = await channel.send(text)
            else:
                await status.edit(content=text)

        try:
            report = await self.service.run(
                prompt, on_text=lambda text: self._send_message(channel, text))
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

        if report.timed_out:
            await set_status(
                f'(Hit the {AGENT_TIMEOUT_SECONDS // 60} minute limit. '
                'Published whatever it finished.)')

        for pull_request in report.pull_requests:
            await channel.send(f'Pull request for **{pull_request.repo_name}**: {pull_request.url}')

    async def _acknowledge(self, message: discord.Message):
        try:
            await message.add_reaction(CHECKING_EMOJI)
        except discord.HTTPException:
            pass  # the ack is a nicety; never abort the task over it

    async def _make_response_thread(self, message: discord.Message, prompt: str):
        """Thread off the message; threads and DMs already contain the reply."""
        if isinstance(message.channel, (discord.Thread, discord.DMChannel)):
            return message.channel
        return await message.create_thread(name=prompt[:80])

    async def _send_message(self, channel, text: str):
        for chunk in split_message(text):
            await channel.send(chunk)
