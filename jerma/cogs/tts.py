from discord.ext import commands
import discord
import re
import os
from pydub.audio_segment import AudioSegment
from .utils import ttsengine, textconverter
from typing import Optional


def setup(bot):
    bot.add_cog(TTS(bot, bot.tts_engine, bot.jtts_engine))


class TTS(commands.Cog):
    """Cog for text-to-speech functionality."""

    def __init__(self, bot, tts_engine, jtts_engine):
        self.bot = bot
        self.tts: ttsengine.TTSEngineInterface = tts_engine
        self.jtts: ttsengine.TTSEngineInterface = jtts_engine

    def text_to_wav(self, text, speed='normal', engine=None):
        if not engine: engine = self.tts
        if speed == 'slow':
            return engine.text_to_wav_slow(text)
        if speed == 'fast':
            return engine.text_to_wav_fast(text)
        return engine.text_to_wav_normal(text)

    def play_text(self, vc, to_speak, speed='normal', engine=None):
        if not engine: engine = self.tts
        sound_file = self.text_to_wav(to_speak, speed=speed, engine=engine)
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
        while sound[trim_ms:trim_ms + chunk_size].dBFS < silence_threshold and trim_ms < len(sound):
            trim_ms += chunk_size

        return sound[trim_ms:]

    def generate_id_path(self, label, ctx):
        return os.path.join(self.bot.path, 'resources', 'soundclips', 'temp', label + str(ctx.guild.id) + '.wav')

    def birthday_wave(self, name, ctx):
        """Construct birthday sound."""
        song = AudioSegment.from_file(os.path.join('resources', 'soundclips', 'blank_birthday.wav'))
        name = AudioSegment.from_file(name)
        name = self.remove_leading_silence(name)
        insert_times = [7.95 * 1000, 12.1 * 1000]

        for insert_time in insert_times:
            song = song.overlay(name, position=insert_time)

        outpath = self.generate_id_path('birthday_name', ctx)
        song.export(outpath)
        return outpath

    def strip_quotes(self, text: str):
        print('removing quotes in:', text)
        to_remove = re.compile(r'[\'"]')
        print('new:', to_remove.sub('', text))
        return to_remove.sub('', text)

    @commands.command()
    async def birthday(self, ctx, *args):
        """Wish someone a happy birthday!"""
        if not args:
            raise discord.InvalidArgument()
        to_speak = self.strip_quotes(' '.join(args))  # ingest args
        name = self.text_to_wav(to_speak)
        birthday_with_name = self.birthday_wave(name, ctx)
        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        player = self.bot.get_cog('SoundPlayer')
        player.play_sound_file(birthday_with_name, vc)

    @commands.command()
    async def speakfile(self, ctx, *args):
        """Send the input text as a sound file from text-to-speech."""
        if not args:
            raise discord.InvalidArgument()
        to_speak = self.strip_quotes(' '.join(args))  # ingest args
        await ctx.send(file=discord.File(self.text_to_wav(to_speak)))

    @commands.command()
    async def adderall(self, ctx, *args):
        """Text-to-speech but f a s t."""
        if not args:
            raise discord.InvalidArgument()
        to_speak = self.strip_quotes(' '.join(args))  # ingest args
        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        self.play_text(vc, to_speak, speed='fast')

    @commands.command()
    async def speak(self, ctx, *args):
        """Play your input text through text-to-speech."""
        if not args:
            raise discord.InvalidArgument()
        to_speak = self.strip_quotes(' '.join(args))  # ingest args
        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        self.play_text(vc, to_speak)

    @commands.command()
    async def speakdrunk(self, ctx, *args):
        """Text-to-speech but more drunk."""
        if not args:
            raise discord.InvalidArgument()
        to_speak = self.strip_quotes(''.join(args))  # ingest args
        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        self.play_text(vc, to_speak, speed='slow')

    @commands.command(aliases=['sa'])
    async def speakanime(self, ctx, *args):
        if not args:
            raise discord.InvalidArgument()
        words_in = self.strip_quotes(' '.join(args))  # ingest args
        words_and_punct = textconverter.split_to_words_and_punctuation(words_in)
        kana_to_speak = ' '.join(textconverter.mixed_lang_to_katakana(words_and_punct))
        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        self.play_text(vc, kana_to_speak, engine=self.jtts)

    # TODO hide if jtts is none
    # TODO make this respect per-guild preferences
    @commands.command()
    async def inflection(self, ctx, voice: Optional[str]):
        """Change the inflection of Japanese speech."""
        voices = ['angry', 'bashful', 'happy', 'normal', 'sad']
        if not voice or voice not in voices:
            await ctx.send('Voice options: ' + ', '.join(voices) + '.')
            return
        path, _ = os.path.split(self.jtts.voice)
        filename = 'mei_' + voice + '.htsvoice'
        newpath = os.path.join(path, filename)
        print('setting open_jtalk voice to:', filename)
        self.jtts.voice = newpath
        await ctx.send(f'Mr. Stark, I\'m feeling {voice}.')
