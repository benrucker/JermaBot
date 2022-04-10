import os
from discord import app_commands
import discord

class GuildSoundsError(BaseException):
    def __init__(self, error, msg):
        self.error = error
        self.msg = msg


class SGuildSounds(app_commands.Group):
    """Maintains guild-specific sound functionality."""

    def __init__(self, bot, *args, **kwargs):
        self.bot = bot
        super().__init__(*args, **kwargs)

    @app_commands.command()
    @app_commands.describe(sound='The sound to play.')
    # @app_commands.autocomplete(sound=sound_autocomplete)
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

    @app_commands.command()
    @app_commands.describe(say='The phrase to say.')
    # @app_commands.autocomplete(sound=sound_autocomplete)
    async def say(self, interaction: discord.Interaction, say: str):
        """Play a sound."""
        await interaction.response.send_message(say)

    def get_sound(self, sound, guild: discord.Guild):
        print('getting sound')
        ginfo = self.bot.get_guildinfo(guild.id)
        print('got ginfo')
        print('ginfo.sounds:', ginfo.sounds)
        try: 
            sound_filename = ginfo.sounds[sound.lower()]
            return os.path.join(ginfo.sound_folder, sound_filename)
        except KeyError:
            return None