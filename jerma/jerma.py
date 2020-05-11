import argparse
import asyncio
from collections import OrderedDict
import colorama
from colorama import Fore as t
from colorama import Back, Style
import discord
from discord.ext import commands
from glob import glob
import logging
import os
from pydub import AudioSegment
import random
import sys
import subprocess
import time
import traceback

from cogs.guild_sounds import GuildSounds
from cogs.sound_player import SoundPlayer
from cogs.control import Control
from cogs.tts import TTS
from cogs.admin import Admin
from cogs.fun import Fun
from jerma_exception import JermaException
from guild_info import GuildInfo
import ttsengine


YES = ['yes','yeah','yep','yeppers','of course','ye','y','ya','yah']
NO  = ['no','n','nope','start over','nada', 'nah']

colorama.init(autoreset=True)  # set up colored console out
guilds = dict()
tts = None

prefixes = ['$', '+']


class JermaBot(commands.Bot):
    def __init__(self, path, command_prefix=None):
        self.path = path
        self.guild_infos = dict()
        super().__init__(command_prefix=command_prefix)

    def get_guildinfo(self, id=None):
        if not id:
            return self.guild_infos
        else:
            return self.guild_infos[id]

    def make_guildinfo(self, guild: discord.Guild):
        self.guild_infos[guild.id] = GuildInfo(guild)

    def initialize_guild_infos(self):
        for guild in self.guilds:
            os.makedirs(os.path.join('guilds',f'{guild.id}','sounds'), exist_ok=True)
            self.guild_infos[guild.id] = GuildInfo(guild)

    def JermaException(self, err, msg):
        return JermaException(err, msg)


if __name__ == '__main__':
    global source_path, bot
    source_path = os.path.dirname(os.path.abspath(__file__))  # /a/b/c/d/e
    bot = JermaBot(source_path, command_prefix=commands.when_mentioned_or('$', '+'))

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description='Run JermaBot')
    parser.add_argument('-s', '--secret_filename', help='location of bot token text file')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-mycroft', '--mycroft_path', help='tell jerma to use mycroft at the given path')
    group.add_argument('-voice', '--voice_path', help='tell jerma to use windows voice.exe tts engine')
    group.add_argument('-espeak', help='tell jerma to use espeak tts engine',
                        action='store_true')
    parser.add_argument('-mv','--mycroft_voice', help='name of OR path to the voice to use with mycroft')
    args = parser.parse_args()

    if args.secret_filename:
        file = open(args.secret)
    else:
        file = open('secret.txt')
    secret = file.read()
    file.close()

    if args.mycroft_path:
        if args.mycroft_voice:
            _v = args.mycroft_voice
        else:
            _v = 'ap'
        tts = ttsengine.construct(engine=ttsengine.MYCROFT, path=args.mycroft_path, voice=_v)
    elif args.voice_path:
        tts = ttsengine.construct(engine=ttsengine.VOICE, path=args.voice_path)
    else:
        tts = ttsengine.construct(engine=ttsengine.ESPEAK) 
    bot.tts_engine = tts

    try:
        os.makedirs(os.path.join('resources', 'soundclips', 'temp'))
    except:
        pass

    bot.load_extension('cogs.guild_sounds')
    bot.load_extension('cogs.sound_player')
    bot.load_extension('cogs.control')
    bot.load_extension('cogs.tts')
    bot.load_extension('cogs.admin')
    bot.load_extension('cogs.fun')
    bot.load_extension('cogs.uncategorized')

    bot.run(secret)
