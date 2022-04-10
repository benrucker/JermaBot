import argparse
import logging
import os
import sys
import time
import traceback

import colorama
import discord
from colorama import Fore as t
from colorama import Style
from discord import app_commands
from discord.ext import commands

from cogs.utils import ttsengine
from guild_info import GuildInfo
from cogs.s_guild_sounds import SGuildSounds

YES = ['yes', 'yeah', 'yep', 'yeppers', 'of course', 'ye', 'y', 'ya', 'yah']
NO = ['no', 'n', 'nope', 'start over', 'nada', 'nah']


colorama.init(autoreset=True)
guilds = dict()
tts = None
intents = discord.Intents.default()
# intents.members = True

prefixes = ['$', '+']


class JermaBot(commands.Bot):
    """Base JermaBot class."""

    def __init__(self, path, **kwargs):
        """
        Construct JermaBot.

        Sets the path and guild_info attrs to be accessed by cogs. Calls
        its superclass's constructor as well.
        """
        self.path = path
        self.guild_infos = dict()
        super().__init__(**kwargs)

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.admin")
        return await super().setup_hook()

    async def on_ready(self):
        """Initialize some important data and indicate startup success."""
        if len(self.get_guildinfo()) == 0:
            self.initialize_guild_infos()

        print(f'Logged into {len(self.guilds)} guilds:')
        for guild in list(self.guilds):
            print(f'\t{guild.name}:{guild.id}')
        print("Let's fucking go, bois.")

    def get_guildinfo(self, gid=None):
        """Return a GuildInfo object for the given guild id."""
        if not gid:
            return self.guild_infos
        else:
            return self.guild_infos[gid]

    def make_guildinfo(self, guild: discord.Guild):
        """Set a guildinfo object to a certain id."""
        self.guild_infos[guild.id] = GuildInfo(guild)

    def initialize_guild_infos(self):
        """Construct dictionary of guildinfo objects."""
        for guild in self.guilds:
            os.makedirs(os.path.join(
                'guilds', f'{guild.id}', 'sounds'), exist_ok=True)
            self.guild_infos[guild.id] = GuildInfo(guild)


if __name__ == '__main__':
    global source_path, bot
    source_path = os.path.dirname(os.path.abspath(__file__))

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description='Run JermaBot')
    parser.add_argument('-s', '--secret_filename',
                        help='location of bot token text file',
                        default='secret.txt')
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument('-mycroft', '--mycroft_path',
                       help='tell jerma to use mycroft at the given path')
    group.add_argument('-voice', '--voice_path',
                       help='tell jerma to use windows voice.exe tts engine')
    group.add_argument('-espeak', help='tell jerma to use espeak tts engine',
                       action='store_true')
    parser.add_argument('-mv', '--mycroft_voice',
                        help='name of OR path to the voice to use with mycroft')
    parser.add_argument('-jv', '--japanese_voice',
                        help='path to voice to use with Japanese TTS')
    parser.add_argument('-jd', '--japanese_dict',
                        help='path to dictionary to use with Japanese TTS')
    args = parser.parse_args()

    with open(args.secret_filename) as f:
        secret = f.read().strip()

    if args.mycroft_path:
        voice = args.mycroft_voice if args.mycroft_voice else 'ap'
        tts = ttsengine.construct(
            engine=ttsengine.MYCROFT,
            path=args.mycroft_path,
            voice=voice
        )
    elif args.voice_path:
        tts = ttsengine.construct(
            engine=ttsengine.VOICE,
            path=args.voice_path
        )
    elif args.espeak:
        tts = ttsengine.construct(
            engine=ttsengine.ESPEAK
        )
    else:
        print('Setting TTS to none')
        tts = None

    if args.japanese_voice and args.japanese_dict:
        print('setting JTTS to openjtalk')
        print('openjtalk voice at:', args.japanese_voice)
        jtts = ttsengine.construct(engine=ttsengine.OPEN_JTALK,
                                   voice=args.japanese_voice,
                                   dic=args.japanese_dict)
    else:
        print('setting jtts to None')
        jtts = None

    try:
        os.makedirs(os.path.join('resources', 'soundclips', 'temp'))
    except:
        pass

    bot = JermaBot(
        source_path,
        command_prefix=commands.when_mentioned_or('$', '+'),
        intents=intents
    )
    bot.tts_engine = tts
    bot.jtts_engine = jtts

    bot.tree.add_command(SGuildSounds(bot), guild=discord.Object(id=571004411137097731))

    bot.run(secret)
