import argparse
import colorama
from colorama import Fore as t
from colorama import Style
import discord
from discord.ext import commands
import logging
import os
import random
import sys
import time
import traceback

from jerma_exception import JermaException
from guild_info import GuildInfo
import ttsengine
from discord.enums import ActivityType
from discord.activity import Activity


YES = ['yes','yeah','yep','yeppers','of course','ye','y','ya','yah']
NO  = ['no','n','nope','start over','nada', 'nah']


ACTIVITIES = [(ActivityType.listening, 'my heart.'),
              (ActivityType.listening, 'rats lofi'),
              (ActivityType.listening, 'Shigurain.'),
              (ActivityType.listening, 'Kim!'),
              (ActivityType.listening, 'the DOOM OST.'),
              (ActivityType.listening, 'a clown podcast.'),
              (ActivityType.streaming, 'DARK SOULS III'),
              (ActivityType.streaming, 'Just Cause 4 for 3DS'),
              (ActivityType.watching, 'chat make fun of me.'),
              (ActivityType.watching, 'E3® 2022.'),
              (ActivityType.watching, 'GrillMasterxBBQ\'s vids.'),
              (ActivityType.watching, 'the byeahs.'),  # comma on last item is good
             ]


colorama.init(autoreset=True)  # set up colored console out
guilds = dict()
tts = None

prefixes = ['$', '+']


