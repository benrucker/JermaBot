from discord.ext import commands
import discord
import traceback
import sys
from collections import OrderedDict


def setup(bot):
    bot.add_cog(Scoreboard(bot))


class Scoreboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.is_owner()
    @commands.command()
    async def resetscore(self, ctx, *args):
        if not args:
            raise discord.InvalidArgument
        name = ' '.join(args[0:])
        async for u in ctx.guild.fetch_members():
            if name.lower() in [u.name.lower(), u.display_name.lower()]:
                user = u

        g = self.bot.get_guildinfo(ctx.guild.id)
        g.reset_score(user.id)

    @commands.command()
    async def addpoint(self, ctx, *args):
        if not args:
            raise discord.InvalidArgument
        name = ' '.join(args[0:])
        async for u in ctx.guild.fetch_members():
            if name.lower() in [u.name.lower(), u.display_name.lower()]:
                user = u

        g = self.bot.get_guildinfo(ctx.guild.id)
        g.add_point(user.id)

        await ctx.send(user.name + ' now has a whopping ' + str(g.leaderboard[user.id]) + ' points. Wow!')

    @commands.command()
    async def leaderboard(self, ctx):
        try:
            lb = self.bot.get_guildinfo(ctx.guild.id).leaderboard
            lbord = OrderedDict(sorted(lb.items(), key=lambda t: t[1], reverse=True))
            out = str()
            for i in lbord:
                user = ctx.guild.get_member(i)
                if user.nick:
                    name = user.nick
                else:
                    name = user.name
                out += '' + name + ' - ' + str(lbord[i]) + '\n'
            await ctx.send(out)
        except Exception as e:
            print(traceback.format_exception(None,  # <- type(e) by docs, but ignored
                                            e, e.__traceback__),
                file=sys.stderr, flush=True)

    async def process_scoreboard(self, ctx):
        """Add points to person's score"""
        score = int(ctx.command)

        if score not in [-3,-2,-1,1,2,3]:
            await ctx.send("That's too many JermaBucks dude! Do you think I'm made of cash? Only do 1-3.")
            return  # send failure message

        name = ' '.join(ctx.message.content.split(' ')[1:])
        user = None

        async for u in ctx.guild.fetch_members():
            if name.lower() in [u.name.lower(), u.display_name.lower()]:
                user = u

        if not name or not user:
            return

        g = self.bot.get_guildinfo(ctx.guild.id)
        g.add_point(user.id, amount=score)

        if user.nick:
            name = user.nick
        else:
            name = user.name
        await ctx.send(name + ' now has a whopping ' + str(g.leaderboard[user.id]) + ' points. Wow!')