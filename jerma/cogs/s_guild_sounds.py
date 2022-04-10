import os
from discord import app_commands
from discord.ext import commands
import discord


class GuildSoundsError(BaseException):
    def __init__(self, error, msg):
        self.error = error
        self.msg = msg


async def setup(bot):
    await bot.add_cog(SGuildSounds(bot), guild=discord.Object(id=571004411137097731))


class SGuildSounds(commands.Cog):
    """Maintains guild-specific sound functionality."""

    def __init__(self, bot, *args, **kwargs):
        self.bot = bot
        super().__init__(*args, **kwargs)

    @ app_commands.command()
    @ app_commands.describe(sound='The sound to play.')
    async def play(self, interaction: discord.Interaction, sound: str):
        """Play a sound."""
        sound = sound
        current_sound = self.get_sound(sound, interaction.guild)
        if not current_sound:
            raise GuildSoundsError('Sound ' + sound + ' not found.',
                                   'Hey gamer, that sound doesn\'t exist.')

        control = self.bot.get_cog('Control')
        print('connecting to user...')
        vc = await control.connect_to_user(interaction.user.voice)
        print('should be connected')
        self.bot.get_cog('SoundPlayer').play_sound_file(current_sound, vc)
        print('dispatched sound_file_play')

    @ play.autocomplete('sound')
    async def sound_autocomplete(
        self,
        interaction: discord.Interaction,
        typed_in: str
    ) -> list[app_commands.Choice[str]]:
        typed_in = typed_in.lower()
        sounds = self.bot.get_guildinfo(interaction.guild.id).sounds
        direct_matches = [
            app_commands.Choice(name=s, value=s)
            for s in sounds if s.startswith(typed_in)
        ]
        return direct_matches

    def get_sound(self, sound, guild: discord.Guild):
        ginfo = self.bot.get_guildinfo(guild.id)
        sounds = ginfo.sounds
        sound_folder = ginfo.sound_folder
        try:
            sound_filename = sounds[sound.lower()]
            return os.path.join(sound_folder, sound_filename)
        except KeyError:
            return None

    @app_commands.command()
    @app_commands.describe(say='The phrase to say.')
    # @app_commands.autocomplete(sound=sound_autocomplete)
    async def say(self, interaction: discord.Interaction, say: str):
        """Play a sound."""
        await interaction.response.send_message(say)
