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
from .utils.agent_service import AgentBusyError, AgentTaskService, WorkspaceError
from .utils.split_message import split_message

BUSY_MESSAGE = "Hold up, gamer, I'm busy."


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
        channel = await self._make_response_thread(message, prompt)

        await channel.send('Lemme check rq :jermaDetective:')

        try:
            report = await self.service.run(
                prompt, on_update=lambda text: self._send_message(channel, text))
        except AgentBusyError:
            await channel.send(BUSY_MESSAGE)
            return
        except WorkspaceError as error:
            await self._send_message(channel, f'Workspace error:\n{error}')
            return
        except Exception:
            trace = traceback.format_exc()[-1500:]
            await self._send_message(channel, f'Error:\n```py\n{trace}\n```')
            return

        for pull_request in report.pull_requests:
            await channel.send(f'Pull request for **{pull_request.repo_name}**: {pull_request.url}')

    async def _make_response_thread(self, message: discord.Message, prompt: str):
        """Thread off the message where possible; otherwise reply in place."""
        if isinstance(message.channel, discord.TextChannel):
            try:
                return await message.create_thread(name=prompt[:80])
            except discord.HTTPException:
                pass
        return message.channel

    async def _send_message(self, channel, text: str):
        for chunk in split_message(text):
            await channel.send(chunk)
