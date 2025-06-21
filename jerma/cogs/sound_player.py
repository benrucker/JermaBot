import os
import time

import discord
from colorama import Fore as t
from discord import VoiceProtocol, app_commands, VoiceClient
from discord.ext import commands
from discord.ext.commands import Context

from jermabot import JermaBot


async def setup(bot: JermaBot):
    await bot.add_cog(SoundPlayer(bot))


class SoundPlayer(commands.Cog):
    """Cog for playing sounds through voice channels."""

    def __init__(self, bot: JermaBot):
        self.bot: JermaBot = bot

    def play_sound_file(self, sound_filepath: str, vc: VoiceProtocol | VoiceClient):
        if not isinstance(vc, VoiceClient):
            print(f'{t.RED}Custom voice protocols are not currently supported.')
            return

        source = self.source_factory(sound_filepath)
        source.volume = self.bot.get_guildinfo(vc.channel.guild.id).volume
        self.stop_audio(vc)
        vc.play(source)
        print(f'[{time.ctime()}] Playing {os.path.split(sound_filepath)[1]} | at volume: {source.volume} | in: {t.CYAN}{vc.guild} #{vc.channel}')

    @commands.hybrid_command()
    @app_commands.default_permissions(use_application_commands=True)
    async def stop(self, ctx: Context):
        """Stop any currently playing audio."""
        vc = ctx.voice_client
        if not isinstance(vc, VoiceClient):
            print(f'{t.RED}Custom voice protocols are not currently supported.')
            return

        self.stop_audio(vc)
        if ctx.interaction:
            await ctx.send("The sound has been stopped in its tracks 🤠", ephemeral=True)

    def stop_audio(self, vc: VoiceProtocol | VoiceClient):
        if not isinstance(vc, VoiceClient):
            print(f'{t.RED}Custom voice protocols are not currently supported.')
            return

        if vc.is_playing():
            vc.stop()
            silence = os.path.join('resources', 'soundclips', 'silence.wav')
            self.play_sound_file(silence, vc)
            while vc.is_playing():
                continue

    @commands.hybrid_command()
    @app_commands.default_permissions(use_application_commands=True)
    async def volume(self, ctx: Context, volume: int):
        """Change the volume of played sounds."""
        if ctx.guild is None:
            print(f'{t.RED}Volume command was called in a non-guild context')
            raise RuntimeError("You can't use this command outside of a guild.")

        ginfo = self.bot.get_guildinfo(ctx.guild.id)
        old_vol = ginfo.volume

        if not volume:
            await ctx.send(f'Volume is currently at {int(old_vol*100)}, bro.')
            return
        
        fvol = volume / 100
        ginfo.volume = fvol
        if ctx.voice_client and isinstance(ctx.voice_client, VoiceClient) and isinstance(ctx.voice_client.source, discord.PCMVolumeTransformer):
            ctx.voice_client.source.volume = fvol

        speakers = ['🔈', '🔉', '🔊']
        speaker = '🔇' if volume == 0 else speakers[min(int(fvol * len(speakers)), 2)]
        arrow = '⬆' if fvol > old_vol else '⬇'
        if ctx.interaction:
            await ctx.send(speaker + arrow)
        else:
            react = ctx.message.add_reaction
            await react(speaker)
            await react(arrow)


    @commands.Cog.listener()
    async def on_guild_update(self, before, after):
        """Disconnect from voice channel if guild changes voice region."""
        print(f'Guild {before.name} updated.')
        if before.voice_client and not before.region == after.region:
            print(f'Disconnecting from {before.name} due to region switch.')
            await before.voice_client.disconnect()

    def source_factory(self, filename):
        op = '-guess_layout_max 0'
        return discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(filename, before_options=op))

    class LoopingSource(discord.AudioSource):
        """This class acts the same as a discord.py AudioSource except it will loop forever."""

        def __init__(self, param, source_factory, guild_id, bot):
            self.factory = source_factory
            self.param = param
            self.source = source_factory(self.param)
            self.guild_id = guild_id
            self.source.volume = bot.get_guildinfo(guild_id).volume
            self.bot: JermaBot = bot

        def read(self):
            self.source.volume = self.bot.get_guildinfo(self.guild_id).volume
            ret = self.source.read()
            if not ret:
                self.source.cleanup()
                self.source = self.factory(self.param)
                self.source.volume = self.bot.get_guildinfo(
                    self.guild_id
                ).volume
                ret = self.source.read()
            return ret
