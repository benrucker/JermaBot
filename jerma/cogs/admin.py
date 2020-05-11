from discord.ext import commands
import subprocess
import sys


def setup(bot):
    bot.add_cog(Admin(bot))


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def shutdown(self):
        for g in self.bot.get_guildinfo().values():
            if g.is_snoozed():
                await self.bot.get_guild(g.id).me.edit(nick=None)
            g.exit()
        await self.bot.close()

    @commands.command(hidden=True)
    async def perish(self, ctx):
        """Shut down the bot."""
        await self.shutdown()

    @commands.is_owner()
    @commands.command(hidden=True)
    async def update(self, ctx):
        """Update the bot."""
        try:
            if (sys.platform.startswith('win')):
                result = subprocess.run(['git', 'pull'], shell=True, text=True, capture_output=True)
            else:
                result = subprocess.run(['git pull'], shell=True, text=True, capture_output=True)
        except Exception as e:
            print(e.with_traceback)
            await ctx.send('Something ain\'t right here, pal.')
            return

        if result.returncode != 0:
            await ctx.send('Uhh, gamer? Something didn\'t go right.')
            print(result.returncode)
            print(result.stdout)
        elif 'Already up to date' in result.stdout:
            await ctx.send("Patch notes:\n - Lowered height by 2 inches to allow for more clown car jokes")
        else:
            await ctx.send('Patch applied, sister.')
            await self.shutdown()

    @commands.is_owner()
    @commands.command(hidden=True)
    async def reload(self, ctx, ext: str):
        self.bot.reload_extension(ext)
        await ctx.send('Reloadception complete.')

    @commands.is_owner()
    @commands.command()
    async def reloadall(self, ctx):
        raise NotImplementedError()

    @commands.is_owner()
    @commands.command(hidden=True)
    async def load(self, ctx, ext: str):
        self.bot.load_extension(ext)
        await ctx.send('Loading 99% complete.')
