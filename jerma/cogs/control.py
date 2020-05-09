from discord.ext import commands
import os
import glob
import random
import asyncio


class Control(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def connect_to_channel(self, channel, vc=None):
        if not channel:
            raise AttributeError('channel cannot be None.')
        if not vc:
            vc = await channel.connect(reconnect=False)
        if vc.channel is not channel:
            await vc.move_to(channel)
        return vc

    async def connect_to_user(self, ctx):
        try:
            vc = ctx.voice_client
            user_channel = ctx.author.voice.channel
            return await self.connect_to_channel(user_channel, vc)
        except Exception as e:
            print(e)
            raise self.bot.JermaException('User was not in a voice channel or something.',
                                msg='Hey gamer, you\'re not in a voice channel. Totally uncool.')

    @commands.command()
    async def join(self, ctx):
        """Join the user's voice channel."""
        _ = await self.connect_to_user(ctx)

    @commands.command()
    async def leave(self, ctx):
        """Leave the voice channel, if any."""
        if ctx.voice_client and ctx.voice_client.is_connected():
            loc = os.path.join('resources', 'soundclips', 'leave')
            sounds = glob.glob(os.path.join(self.bot.path, loc))
            soundname = random.choice(sounds)
            sound = os.path.join(loc, soundname)
            self.bot.get_cog('SoundPlayer').play_sound_file(sound, ctx.voice_client)
            asyncio.sleep(1)
            await ctx.guild.voice_client.disconnect()