class JermaBot(commands.Bot):
    """Base JermaBot class."""

    def __init__(self, path, command_prefix=None):
        """
        Construct JermaBot.

        Sets the path and guild_info attrs to be accessed by cogs. Calls
        its superclass's constructor as well.
        """
        self.path = path
        self.guild_infos = dict()
        super().__init__(command_prefix=command_prefix)

    def get_guildinfo(self, id=None):
        """Return a GuildInfo object for the given guild id."""
        if not id:
            return self.guild_infos
        else:
            return self.guild_infos[id]

    def make_guildinfo(self, guild: discord.Guild):
        """Set a guildinfo object to a certain id."""
        self.guild_infos[guild.id] = GuildInfo(guild)

    def initialize_guild_infos(self):
        """Construct dictionary of guildinfo objects."""
        for guild in self.guilds:
            os.makedirs(os.path.join('guilds', f'{guild.id}', 'sounds'), exist_ok=True)
            self.guild_infos[guild.id] = GuildInfo(guild)

    def JermaException(self, err, msg):
        """Return a JermaException. Useful for added cogs to access."""
        return JermaException(err, msg)

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
        if hasattr(e, 'original') and type(e.original) is self.JermaException:
            e2 = e.original
            print(f'{t.RED}Caught JermaException: ' + str(e2))
            await ctx.send(e2.message)
        # if type(e) is commands.errors.CommandInvokeError:
            #else:
                #ben = get_ben()
                #mention = ben.mention + ' something went bonkers.'
                #await ctx.send(mention if ben else 'Something went crazy wrong. Sorry gamers.')
        else:
            if type(e) is commands.errors.CheckFailure:
                await ctx.send('You don\'t have the correct server permissions to do that, dude.')

            # return to default discord.py behavior circa 2020.4.25
            # https://github.com/Rapptz/discord.py/discord/ext/commands/bot.py
            if hasattr(ctx.command, 'on_error'):
                return

            cog = ctx.cog
            if cog:
                if discord.ext.commands.Cog._get_overridden_method(cog.cog_command_error) is not None:
                    return

            print('Ignoring exception in command {}:'.format(ctx.command), file=sys.stderr)
            traceback.print_exception(type(e), e, e.__traceback__, file=sys.stderr)
            # end plaigarism

    async def get_context_no_command(self, message, cls=commands.Context):
        """Construct a context object from a given message."""
        view = discord.ext.commands.view.StringView(message.content)
        ctx = cls(prefix=None, view=view, bot=self, message=message)

        if self._skip_check(message.author.id, self.user.id):
            return ctx

        prefix = await self.get_prefix(message)
        invoked_prefix = prefix

        if isinstance(prefix, str):
            if not view.skip_string(prefix):
                return ctx
        else:
            try:
                # if the context class' __init__ consumes something from the view this
                # will be wrong.  That seems unreasonable though.
                if message.content.startswith(tuple(prefix)):
                    invoked_prefix = discord.utils.find(view.skip_string, prefix)
                else:
                    return ctx

            except TypeError:
                if not isinstance(prefix, list):
                    raise TypeError("get_prefix must return either a string or a list of string, "
                                    "not {}".format(prefix.__class__.__name__))

                # It's possible a bad command_prefix got us here.
                for value in prefix:
                    if not isinstance(value, str):
                        raise TypeError("Iterable command_prefix or list returned from get_prefix must "
                                        "contain only strings, not {}".format(value.__class__.__name__))

                # Getting here shouldn't happen
                raise

        invoker = view.get_word()
        ctx.invoked_with = invoker
        ctx.prefix = invoked_prefix
        ctx.command = invoker
        return ctx

    # -------------- Overrides --------------------
    async def get_context(self, message, *, cls=discord.ext.commands.Context):
        """Construct a context object from a given command."""
        view = discord.ext.commands.view.StringView(message.content)
        ctx = cls(prefix=None, view=view, bot=self, message=message)

        if self._skip_check(message.author.id, self.user.id):
            return ctx

        prefix = await self.get_prefix(message)
        invoked_prefix = prefix

        if isinstance(prefix, str):
            if not view.skip_string(prefix):
                return ctx
        else:
            try:
                # if the context class' __init__ consumes something from the view this
                # will be wrong.  That seems unreasonable though.
                if message.content.startswith(tuple(prefix)):
                    invoked_prefix = discord.utils.find(view.skip_string, prefix)
                else:
                    return ctx

            except TypeError:
                if not isinstance(prefix, list):
                    raise TypeError("get_prefix must return either a string or a list of string, "
                                    "not {}".format(prefix.__class__.__name__))

                # It's possible a bad command_prefix got us here.
                for value in prefix:
                    if not isinstance(value, str):
                        raise TypeError("Iterable command_prefix or list returned from get_prefix must "
                                        "contain only strings, not {}".format(value.__class__.__name__))

                # Getting here shouldn't happen
                raise

        invoker = view.get_word()
        ctx.invoked_with = invoker
        ctx.prefix = invoked_prefix
        ctx.command = self.all_commands.get(invoker)
        return ctx

    async def process_commands(self, message):
        """Process commands."""
        if message.author.bot:
            return

        ctx = await self.get_context_no_command(message)
        # print(type(ctx.prefix), ctx.prefix)
        if ctx.prefix == '+':
            await self.get_cog('Scoreboard').process_scoreboard(ctx)
        else:
            ctx.command = self.all_commands.get(ctx.invoked_with)
            await self.invoke(ctx)

    async def on_message(self, message):
        """Log info about recevied messages and send to process."""
        if message.content.startswith('$$'):
            return  # protection against hackerbot commands
        # elif message.content.startswith('+'):
        #     await process_scoreboard(await get_context(message))
        #     return

        if message.content.startswith(('$', '+')):
            print(f'[{time.ctime()}] {message.author.name} - {message.guild} #{message.channel}: {t.BLUE}{Style.BRIGHT}{message.content}')
        elif message.author == self.user:
            print(f'[{time.ctime()}] {message.author.name} - {message.guild} #{message.channel}: {message.content}')

        await self.process_commands(message)
        #except AttributeError as _:
        #    pass # ignore embed-only messages
        #except Exception as e:
        #    print(e)


if __name__ == '__main__':
    global source_path, bot
    source_path = os.path.dirname(os.path.abspath(__file__))  # /a/b/c/d/e

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description='Run JermaBot')
    parser.add_argument('-s', '--secret_filename', help='location of bot token text file')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-mycroft', '--mycroft_path', help='tell jerma to use mycroft at the given path')
    group.add_argument('-voice', '--voice_path', help='tell jerma to use windows voice.exe tts engine')
    group.add_argument('-espeak', help='tell jerma to use espeak tts engine',
                       action='store_true')
    parser.add_argument('-mv', '--mycroft_voice', help='name of OR path to the voice to use with mycroft')
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

    try:
        os.makedirs(os.path.join('resources', 'soundclips', 'temp'))
    except:
        pass

    bot = JermaBot(source_path, command_prefix=commands.when_mentioned_or('$', '+'))
    bot.tts_engine = tts

    bot.load_extension('cogs.guild_sounds')
    bot.load_extension('cogs.sound_player')
    bot.load_extension('cogs.control')
    bot.load_extension('cogs.tts')
    bot.load_extension('cogs.admin')
    bot.load_extension('cogs.fun')
    bot.load_extension('cogs.uncategorized')

    bot.run(secret)
