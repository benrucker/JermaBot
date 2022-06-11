import os
from discord import app_commands, Interaction
from discord.ext import commands
import discord

from jermabot import JermaBot
from guild_info import GuildInfo

async def setup(bot):
    await bot.add_cog(SGuildSounds(bot))


class SGuildSounds(commands.Cog):
    """Maintains guild-specific sound functionality."""

    def __init__(self, bot, *args, **kwargs):
        self.bot: JermaBot = bot
        super().__init__(*args, **kwargs)

    @app_commands.command()
    @app_commands.describe(sound='The sound to play.')
    async def play(self, intr: Interaction, sound: str):
        """Play a sound."""
        if not intr.user.voice:
            await intr.response.send_message('Hey gamer, you\'re not in a voice channel. Totally uncool.', ephemeral=True)
            return
        
        sound = sound.lower()
        current_sound = self.get_sound(sound, intr.guild)
        if not current_sound:
            await intr.response.send_message('Hey gamer, that sound doesn\'t exist.', ephemeral=True)
            return
        
        control = self.bot.get_cog('Control')
        vc = await control.connect_to_user(intr.user.voice, intr.guild)
        self.bot.get_cog('SoundPlayer').play_sound_file(current_sound, vc)
        await intr.response.send_message(f'Playing **{sound}*')

    @play.autocomplete('sound')
    async def sound_autocomplete(
        self,
        intr: Interaction,
        typed_in: str
    ) -> list[app_commands.Choice[str]]:
        typed_in = typed_in.lower()
        sounds = self.bot.get_guildinfo(intr.guild.id).sounds
        direct_matches = {
            s for s in sounds if s.startswith(typed_in)
        }
        close_matches = {
            s for s in sounds if self.letters_appear_in_order(typed_in, s)
        }.difference(direct_matches)
        matches = (list(sorted(direct_matches)) + list(sorted(close_matches)))[:25]
        return [
            app_commands.Choice(name=s, value=s)
            for s in matches
        ]

    def letters_appear_in_order(self, part: str, full: str):
        part: list = list(part)
        while full and part:
            index = full.find(part.pop(0))
            if index == -1:
                return False
            full = full[index + 1:]
        return not part

    def get_sound(self, sound, guild: discord.Guild):
        ginfo: GuildInfo = self.bot.get_guildinfo(guild.id)
        sounds = ginfo.sounds
        sound_folder = ginfo.sound_folder
        try:
            sound_filename = sounds[sound.lower()]
            return os.path.join(sound_folder, sound_filename)
        except KeyError:
            return None

    @app_commands.command()
    @app_commands.describe(say='The phrase to say.')
    async def say(self, intr: Interaction, say: str):
        """Play a sound."""
        await intr.response.send_message(say)
