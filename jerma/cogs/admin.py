from contextlib import redirect_stdout
import io
import random
import subprocess
import sys
import textwrap
import traceback
from typing import Optional

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Bot, Context


ADMIN_GUILDS = [
    571004411137097731,
    173840048343482368,
]


async def setup(bot):
    await bot.add_cog(Admin(bot))


class Admin(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot: Bot = bot

    @commands.is_owner()
    @commands.hybrid_command()
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def perish(self, ctx: Context):
        """Shut down the bot."""
        await ctx.send("o7")
        await self.shutdown()

    async def shutdown(self):
        for g in self.bot.get_guildinfo().values():
            if g.is_snoozed():
                await self.bot.get_guild(g.id).me.edit(nick=None)
        extensions = self.bot.extensions.copy()
        for ext in extensions:
            print(f'Unloading {ext}')
            self.bot.unload_extension(ext)
        await self.bot.close()

    @commands.is_owner()
    @commands.hybrid_command()
    @app_commands.describe(reload="Reloads all cogs if true.")
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def update(self, ctx: Context, reload: bool | None):
        """Update the bot."""
        updated = await self._handle_pull(ctx)
        if reload:
            await self._reload_all_cogs(ctx)

        if updated:
            await ctx.send('Patch applied, sister.')
        else:
            await ctx.send("Patch notes:\n - Lowered height by 2 inches to allow for more clown car jokes")
        return updated

    def _git_pull(self):
        """Run git pull and return the result."""
        if (sys.platform.startswith('win')):
            result = subprocess.run(
                ['git', 'pull'],
                shell=True,
                text=True,
                capture_output=True
            )
        else:
            result = subprocess.run(
                ['git pull'],
                shell=True,
                text=True,
                capture_output=True
            )
        return result

    async def _handle_pull(self, ctx) -> bool:
        """Run git pull and return true if an update succeeded."""
        try:
            result = self._git_pull()
        except Exception as e:
            print(e.with_traceback)
            await ctx.send('Something ain\'t right here, pal.')
            return False
        print(result.returncode)
        print(result.stdout)
        if result.returncode != 0:
            await ctx.send('Uhh, gamer? Something didn\'t go right.')
            return False
        elif 'Already up to date' in result.stdout:
            return False
        else:
            return True

    @commands.is_owner()
    @commands.hybrid_command()
    @app_commands.describe(ext="The cog/extension to reload")
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def reload(self, ctx: Context, ext: str):
        """Reload an extension"""
        await self.bot.reload_extension(ext)
        await ctx.send('Reloadception complete.')

    @commands.is_owner()
    @commands.hybrid_command()
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def reloadall(self, ctx: Context):
        """Reload all extensions"""
        await self._reload_all_cogs(ctx)

    async def _reload_all_cogs(self, ctx: Context):
        await ctx.send('Reloading ' + ', '.join([(str(x)) for x in self.bot.extensions]))
        for ext in self.bot.extensions.copy():
            await self.bot.reload_extension(ext)

    @commands.is_owner()
    @commands.hybrid_command()
    @app_commands.describe(ext="The cog/extension to load")
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def load(self, ctx: Context, ext: str):
        """Load an extension"""
        await self.bot.load_extension(ext)
        await ctx.send('Loading 99% complete.')

    @commands.is_owner()
    @commands.hybrid_command()
    @app_commands.describe(ext="The cog/extension to unload")
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def unload(self, ctx: Context, ext: str):
        """Unload an extension"""
        await self.bot.unload_extension(ext)
        await ctx.send('Unloaded, pew pew.')

    @commands.is_owner()
    @commands.hybrid_command()
    @app_commands.describe(lightweight="Skips emoji processing if true")
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def diag(self, ctx: Context, lightweight: Optional[bool]):
        """Get some diagnostic information"""
        await self.send_guild_diag(ctx)
        await self.send_message_diag(ctx)
        await self.send_vc_diag(ctx)
        await self.send_latency_diag(ctx)
        if not lightweight:
            await self.send_emoji_diag(ctx)

    async def send_guild_diag(self, ctx: Context):
        out = ''
        out += f'Logged into **{len(self.bot.guilds)}** guilds:\n'
        for guild in list(self.bot.guilds):
            if len(out) > 1900:
                out += '    ...'
                break
            out += f'    {guild.name} : {str(guild.id)[:5]}\n'
        await ctx.send(out[:2000])

    async def send_message_diag(self, ctx: Context):
        await ctx.send(f'There are currently {len(self.bot.cached_messages)} cached messages.')

    async def send_vc_diag(self, ctx: Context):
        await ctx.send(f'There are currently {len(self.bot.voice_clients)} cached voice clients.')

    async def send_latency_diag(self, ctx: Context):
        latency = self.bot.latency * 1000
        await ctx.send(f'Current websocket latency: {latency:.5} ms')

    async def send_emoji_diag(self, ctx: Context):
        out = f'JermaBot has access to {len(self.bot.emojis)} emojis including '
        e = random.sample(self.bot.emojis, 3)
        out += f'{e[0]}, {e[1]}, and {e[2]}.'
        await ctx.send(out)

    @commands.is_owner()
    @commands.hybrid_command()
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def usercount(self, ctx: Context):
        """Get the number of users JermaBot is connected to"""
        count = 0
        for guild in self.bot.guilds:
            count += guild.member_count
        await ctx.send(count)

    @commands.is_owner()
    @commands.hybrid_command()
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def sync_guild_commands(self, ctx: Context):
        """Sync application commands specific to this guild"""
        print('Syncing guild commands')
        cmds = await self.bot.tree.sync(guild=ctx.guild)
        print(f'Done syncing commands.\n{cmds}')
        await ctx.send(f'Done...\n```{cmds}```')

    @commands.is_owner()
    @commands.hybrid_command()
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def sync_here(self, ctx: Context):
        """Copy global commands to this guild & sync this guild"""
        await ctx.defer()
        self.bot.tree.copy_global_to(guild=ctx.guild)
        cmds = await self.bot.tree.sync(guild=ctx.guild)
        print('synced:', cmds)
        await ctx.send(f"Synced tree here.\n```{cmds}```")

    @commands.is_owner()
    @commands.hybrid_command()
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def sync_global(self, ctx: Context):
        """Sync global application commands"""
        cmds = await self.bot.tree.sync()
        print('synced:', cmds)
        await ctx.send(f"Synced tree globally.\n```{cmds}```")

    @commands.is_owner()
    @commands.command(hidden=True, name='eval')
    async def _eval(self, ctx: Context, *, body: str):
        """Borrowed from https://github.com/Rapptz/RoboDanny/blob/90d31d4d86ea3808179e0974bcab99976bc429d8/cogs/admin.py#L215"""

        env = {
            'self': self,
            'bot': self.bot,
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': ctx.message,
        }

        env.update(globals())

        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await ctx.send(f'```py\n{e.__class__.__name__}: {e}\n```')

        func = env['func']
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except Exception as e:
            value = stdout.getvalue()
            await ctx.send(f'```py\n{value}{traceback.format_exc()}\n```')
        else:
            value = stdout.getvalue()
            try:
                await ctx.message.add_reaction('\u2705')
            except:
                pass

            if ret is None:
                if value:
                    await ctx.send(f'```py\n{value}\n```')
            else:
                self._last_result = ret
                await ctx.send(f'```py\n{value}{ret}\n```')

    def cleanup_code(self, content: str) -> str:
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith('```') and content.endswith('```'):
            return '\n'.join(content.split('\n')[1:-1])

        # remove `foo`
        return content.strip('` \n')

    @app_commands.command()
    @app_commands.describe(content="stuff to spit back out, kinda sneakily")
    @app_commands.guilds(571004411137097731, 173840048343482368)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def spit(self, ctx: Context, content: str):
        """Spit fire"""
        await ctx.response.send_message('spitting', ephemeral=True)
        await ctx.channel.send(content)
