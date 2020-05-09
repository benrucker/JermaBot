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
from jerma_exception import JermaException
from guild_info import GuildInfo
from help import helpEmbed, get_list_embed, make_sounds_dict, get_rand_activity
import ttsengine


YES = ['yes','yeah','yep','yeppers','of course','ye','y','ya','yah']
NO  = ['no','n','nope','start over','nada', 'nah']

colorama.init(autoreset=True)  # set up colored console out
guilds = dict()
tts = None

prefixes = ['$', '+']
bot = commands.Bot(command_prefix=commands.when_mentioned_or('$', '+'))


def has_sound_file(message):
    attachment = message.attachments[0]
    return attachment.filename.endswith('.mp3') or attachment.filename.endswith('.wav')


async def add_sound_to_guild(sound, guild, filename=None):
    folder = get_guild_sound_path(guild)
    if not filename:
        filename = sound.filename.lower()

    path = os.path.join(folder, filename)

    await sound.save(path)


def get_guild_sound_path(guild):
    ginfo = guilds[guild.id]
    return ginfo.sound_folder


def get_ben():
    return bot.get_user(bot.owner_id)

def get_soul_stone_channel(ctx):
    for channel in ctx.guild.voice_channels:
        if channel.id == 343939767068655616:
            return channel
    raise Exception('channel not found')


def is_major(member):
    if type(member) is commands.Context:
        member = member.author
    for role in member.roles:
        if role.id == 374095810868019200:
            return True
    return False


def get_snap_sound():
    sounds = []
    snaps_folder = os.path.join('resources', 'soundclips', 'snaps')
    snaps_db = os.path.join(snaps_folder, 'sounds.txt')
    with open(snaps_db, 'r', encoding='utf-8') as file:
        for sound in file.read().split('\n'):
            sounds.append(sound.split(' '))
    print(sounds)
    choice = random.choice(sounds)
    choice[0] = os.path.join(snaps_folder, choice[0])
    choice[1] = float(choice[1])
    choice[2] = float(choice[2])
    return choice


def get_quarantine_sound():
    sound = os.path.join('resources', 'soundclips', '2319.wav')
    with open(os.path.join('resources', 'soundclips', '2319.txt'), 'r', encoding='utf-8') as file:
        a = file.readline().split(' ')
    return [sound, float(a[0]), float(a[1])]


def get_smash_sound():
    sound = os.path.join('resources', 'soundclips', 'smash_kill.wav')
    with open(os.path.join('resources', 'soundclips', 'smash_kill.txt'), 'r', encoding='utf-8') as file:
        a = file.readline().split(' ')
    return [sound, float(a[0]), float(a[1])]

async def help_loop(ctx, msg):
    """WIP"""
    def check(reaction, user):
        return str(reaction.emoji) in ['⬅', '➡']
    while True:  # seconds
        try:
            reaction, _ = await bot.wait_for('reaction_add', timeout=30,
                                             check=check)
        except asyncio.TimeoutError:
            return
        else:
            if str(reaction.emoji) == '⬅':
                msg.edit(embed=helpEmbed)
                msg.remove_reaction(reaction.emoji, reaction.author)
            if str(reaction.emoji) == '➡':
                msg.edit(embed=get_list_embed(guilds[ctx.guild.id]))
                msg.remove_reaction(reaction.emoji, reaction.author)

async def process_scoreboard(ctx):
    """Add points to person's score"""
    score = int(ctx.command)

    if score not in [-3,-2,-1,1,2,3]:
        await ctx.send("That's too many JermaBucks dude! Do you think I'm made of cash? Only do 1-3.")
        return  # send failure message

    name = ' '.join(ctx.message.content.split(' ')[1:])
    user = None

    async for u in ctx.guild.fetch_members():
        if name.lower() in [u.name.lower(), u.display_name.lower()]:
            user = u

    if not name or not user:
        return

    g = guilds[ctx.guild.id]
    g.add_point(user.id, amount=score)

    if user.nick:
        name = user.nick
    else:
        name = user.name
    await ctx.send(name + ' now has a whopping ' + str(g.leaderboard[user.id]) + ' points. Wow!')


async def get_context_no_command(message, cls=commands.Context):
    """Constructs a context object from a given message."""

    view = discord.ext.commands.view.StringView(message.content)
    ctx = cls(prefix=None, view=view, bot=bot, message=message)

    if bot._skip_check(message.author.id, bot.user.id):
        return ctx

    prefix = await bot.get_prefix(message)
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
async def get_context(message, *, cls=discord.ext.commands.Context):
    """Constructs a context object from a given command."""

    view = discord.ext.commands.view.StringView(message.content)
    ctx = cls(prefix=None, view=view, bot=bot, message=message)

    if bot._skip_check(message.author.id, bot.user.id):
        return ctx

    prefix = await bot.get_prefix(message)
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
    ctx.command = bot.all_commands.get(invoker)
    return ctx


