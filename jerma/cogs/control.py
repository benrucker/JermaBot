import asyncio
import glob
import os
import random

from colorama import Fore as t
from discord import Guild, VoiceChannel, VoiceClient, VoiceState
from discord.ext import commands
from discord.ext.commands import Context

from jermabot import JermaBot


async def setup(bot):
    await bot.add_cog(Control(bot))


RECONNECT = False


class JoinFailedError(commands.CommandError):
    def __str__(self):
        return 'Hey gamer, you\'re not in a voice channel. Totally uncool.'


class Control(commands.Cog):
    """Cog for controlling the movement of a bot through voice channels."""

    def __init__(self, bot: JermaBot):
        self.bot: JermaBot = bot

    async def cog_command_error(self, ctx, error):
        if isinstance(error, JoinFailedError):
            await ctx.send(error)

    @commands.command()
    async def join(self, ctx: Context):
        """Join the user's voice channel."""
        _ = await self.connect_to_user(ctx.author.voice, ctx.guild)

    async def connect_to_user(self, user_voice: VoiceState, guild: Guild):
        if not user_voice or not user_voice.channel:
            print('user\'s voice attr is false')
            raise JoinFailedError()
        else:
            print(user_voice)

        try:
            vc = guild.voice_client
            user_channel = user_voice.channel
            return await self.connect_to_channel(vc, user_channel)
        except Exception as e:
            print('connection error: ' + e)
            raise JoinFailedError()

    async def connect_to_channel(self, vc: VoiceClient | None, dest: VoiceChannel):
        if not dest.permissions_for(dest.guild.me).connect:
            print('I don\'t have permission to join that channel.')
            return None

        if vc and vc.is_connected():
            if vc.channel != dest:
                print('Disconnecting and reconnecting from guild')
                await vc.disconnect()
                await asyncio.sleep(0.1)
                print('reconnecting...')
                vc = await dest.connect(reconnect=RECONNECT)
            else:
                print('Already in channel')
                return vc
        elif vc and not vc.is_connected():
            print('Had voice client but was not connected to voice')
            print('reconnecting...')
            await vc.disconnect(force=True)
            await asyncio.sleep(0.2)
            vc = await dest.connect(reconnect=RECONNECT)
        else:
            print('Joining', dest)
            vc = await dest.connect(reconnect=RECONNECT)
        await asyncio.sleep(0.2)
        return vc

    def get_existing_voice_client(self, guild: Guild):
        for vc in self.bot.voice_clients:
            if not isinstance(vc, VoiceClient):
                continue
            if vc.guild == guild:
                return vc

    @commands.command()
    async def leave(self, ctx: Context):
        """Leave the voice channel, if any."""
        if ctx.voice_client and ctx.voice_client.is_connected():
            await self.play_leave_sound(ctx)
            await ctx.guild.voice_client.disconnect()
        else:
            print(f'{t.RED}Leave conditional failed')
            print(f'ctx.voice_client =', ctx.voice_client)
            if ctx.voice_client and isinstance(ctx.voice_client, VoiceClient):
                print(f'is_connected =', ctx.voice_client.is_connected())

    async def play_leave_sound(self, ctx: Context):
        loc = os.path.join('resources', 'soundclips', 'leave')
        sounds = glob.glob(os.path.join(self.bot.path, loc, '*'))
        soundname = random.choice(sounds)
        sound = os.path.join(loc, soundname)
        self.bot.get_cog('SoundPlayer').play_sound_file(
            sound, ctx.voice_client
        )
        await asyncio.sleep(1)
