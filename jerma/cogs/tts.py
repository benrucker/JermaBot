import os
import re
from typing import Optional

import discord
from discord.ext import commands
from pydub.audio_segment import AudioSegment

from cogs.control import Control
from .utils import textconverter, ttsengine


async def setup(bot):
    await bot.add_cog(TTS(bot, bot.tts_engine, bot.jtts_engine))


class TTSNotEnabled(commands.CheckFailure):
    def __str__(self):
        return 'Hey, uh, gamer? TTS isn\'t enabled on this instance of JermaBot.'


class JTTSNotEnabled(commands.CheckFailure):
    def __str__(self):
        return 'Hey man, I don\'t know how to say this but... Japanese TTS isn\'t enabled on this instance of JermaBot.'


def tts_enabled():
    def pred(ctx):
        if not ctx.cog.tts:
            raise TTSNotEnabled()
        return True
    return commands.check(pred)


def jtts_enabled():
    def pred(ctx):
        if not ctx.cog.jtts:
            raise JTTSNotEnabled()
        return True
    return commands.check(pred)


class TTS(commands.Cog):
    """Cog for text-to-speech functionality."""

    def __init__(self, bot: commands.Bot,
                 tts_engine: ttsengine.TTSEngineInterface,
                 jtts_engine: ttsengine.TTSEngineInterface):
        self.bot = bot
        self.tts = tts_engine
        self.jtts = jtts_engine

    async def cog_command_error(self, ctx, error):
        print('caught command error in tts')
        if isinstance(error, (TTSNotEnabled, JTTSNotEnabled)):
            print('its a boy!')
            await ctx.send(error)

    @commands.command()
    @tts_enabled()
    async def speak(self, ctx, *words):
        """Play your input text through text-to-speech."""
        if not words:
            raise discord.InvalidArgument()
        vc = await self.connect(ctx)
        to_speak = self.join_words_to_tts_text(words)
        sound_file = self.make_tts_sound_file(to_speak)
        self.play_sound_file(sound_file, vc)

    @commands.command()
    @tts_enabled()
    async def adderall(self, ctx, *words):
        """Text-to-speech but f a s t."""
        if not words:
            raise discord.InvalidArgument()
        vc = await self.connect(ctx)
        to_speak = self.join_words_to_tts_text(words)
        sound_file = self.make_tts_sound_file(to_speak, speed='fast')
        self.play_sound_file(sound_file, vc)

    @commands.command()
    @tts_enabled()
    async def speakdrunk(self, ctx, *words):
        """Text-to-speech but more drunk."""
        if not words:
            raise discord.InvalidArgument()
        vc = await self.connect(ctx)
        to_speak = self.join_words_to_tts_text(words)
        to_speak = to_speak.strip(' ')  # remove spaces to drunkify
        sound_file = self.make_tts_sound_file(to_speak, speed='slow')
        self.play_sound_file(sound_file, vc)

    @commands.command()
    @tts_enabled()
    async def speakfile(self, ctx, *words):
        """Send the input text as a sound file from text-to-speech."""
        if not words:
            raise discord.InvalidArgument()
        to_speak = self.join_words_to_tts_text(words)
        await ctx.send(file=discord.File(self.text_to_wav(to_speak)))

    @commands.command()
    @tts_enabled()
    async def birthday(self, ctx, *name):
        """Wish someone a happy birthday!"""
        if not name:
            raise discord.InvalidArgument()
        vc = await self.connect(ctx)
        to_speak = self.join_words_to_tts_text(name)
        name_sound = self.text_to_wav(to_speak)
        birthday_with_name = self.birthday_sound(name_sound, ctx)
        self.play_sound_file(birthday_with_name, vc)

    @commands.command(aliases=['sa'])
    @jtts_enabled()
    async def speakanime(self, ctx, *words):
        """Text-to-speech but more Japanese."""
        if not words:
            raise discord.InvalidArgument()
        vc = await self.connect(ctx)
        kana_to_speak = self.join_words_to_jtts_text(words)
        sound_file = self.make_tts_sound_file(kana_to_speak, engine=self.jtts)
        self.play_sound_file(sound_file, vc)

    # TODO make this respect per-guild preferences
    @commands.command()
    @jtts_enabled()
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

    async def connect(self, ctx):
        control: Control = self.bot.get_cog('Control')
        return await control.connect_to_user(ctx.author.voice, ctx.guild)

    def join_words_to_tts_text(self, words: list):
        return self.strip_quotes(' '.join(words))

    def join_words_to_jtts_text(self, words: list):
        words_in = self.strip_quotes(' '.join(words))
        words_and_punct = textconverter.split_to_words_and_punctuation(
            words_in)
        return ' '.join(textconverter.mixed_lang_to_katakana(words_and_punct))

    def strip_quotes(self, text: str):
        to_remove = re.compile(r'[\'"]')
        return to_remove.sub('', text)

    def make_tts_sound_file(self, to_speak, speed='normal', engine=None):
        if not engine:
            engine = self.tts
        filepath = self.text_to_wav(to_speak, speed=speed, engine=engine)
        return filepath

    def play_sound_file(self, sound_file, vc):
        self.bot.get_cog('SoundPlayer').play_sound_file(sound_file, vc)

    def text_to_wav(self, text, speed='normal', engine=None):
        if not engine:
            engine = self.tts
        if speed == 'slow':
            return engine.text_to_wav_slow(text)
        if speed == 'fast':
            return engine.text_to_wav_fast(text)
        return engine.text_to_wav_normal(text)

    def birthday_sound(self, name_sound, ctx):
        """Construct birthday sound."""
        song = AudioSegment.from_file(os.path.join(
            'resources', 'soundclips', 'blank_birthday.wav'))
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

    def generate_id_path(self, label, ctx):
        return os.path.join(self.bot.path, 'resources', 'soundclips', 'temp', label + str(ctx.guild.id) + '.wav')
