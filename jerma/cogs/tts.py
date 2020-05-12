from discord.ext import commands
import discord
from pydub.audio_segment import AudioSegment
import os


def setup(bot):
    bot.add_cog(TTS(bot, bot.tts_engine))


class TTS(commands.Cog):
    """Cog for text-to-speech functionality."""

    def __init__(self, bot, tts_engine):
        self.bot = bot
        self.tts = tts_engine

    def text_to_wav(self, text, speed='normal'):
        if speed == 'slow':
            return self.tts.text_to_wav_slow(text)
        if speed == 'fast':
            return self.tts.text_to_wav_fast(text)
        return self.tts.text_to_wav_normal(text)

    def play_text(self, vc, to_speak, speed='normal'):
        sound_file = self.text_to_wav(to_speak, speed=speed)
        self.bot.get_cog('SoundPlayer').play_sound_file(sound_file, vc)

    def remove_leading_silence(self, sound, silence_threshold=-50.0, chunk_size=10):
        """
        Return an AudioSegment that starts at the end of leading silence.

        sound is a pydub.AudioSegment
        silence_threshold in dB
        chunk_size in ms

        Taken from https://stackoverflow.com/questions/29547218/remove-silence-at-the-beginning-and-at-the-end-of-wave-files-with-pydub
        """
        trim_ms = 0  # ms

        assert chunk_size > 0  # to avoid infinite loop
        while sound[trim_ms:trim_ms+chunk_size].dBFS < silence_threshold and trim_ms < len(sound):
            trim_ms += chunk_size

        return sound[trim_ms:]

    def generate_id_path(self, label, ctx):
        return os.path.join(self.bot.path, 'resources', 'soundclips', 'temp', label + str(ctx.guild.id) + '.wav')

    def birthday_wave(self, name, ctx):
        """Construct birthday sound."""
        song = AudioSegment.from_wav(os.path.join('resources', 'soundclips', 'blank_birthday.wav'))
        name = AudioSegment.from_wav(name)
        name = self.remove_leading_silence(name)
        insert_times = [7.95 * 1000, 12.1 * 1000]

        for insert_time in insert_times:
            song = song.overlay(name, position=insert_time)

        outpath = self.generate_id_path('birthday_name', ctx)
        song.export(outpath)
        return outpath

    @commands.command()
    async def birthday(self, ctx, *args):
        """Wish someone a happy birthday!"""
        if not args:
            raise discord.InvalidArgument
        to_speak = ' '.join(args)
        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        vc.play(discord.FFmpegPCMAudio(
                    self.birthday_wave(
                        self.text_to_wav(to_speak), ctx)))

    @commands.command()
    async def speakfile(self, ctx, *args):
        """Send the input text as a sound file from text-to-speech."""
        if not args:
            raise discord.InvalidArgument
        to_speak = ' '.join(args)
        await ctx.send(file=discord.File(self.text_to_wav(to_speak)))

    @commands.command()
    async def adderall(self, ctx, *args):
        """Text-to-speech but f a s t."""
        if not args:
            raise discord.InvalidArgument
        to_speak = ' '.join(args)
        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        self.play_text(vc, to_speak, speed='fast')

    @commands.command()
    async def speak(self, ctx, *args):
        """Play your input text through text-to-speech."""
        if not args:
            raise discord.InvalidArgument
        to_speak = ' '.join(args)
        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        self.play_text(vc, to_speak)

    @commands.command()
    async def speakdrunk(self, ctx, *args):
        """Text-to-speech but more drunk."""
        if not args:
            raise discord.InvalidArgument
        to_speak = ''.join(args)
        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        self.play_text(vc, to_speak, speed='slow')
