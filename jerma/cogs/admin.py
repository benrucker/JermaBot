from discord.ext import commands
import subprocess
import sys


def setup(bot):
    bot.add_cog(Admin(bot))


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.is_owner()
    @commands.command()
    async def moveto(self, ctx, id: int):
        await self.bot.get_cog('Control').connect_to_channel(self.bot.get_channel(id))

    async def shutdown(self):
        for g in self.bot.get_guildinfo().values():
            if g.is_snoozed():
                await self.bot.get_guild(g.id).me.edit(nick=None)
            g.exit()
        extensions = self.bot.extensions.copy()
        for ext in extensions:
            print(f'Unloading {ext}')
            self.bot.unload_extension(ext)
        await self.bot.close()

    @commands.command(hidden=True)
    async def perish(self, ctx):
        """Shut down the bot."""
        await self.shutdown()

    def _git_pull(self):
        """Run git pull and return the result."""
        if (sys.platform.startswith('win')):
            result = subprocess.run(['git', 'pull'], shell=True, text=True, capture_output=True)
        else:
            result = subprocess.run(['git pull'], shell=True, text=True, capture_output=True)
        return result

    async def _handle_pull(self, ctx) -> bool:
        """Run git pull and return true if there was content to pull."""
        try:
            result = self._git_pull()
        except Exception as e:
            print(e.with_traceback)
            await ctx.send('Something ain\'t right here, pal.')
            return
        if result.returncode != 0:
            await ctx.send('Uhh, gamer? Something didn\'t go right.')
            print(result.returncode)
            print(result.stdout)
        elif 'Already up to date' in result.stdout:
            return False
        else:
            return True

    @commands.is_owner()
    @commands.command()
    async def update(self, ctx, *args):
        """Update the bot."""
        updated = await self._handle_pull(ctx)
        if '-r' in args:
            await self._reload_all_cogs(ctx)
        if updated:
            await ctx.send('Patch applied, sister.')
        else:
            await ctx.send("Patch notes:\n - Lowered height by 2 inches to allow for more clown car jokes")
        return updated

    @commands.is_owner()
    @commands.command(hidden=True)
    async def reload(self, ctx, ext: str):
        self.bot.reload_extension(ext)
        await ctx.send('Reloadception complete.')

    async def _reload_all_cogs(self, ctx):
        await ctx.send('Reloading ' + ', '.join([(str(x)) for x in self.bot.extensions]))
        for ext in self.bot.extensions:
            self.bot.reload_extension(ext)

    @commands.is_owner()
    @commands.command()
    async def reloadall(self, ctx):
        await self._reload_all_cogs(ctx)

    @commands.is_owner()
    @commands.command(hidden=True)
    async def load(self, ctx, ext: str):
        self.bot.load_extension(ext)
        await ctx.send('Loading 99% complete.')

    @commands.is_owner()
    @commands.command()
    async def unload(self, ctx, ext: str):
        self.bot.unload_extension(ext)
        await ctx.send('Unloaded, pew pew.')
