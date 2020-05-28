import discord
from discord.ext import commands
import os
import glob
import random
import asyncio


def setup(bot):
    bot.add_cog(Control(bot))


class Control(commands.Cog):
    """Cog for controlling the movement of a bot through voice channels."""

    def __init__(self, bot):
        self.bot = bot

    # async def connect_to_channel(self, channel, vc=None):
    #     if not channel:
    #         raise AttributeError('channel cannot be None.')
    #     if not vc:
    #         print('Not vc')
    #         vc = await channel.connect(reconnect=True)
    #     elif vc.channel.id != channel.id:
    #         print(f'Moving from {vc.channel.id} to {channel.id}')
    #         await vc.move_to(channel)
    #         await asyncio.sleep(.1)
    #         print(f'Moved!')
    #     return vc

    async def connect_to_channel(self, vc, dest: discord.abc.GuildChannel):
        if not dest.permissions_for(dest.guild.me).connect:
            print('I don\'t have permission to join that channel.')
            return None

        if vc:
            if vc.channel != dest:
                print('Disconnecting and reconnecting from guild')
                await vc.disconnect()
                await asyncio.sleep(0.1)
                print('reconnecting...')
                vc = await dest.connect(reconnect=True)
                await asyncio.sleep(0.1)
            else:
                print('Already in channel')
                pass  # already there
        else:
            print('Joining', dest)
            vc = await dest.connect(reconnect=True)
        await asyncio.sleep(0.2)
        return vc

    async def connect_to_user(self, ctx):
        try:
            vc = ctx.voice_client
            user_channel = ctx.author.voice.channel
            return await self.connect_to_channel(vc, user_channel)
        except Exception as e:
            print(e)
            raise self.bot.JermaException(
                    'User was not in a voice channel or something.',
                    msg='Hey gamer, you\'re not in a voice channel. Totally uncool.'
                    )

    def get_existing_voice_client(self, guild):
        for vc in self.bot.voice_clients:
            if vc.guild == guild:
                return vc

    @commands.command()
    async def join(self, ctx):
        """Join the user's voice channel."""
        _ = await self.connect_to_user(ctx)

    @commands.command()
    async def leave(self, ctx):
        """Leave the voice channel, if any."""
        if ctx.voice_client and ctx.voice_client.is_connected():
            loc = os.path.join('resources', 'soundclips', 'leave')
            sounds = glob.glob(os.path.join(self.bot.path, loc, '*'))
            soundname = random.choice(sounds)
            sound = os.path.join(loc, soundname)
            self.bot.get_cog('SoundPlayer').play_sound_file(sound, ctx.voice_client)
            await asyncio.sleep(1)
            await ctx.guild.voice_client.disconnect()
