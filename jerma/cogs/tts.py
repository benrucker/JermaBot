import os
import re
from typing import Optional

import discord
from discord import app_commands
from cogs.control import Control
from discord.ext import commands
from discord.ext.commands import Context
from pydub.audio_segment import AudioSegment

from jermabot import JermaBot

from .utils import textconverter
from .utils.ttsengine import TTSEngine


async def setup(bot: JermaBot):
    await bot.add_cog(TTS(bot, bot.tts_engine, bot.jtts_engine))


class TTSNotEnabled(commands.CheckFailure):
    def __str__(self):
        return 'Hey, uh, gamer? TTS isn\'t enabled on this instance of JermaBot.'


class JTTSNotEnabled(commands.CheckFailure):
    def __str__(self):
        return 'Hey man, I don\'t know how to say this but... Japanese TTS isn\'t enabled on this instance of JermaBot.'


def tts_enabled():
    def pred(ctx: Context):
        if not ctx.cog.tts:
            raise TTSNotEnabled()
        return True
    return commands.check(pred)


def jtts_enabled():
    def pred(ctx: Context):
        if not ctx.cog.jtts:
            raise JTTSNotEnabled()
        return True
    return commands.check(pred)


class TTS(commands.Cog):
    """Cog for text-to-speech functionality."""

    def __init__(self, bot: JermaBot, tts_engine: TTSEngine, jtts_engine: TTSEngine):
        self.bot: JermaBot = bot
        self.tts = tts_engine
        self.jtts = jtts_engine

    async def cog_command_error(self, ctx: Context, error):
        print('caught command error in tts')
        if isinstance(error, (TTSNotEnabled, JTTSNotEnabled)):
            print('its a boy!')
            await ctx.send(error)

    @commands.hybrid_command()
    @app_commands.describe(text="The sentence(s) for JermaBot to speak")
    @tts_enabled()
    async def speak(self, ctx: Context, *, text: str):
        """Play your input text through text-to-speech."""
        if not text:
            raise discord.InvalidArgument()
        vc = await self.connect(ctx)
        to_speak = self.convert_text_to_tts_text(text)
        sound_file = self.make_tts_sound_file(to_speak)
        self.play_sound_file(sound_file, vc)

    @commands.hybrid_command()
    @app_commands.describe(text="The sentence(s) for JermaBot to speak")
    @tts_enabled()
    async def adderall(self, ctx: Context, *, text: str):
        """Text-to-speech but f a s t."""
        if not text:
            raise discord.InvalidArgument()
        vc = await self.connect(ctx)
        to_speak = self.convert_text_to_tts_text(text)
        sound_file = self.make_tts_sound_file(to_speak, speed='fast')
        self.play_sound_file(sound_file, vc)

    @commands.hybrid_command()
    @app_commands.describe(text="The sentence(s) for JermaBot to speak")
    @tts_enabled()
    async def speakdrunk(self, ctx: Context, *, text: str):
        """Text-to-speech but more drunk."""
        if not text:
            raise discord.InvalidArgument()
        vc = await self.connect(ctx)
        to_speak = self.convert_text_to_tts_text(text)
        to_speak = to_speak.strip(' ')  # remove spaces to drunkify
        sound_file = self.make_tts_sound_file(to_speak, speed='slow')
        self.play_sound_file(sound_file, vc)

    @commands.hybrid_command()
    @app_commands.describe(text="The sentence(s) for JermaBot to speak")
    @tts_enabled()
    async def speakfile(self, ctx: Context, *, text: str):
        """Send the input text as a sound file from text-to-speech."""
        if not text:
            raise discord.InvalidArgument()
        to_speak = self.convert_text_to_tts_text(text)
        await ctx.send(file=discord.File(self.text_to_wav(to_speak)))

    @commands.hybrid_command()
    @app_commands.describe(name="The person to wish happy birthday to!")
    @tts_enabled()
    async def birthday(self, ctx: Context, *, name: str):
        """Wish someone a happy birthday!"""
        if not name:
            raise discord.InvalidArgument()
        vc = await self.connect(ctx)
        to_speak = self.convert_text_to_tts_text(name)
        name_sound = self.text_to_wav(to_speak)
        birthday_with_name = self.birthday_sound(name_sound, ctx)
        self.play_sound_file(birthday_with_name, vc)

    @commands.command(aliases=['sa'])
    @app_commands.describe(text="The sentence(s) for JermaBot to speak")
    @jtts_enabled()
    async def speakanime(self, ctx: Context, *, text: str):
        """Text-to-speech but more Japanese."""
        if not text:
            raise discord.InvalidArgument()
        vc = await self.connect(ctx)
        kana_to_speak = self.join_text_to_jtts_text(text)
        sound_file = self.make_tts_sound_file(kana_to_speak, engine=self.jtts)
        self.play_sound_file(sound_file, vc)

    # TODO make this respect per-guild preferences
    @commands.hybrid_command()
    @app_commands.describe(voice="The inflection for JermaBot to have when speaking Japanese")
    @jtts_enabled()
    async def inflection(self, ctx: Context, voice: Optional[str]):
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

    async def connect(self, ctx: Context):
        control: Control = self.bot.get_cog('Control')
        return await control.connect_to_user(ctx.author.voice, ctx.guild)

    def convert_text_to_tts_text(self, text: str):
        return self.strip_quotes(text)

    def join_text_to_jtts_text(self, text: str):
        text_stripped = self.strip_quotes(text)
        text_and_punct = textconverter.split_to_words_and_punctuation(
            text_stripped
        )
        return ' '.join(textconverter.mixed_lang_to_katakana(text_and_punct))

    def strip_quotes(self, text: str):
        return text.replace('"', '').replace("'", '')

    def make_tts_sound_file(self, to_speak: str, speed='normal', engine: TTSEngine = None):
        if not engine:
            engine = self.tts
        filepath = self.text_to_wav(to_speak, speed=speed, engine=engine)
        return filepath

    def play_sound_file(self, sound_file, vc):
        self.bot.get_cog('SoundPlayer').play_sound_file(sound_file, vc)

    def text_to_wav(self, text: str, speed='normal', engine=None):
        if not engine:
            engine = self.tts
        if speed == 'slow':
            return engine.text_to_wav_slow(text)
        if speed == 'fast':
            return engine.text_to_wav_fast(text)
        return engine.text_to_wav_normal(text)

    def birthday_sound(self, name_sound, ctx: Context):
        """Construct birthday sound."""
        song: AudioSegment = AudioSegment.from_file(os.path.join(
            'resources', 'soundclips', 'blank_birthday.wav'
        ))
        name_sound = AudioSegment.from_file(name_sound)
        name_sound = self.remove_leading_silence(name_sound)

        insert_times = [7.95 * 1000, 12.1 * 1000]
        for insert_time in insert_times:
            song = song.overlay(name_sound, position=insert_time)

        # TODO factor out an id path generator in utils
        # or maybe be directed to one on construction
        outpath = self.generate_id_path('birthday_name', ctx)
        song.export(outpath)
        return outpath

    def remove_leading_silence(self, sound, silence_threshold_db=-50.0, chunk_size_ms=10):
        # Taken from https://stackoverflow.com/questions/29547218/remove-silence-at-the-beginning-and-at-the-end-of-wave-files-with-pydub
        trim_ms = 0

        assert chunk_size_ms > 0  # to avoid infinite loop
        while sound[trim_ms:trim_ms + chunk_size_ms].dBFS < silence_threshold_db and trim_ms < len(sound):
            trim_ms += chunk_size_ms

        return sound[trim_ms:]

    def generate_id_path(self, label, ctx: Context):
        return os.path.join(self.bot.path, 'resources', 'soundclips', 'temp', label + str(ctx.guild.id) + '.wav')