async def process_commands(message):
    """Process commands."""
    if message.author.bot:
        return

    ctx = await get_context_no_command(message)
    # print(type(ctx.prefix), ctx.prefix)
    if ctx.prefix == '+':
        await process_scoreboard(ctx)
    else:
        ctx.command = bot.all_commands.get(ctx.invoked_with)
        await bot.invoke(ctx)


@bot.event
async def on_message(message):
    """Log info about recevied messages and send to process."""
    if message.content.startswith('$$'):
        return  # protection against hackerbot commands
    # elif message.content.startswith('+'):
    #     await process_scoreboard(await get_context(message))
    #     return

    if message.content.startswith(tuple(prefixes)):
        print(f'[{time.ctime()}] {message.author.name} - {message.guild} #{message.channel}: {t.BLUE}{Style.BRIGHT}{message.content}')
    elif message.author == bot.user:
        print(f'[{time.ctime()}] {message.author.name} - {message.guild} #{message.channel}: {message.content}')

    await process_commands(message)
    #except AttributeError as _:
    #    pass # ignore embed-only messages
    #except Exception as e:
    #    print(e)
# ----------------------------------------------------------------------------


@bot.command()
async def jermahelp(ctx):
    """Send the user important info about JermaBot."""
    avatar = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='avatar.png')
    thumbnail = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='thumbnail.png')
    await ctx.author.send(files=[avatar, thumbnail], embed=helpEmbed)
    await ctx.message.add_reaction("✉")


