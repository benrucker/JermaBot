from itertools import cycle
import discord
from discord.enums import ActivityType
from discord.ext import commands, tasks
from random import shuffle

PRESENCES = [
    (ActivityType.listening, 'my heart.'),
    (ActivityType.listening, 'rats lofi'),
    (ActivityType.listening, 'Shigurain.'),
    (ActivityType.listening, 'a Scream Compilation.'),
    (ActivityType.listening, 'the DOOM OST.'),
    (ActivityType.listening, 'a clown podcast.'),
    (ActivityType.streaming, 'DARK SOULS III'),
    (ActivityType.streaming, 'Just Cause 4 for 3DS'),
    (ActivityType.watching, 'chat make fun of me.'),
    (ActivityType.watching, 'E3® 2022.'),
    (ActivityType.watching, 'GrillMasterxBBQ\'s vids.'),
    (ActivityType.watching, 'your form, bro!'),
    (ActivityType.watching, 'the byeahs.'),
]
shuffle(PRESENCES)

async def setup(bot):
    await bot.add_cog(Presence(bot))


class Presence(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.presences = cycle(PRESENCES)
        self.task_change_presence.start()

    def cog_unload(self):
        self.task_change_presence.cancel()

    @tasks.loop(minutes=60)
    async def task_change_presence(self):
        if not self.bot.is_closed():
            curr = next(self.presences)
            await self.bot.change_presence(activity=discord.Activity(
                type=curr[0], name=curr[1]
            ))

    @task_change_presence.before_loop
    async def task_before_change_presence(self):
        await self.bot.wait_until_ready()
