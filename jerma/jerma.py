import argparse
import logging
import os
import random
import sys
import time
import traceback

import colorama
from colorama import Fore as t
from colorama import Style
import discord
from discord.activity import Activity
from discord.enums import ActivityType
from discord.ext import commands

from cogs.utils import ttsengine
from guild_info import GuildInfo


YES = ['yes', 'yeah', 'yep', 'yeppers', 'of course', 'ye', 'y', 'ya', 'yah']
NO = ['no', 'n', 'nope', 'start over', 'nada', 'nah']


ACTIVITIES = [
    (ActivityType.listening, 'my heart.'),
    (ActivityType.listening, 'rats lofi'),
    (ActivityType.listening, 'Shigurain.'),
    (ActivityType.listening, 'a Scream Compilation.'),
    (ActivityType.listening, 'the DOOM OST.'),
    (ActivityType.listening, 'a clown podcast.'),
    (ActivityType.streaming, 'DARK SOULS III'),
    (ActivityType.streaming, 'Just Cause 4 for 3DS'),
    (ActivityType.watching, 'chat make fun of me.'),
    (ActivityType.watching, 'E3® 2022.'),
    (ActivityType.watching, 'GrillMasterxBBQ\'s vids.'),
    (ActivityType.watching, 'your form, bro!'),
    (ActivityType.watching, 'the byeahs.'),
]


colorama.init(autoreset=True)
guilds = dict()
tts = None
intents = discord.Intents.default()
intents.members = True

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

    def get_rand_activity(self):
        """Return an Activity object with a random value."""
        info = random.choice(ACTIVITIES)
        return Activity(name=info[1], type=info[0])

    async def on_ready(self):
        """Initialize some important data and indicate startup success."""
        await self.change_presence(activity=self.get_rand_activity())

        if len(self.get_guildinfo()) == 0:
            self.initialize_guild_infos()

        print(f'Logged into {len(self.guilds)} guilds:')
        for guild in list(self.guilds):
            print(f'\t{guild.name}:{guild.id}')
        print("Let's fucking go, bois.")

    async def on_command_error(self, ctx, e):
        """Catch errors and handle them."""
        if type(e) is commands.errors.CheckFailure:
            print(e)
            await ctx.send('Something went wrong, dude. You probably don\'t have the correct server permissions to do that.')

        # return to default discord.py behavior circa 2020.4.25
        # https://github.com/Rapptz/discord.py/discord/ext/commands/bot.py
        if hasattr(ctx.command, 'on_error'):
            return

        cog = ctx.cog
        if cog:
            if discord.ext.commands.Cog._get_overridden_method(cog.cog_command_error) is not None:
                return

        print('Ignoring exception in command {}:'.format(
            ctx.command), file=sys.stderr)
        traceback.print_exception(type(e), e, e.__traceback__, file=sys.stderr)
        # end plaigarism

    async def process_commands(self, message):
        """Process commands."""
        if message.author.bot:
            return

        ctx = await self.get_context(message)
        await self.invoke(ctx)

    async def on_message(self, message):
        """Log info about recevied messages and send to process."""
        if message.content.startswith('$$'):
            return  # protection against hackerbot commands

        if message.content.startswith(('$')):
            print(f'[{time.ctime()}] {message.author.name} - {message.guild} #{message.channel}: {t.BLUE}{Style.BRIGHT}{message.content}')
        elif message.author == self.user:
            print(
                f'[{time.ctime()}] {message.author.name} - {message.guild} #{message.channel}: {message.content}')

        await self.process_commands(message)


if __name__ == '__main__':
    global source_path, bot
    source_path = os.path.dirname(os.path.abspath(__file__))

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description='Run JermaBot')
    parser.add_argument('-s', '--secret_filename',
                        help='location of bot token text file')
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
        tts = ttsengine.construct(
            engine=ttsengine.MYCROFT, path=args.mycroft_path, voice=_v)
    elif args.voice_path:
        tts = ttsengine.construct(engine=ttsengine.VOICE, path=args.voice_path)
    elif args.espeak:
        tts = ttsengine.construct(engine=ttsengine.ESPEAK)
    else:
        print('Setting TTS to none')
        tts = None

    try:
        os.makedirs(os.path.join('resources', 'soundclips', 'temp'))
    except:
        pass

    bot = JermaBot(source_path, command_prefix=commands.when_mentioned_or('$', '+'), intents=intents)
    bot.tts_engine = tts
    if args.japanese_voice and args.japanese_dict:
        print('setting JTTS to openjtalk')
        print('openjtalk voice at:', args.japanese_voice)
        bot.jtts_engine = ttsengine.construct(engine=ttsengine.OPEN_JTALK,
                                              voice=args.japanese_voice,
                                              dic=args.japanese_dict)
    else:
        print('setting jtts to None')
        bot.jtts_engine = None

    bot.load_extension('cogs.guild_sounds')
    bot.load_extension('cogs.sound_player')
    bot.load_extension('cogs.control')
    bot.load_extension('cogs.tts')
    bot.load_extension('cogs.admin')
    bot.load_extension('cogs.fun')

    bot.run(secret)