@bot.command()
@commands.check(is_major)
async def resetscore(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    name = ' '.join(args[0:])
    async for u in ctx.guild.fetch_members():
        if name.lower() in [u.name.lower(), u.display_name.lower()]:
            user = u

    g = guilds[ctx.guild.id]
    g.reset_score(user.id)


@bot.command()
async def addpoint(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    name = ' '.join(args[0:])
    async for u in ctx.guild.fetch_members():
        if name.lower() in [u.name.lower(), u.display_name.lower()]:
            user = u

    g = guilds[ctx.guild.id]
    g.add_point(user.id)

    await ctx.send(user.name + ' now has a whopping ' + str(g.leaderboard[user.id]) + ' points. Wow!')


@bot.command()
async def leaderboard(ctx):
    try:
        lb = guilds[ctx.guild.id].leaderboard
        lbord = OrderedDict(sorted(lb.items(), key=lambda t: t[1], reverse=True))
        out = str()
        for i in lbord:
            user = ctx.guild.get_member(i)
            if user.nick:
                name = user.nick
            else:
                name = user.name
            out += '' + name + ' - ' + str(lbord[i]) + '\n'
        await ctx.send(out)
    except Exception as e:
        print(traceback.format_exception(None,  # <- type(e) by docs, but ignored
                                         e, e.__traceback__),
              file=sys.stderr, flush=True)


@bot.command()
async def drake(ctx, *args):
    if not args:
        return
    try:
        msg = await ctx.channel.fetch_message(args[0])
    except:
        pass
    await ctx.message.delete(delay=1)
    await msg.add_reaction('drake:679179726740258826')
    await asyncio.sleep(5)
    await msg.remove_reaction('drake:679179726740258826', bot.user)


@bot.command()
async def jermasnap(ctx):
    """Snap the user's voice channel."""
    print('jermasnap')
    if not is_major(ctx.author):
        return

    vc = await connect_to_user(ctx)
    soul_stone = get_soul_stone_channel(ctx)

    users = ctx.author.voice.channel.members
    users.remove(ctx.me)
    snapees = random.sample(users, k=len(users) // 2)

    guilds[ctx.guild.id].is_snapping = True

    sound, delay, length = get_snap_sound()
    vc.play(discord.FFmpegPCMAudio(sound))
    time.sleep(delay)

    for snapee in snapees:
        await snapee.move_to(soul_stone)

    time.sleep(length - delay)
    do_moonlight = random.random() < 0.25
    if do_moonlight:
        await ctx.guild.voice_client.move_to(soul_stone)
        vc.play(discord.FFmpegPCMAudio(os.path.join('soundclips', 'moonlight.wav')))
        await asyncio.sleep(1)
        guilds[ctx.guild.id].is_snapping = False
    else:
        vc.play(discord.FFmpegPCMAudio(os.path.join('resources', 'soundclips', 'snaps', 'up in smoke.mp3')))
        await asyncio.sleep(1)
        guilds[ctx.guild.id].is_snapping = False


@bot.command()
async def q(ctx, *args):
    await quarantine(ctx, args)


@bot.command()
async def quarantine(ctx, *args):
    """Save'm."""
    if not args:
        raise discord.InvalidArgument

    name = ' '.join(args[0:])

    vc = await connect_to_user(ctx)
    dest_channel = get_soul_stone_channel(ctx)

    users = ctx.author.voice.channel.members
    user = None
    for u in users:
        if name.lower() in [u.name.lower(), u.display_name.lower()]:
            user = u

    if not user:
        return

    sound, delay, length = get_quarantine_sound()
    
    guilds[ctx.guild.id].is_snapping = True
    bot.get_cog('SoundPlayer').play_sound_file(sound, vc, output=True)
    time.sleep(delay)
    await user.move_to(dest_channel)
    time.sleep(length - delay)
    guilds[ctx.guild.id].is_snapping = False


@bot.command()
async def fsmash(ctx, *args):
    """Killem."""
    if not args:
        raise discord.InvalidArgument

    name = ' '.join(args[0:])

    vc = await connect_to_user(ctx)
    dest_channel = get_soul_stone_channel(ctx)

    users = ctx.author.voice.channel.members
    user = None
    for u in users:
        if name.lower() in [u.name.lower(), u.display_name.lower()]:
            user = u

    if not user:
        return

    sound, delay, length = get_smash_sound()
    
    guilds[ctx.guild.id].is_snapping = True
    bot.get_cog('SoundPlayer').play_sound_file(sound, vc, output=True)
    time.sleep(delay)
    await user.move_to(dest_channel)
    time.sleep(length - delay)
    guilds[ctx.guild.id].is_snapping = False


@bot.command()
async def downsmash(ctx, *args):
    """Killem even more."""
    if not args:
        raise discord.InvalidArgument()  # malformed statement?

    name = ' '.join(args[0:])

    vc = await connect_to_user(ctx)

    users = ctx.author.voice.channel.members
    user = None
    for u in users:
        if name.lower() in [u.name.lower(), u.display_name.lower()]:
            user = u

    if not user:
        return

    sound, delay, length = get_smash_sound()
    
    guilds[ctx.guild.id].is_snapping = True
    bot.get_cog('SoundPlayer').play_sound_file(sound, vc, output=True)
    time.sleep(delay)
    await user.move_to(None)
    time.sleep(length - delay)
    guilds[ctx.guild.id].is_snapping = False

@bot.command()
async def jermalofi(ctx):
    """Chill with a sick jam."""
    print('jermalofi')
    vc = await connect_to_user(ctx)
    id = ctx.guild.id
    vc.play(bot.get_cog('SoundPlayer').LoopingSource(os.path.join('resources', 'soundclips', 'birthdayloop.wav'),
                                                    bot.get_cog('SoundPlayer').source_factory,
                                                    id))

@commands.is_owner()
@bot.command()
async def update(ctx):
    """Update the bot."""
    try:
        if (sys.platform.startswith('win')):
            result = subprocess.run(['git', 'pull'], shell=True, text=True, capture_output=True)
        else:
            result = subprocess.run(['git pull'], shell=True, text=True, capture_output=True)
    except Exception as e:
        print(e.with_traceback)
        await ctx.send('Something ain\'t right here, pal.')
        return

    if result.returncode != 0:
        await ctx.send('Uhh, gamer? Something didn\'t go right.')
        print(result.returncode)
        print(result.stdout)
    elif 'Already up to date' in result.stdout:
        await ctx.send("Patch notes:\n - Lowered height by 2 inches to allow for more clown car jokes")
    else:
        await ctx.send('Patch applied, sister.')
        await shutdown()


@bot.event
async def on_ready():
    """Initialize some important data and indicate startup success."""
    global guilds
    await bot.change_presence(activity=get_rand_activity())

    if len(guilds) == 0:
        guilds = dict()
        for guild in bot.guilds:
            os.makedirs(os.path.join('guilds',f'{guild.id}','sounds'), exist_ok=True)
            guilds[guild.id] = GuildInfo(guild)

    # with open('avatar.png', 'rb') as file:
    #     await bot.user.edit(avatar=file.read()) # move to on_guild_add
    print(f'Logged into {str(len(guilds))} guilds:')
    for guild in list(guilds.values()):
        print(f'\t{guild.name}:{guild.id}')
    print("Let's fucking go, bois.")


@bot.event
async def on_guild_join(guild):
    os.makedirs(os.path.join('guilds',f'{guild.id}','sounds'), exist_ok=True)
    guilds[guild.id] = GuildInfo(guild)
    print(f'Added to {guild.name}:{guild.id}!')


@bot.event
async def on_guild_update(before, after):
    print(f'Guild {before.name} updated.')
    if before.voice_client and not before.region == after.region:
        print(f'Disconnecting from {before.name} due to region switch.')
        await before.voice_client.disconnect()

@bot.event
async def on_command_error(ctx, e):
    """Catch errors and handle them."""
    if hasattr(e, 'original') and type(e.original) is JermaException:
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


if __name__ == '__main__':
    global source_path
    source_path = os.path.dirname(os.path.abspath(__file__))  # /a/b/c/d/e

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

    try:
        os.makedirs(os.path.join('resources', 'soundclips', 'temp'))
    except:
        pass

    bot.run(secret)
