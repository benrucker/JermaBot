from colorama import Fore as t
from colorama import Back, Style
from discord.ext import commands
import discord
import time
import os
from discord.embeds import Embed
import random
from discord.activity import Activity
from discord.enums import ActivityType
import traceback
import sys


activities = [(ActivityType.listening, 'my heart.'),
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
              (ActivityType.watching, 'the byeahs.'), # comma on last item is good
             ]


def setup(bot):
    bot.add_cog(Uncategorized(bot))


class Uncategorized(commands.Cog):
    def __init__(self, bot):
       self.bot = bot
       self.help_embed = self.make_help_embed()

    def make_help_embed(self):
        h = Embed(title=" help | list of all useful commands", description="Fueling epic gamer moments since 2011", color=0x66c3cb)
        h.set_author(name="Jermabot Help", url="https://www.youtube.com/watch?v=fnbvTOcNFhU", icon_url="attachment://avatar.png")
        h.set_thumbnail(url="attachment://thumbnail.png")
        h.add_field(name="speak <words>", 
                    value="Jerma joins the channel and says what you input in the command using voice.exe .", inline=True)
        h.add_field(name="speakdrunk <stuff>",
                    value="Same as speak but more like a streamer during a Labo build.", inline=True)
        h.add_field(name="adderall <things>", 
                    value="Same as speak but more like a streamer during a bitcoin joke.", inline=True)
        h.add_field(name="play <sound>", 
                    value="Jerma joins the channel and plays the sound specified by name.", inline=True)
        h.add_field(name="birthday <name>", 
                    value="Jerma joins the channel and plays a birthday song for the person with the given name.", inline=True)
        h.add_field(name="join", value=" Jerma joins the channel.", inline=True)
        h.add_field(name="leave", value="Jerma leaves the channel.", inline=True)
        h.add_field(name="jermalofi", value="Jerma joins the channel and plays some rats lofi.", inline=True)
        h.set_footer(text="Message @bebenebenebeb#9414 or @fops#1969 with any questions")
        return h

    def is_major(self, member):
        if type(member) is commands.Context:
            member = member.author
        for role in member.roles:
            if role.id == 374095810868019200:
                return True
        return False

    async def get_context_no_command(self, message, cls=commands.Context):
        """Constructs a context object from a given message."""

        view = discord.ext.commands.view.StringView(message.content)
        ctx = cls(prefix=None, view=view, bot=self.bot, message=message)

        if self.bot._skip_check(message.author.id, self.bot.user.id):
            return ctx

        prefix = await self.bot.get_prefix(message)
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
        """Constructs a context object from a given command."""

        view = discord.ext.commands.view.StringView(message.content)
        ctx = cls(prefix=None, view=view, bot=self.bot, message=message)

        if self.bot._skip_check(message.author.id, self.bot.user.id):
            return ctx

        prefix = await self.bot.get_prefix(message)
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
        ctx.command = self.bot.all_commands.get(invoker)
        return ctx

    async def process_commands(self, message):
        """Process commands."""
        if message.author.bot:
            return

        ctx = await self.get_context_no_command(message)
        # print(type(ctx.prefix), ctx.prefix)
        if ctx.prefix == '+':
            await self.bot.get_cog('Scoreboard').process_scoreboard(ctx)
        else:
            ctx.command = self.bot.all_commands.get(ctx.invoked_with)
            await self.bot.invoke(ctx)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Log info about recevied messages and send to process."""
        if message.content.startswith('$$'):
            return  # protection against hackerbot commands
        # elif message.content.startswith('+'):
        #     await process_scoreboard(await get_context(message))
        #     return

        if message.content.startswith(tuple(self.bot.command_prefix)):
            print(f'[{time.ctime()}] {message.author.name} - {message.guild} #{message.channel}: {t.BLUE}{Style.BRIGHT}{message.content}')
        elif message.author == self.bot.user:
            print(f'[{time.ctime()}] {message.author.name} - {message.guild} #{message.channel}: {message.content}')

        await self.process_commands(message)
        #except AttributeError as _:
        #    pass # ignore embed-only messages
        #except Exception as e:
        #    print(e)
    # ----------------------------------------------------------------------------

    @commands.command()
    async def jermahelp(self, ctx):
        """Send the user important info about JermaBot."""
        avatar = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='avatar.png')
        thumbnail = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='thumbnail.png')
        await ctx.author.send(files=[avatar, thumbnail], embed=self.help_embed)
        await ctx.message.add_reaction("✉")

    def get_rand_activity(self):
        info = random.choice(activities)
        return Activity(name=info[1], type=info[0])

    @commands.Cog.listener()
    async def on_ready(self):
        """Initialize some important data and indicate startup success."""
        await self.bot.change_presence(activity=self.get_rand_activity())

        if len(self.bot.get_guildinfo()) == 0:
            self.bot.initialize_guild_infos()

        print(f'Logged into {len(self.bot.guilds)} guilds:')
        for guild in list(self.bot.guilds):
            print(f'\t{guild.name}:{guild.id}')
        print("Let's fucking go, bois.")

    @commands.Cog.listener()
    async def on_command_error(self, ctx, e):
        """Catch errors and handle them."""
        if hasattr(e, 'original') and type(e.original) is self.bot.JermaException:
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
