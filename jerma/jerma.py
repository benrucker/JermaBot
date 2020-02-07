import asyncio
import sys
import time
import discord
import random
import os
import logging
import colorama
from colorama import Fore as t
from colorama import Back, Style
from glob import glob
from discord.ext import commands
import subprocess
from pydub import AudioSegment
from guild_info import GuildInfo

from help import helpEmbed, get_list_embed, make_sounds_dict, get_rand_activity


colorama.init(autoreset=True) # set up colored console out

tts_path = 'resources/voice.exe'

prefix = '$'
bot = commands.Bot(prefix)


def check_perms(user, action):
    if action is 'addsound':
        if is_major(user):
            return
        else:
            raise JermaException('invalid permissions to do ' + addsound,
                                 'You don\'t got permission to do that, pardner.')
    else:
        raise NotImplementedError()


def has_sound_file(message):
    attachment = message.attachments[0]
    return attachment.filename.endswith('.mp3') or attachment.filename.endswith('.wav')


async def add_sound_to_guild(sound, guild):
    folder = get_guild_sound_path(guild)
    filename = sound.filename.lower()

    path = os.path.join(folder, filename)

    await sound.save(path)


def get_guild_sound_path(guild):
    ginfo = guilds[guild.id]
    return ginfo.sound_folder


def get_ben():
    return bot.get_user(bot.owner_id)


def play_sound_file(sound, vc, output=True):
    source = source_factory(sound)
    source.volume = guilds[vc.channel.guild.id].volume
    stop_audio(vc)
    vc.play(source)

    if output:
        c = t.CYAN
        print(f'Playing {sound} | at volume: {source.volume} | in: {c}{vc.guild} #{vc.channel}')


def play_text(vc, to_speak, ctx, label, _speed=0):
    sound_file = text_to_wav(to_speak, ctx, label, speed=_speed)
    play_sound_file(sound_file, vc)


def stop_audio(vc):
    if vc.is_playing():
        vc.stop()
        silence = os.path.join('resources', 'soundclips', 'silence.wav')
        play_sound_file(silence, vc, output=False)
        #time.sleep(.07)
        while vc.is_playing():
            continue


def source_factory(filename):
    op = '-guess_layout_max 0'
    return discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(filename, before_options=op))


def get_sound(sound, guild):
    ginfo = guilds[guild.id]
    sounds = make_sounds_dict(ginfo.sound_folder)
    try:
        return os.path.join(ginfo.sound_folder, sounds[sound.lower()])
    except KeyError:
        return None


def delete_sound(sound, guild):
    path = guilds[guild.id].sound_folder
    os.remove(sound)


def rename_file(old_filepath, new_filepath):
    os.rename(old_filepath, new_filepath)


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


def text_to_wav(text, ctx, label, speed=0):
    soundclip = generate_id_path(label, ctx)
    file = os.path.join(source_path, soundclip)
    subprocess.call([tts_path, '-r', str(speed), '-o', file, text])
    return soundclip


def generate_id_path(label, ctx):
    return os.path.join('resources', 'soundclips', 'temp', label + str(ctx.guild.id) + '.wav')


async def connect_to_user(ctx):
    try:
        vc = ctx.voice_client
        user_channel = ctx.author.voice.channel
        return await connect_to_channel(user_channel, vc)
    except:
        raise JermaException('User was not in a voice channel or something.',
                             msg='Hey gamer, you\'re not in a voice channel. Totally uncool.')


async def connect_to_channel(channel, vc=None):
    if not channel:
        raise AttributeError('channel cannot be None.')

    if not vc:
        vc = await channel.connect(reconnect=False)

    if vc.channel is not channel:
        await vc.move_to(channel)

    return vc


def get_existing_voice_client(guild):
    for vc in bot.voice_clients:
        if vc.guild is guild:
            return vc


def birthday_wave(name, ctx):
    """Construct birthday sound."""
    song = AudioSegment.from_wav(os.path.join('resources', 'soundclips', 'blank_birthday.wav'))
    name = AudioSegment.from_wav(name)
    insert_times = [7.95 * 1000, 12.1 * 1000]

    for insert_time in insert_times:
    	song = song.overlay(name, position=insert_time)

    outpath = generate_id_path('birthday_name', ctx)
    song.export(outpath)
    return outpath


async def help_loop(ctx, msg):
    """WIP"""
    def check(reaction, user):
        return str(reaction.emoji) in ['⬅', '➡']
    while True: # seconds
        try:
            reaction, _ = await bot.wait_for('reaction_add', timeout=30,
                                             check=check)
        except asyncio.TimeoutError:
            return
        else:
            if str(reaction.emoji) is '⬅':
                msg.edit(embed=helpEmbed)
                msg.remove_reaction(reaction.emoji, reaction.author)
            if str(reaction.emoji) is '➡':
                msg.edit(embed=get_list_embed(guilds[ctx.guild.id]))
                msg.remove_reaction(reaction.emoji, reaction.author)


@bot.event
async def on_message(message):
    """Log info about recevied messages and send to process."""
    if message.content.startswith('$$'):
        return # protection against hackerbot commands

    if message.content.startswith(prefix):
        print(f'{message.author.name} - {message.guild} #{message.channel}: {t.BLUE}{Style.BRIGHT}{message.content}')
    elif message.author == bot.user:
        print(f'{message.author.name} - {message.guild} #{message.channel}: {message.content}')

    await bot.process_commands(message)
    #except AttributeError as _:
    #    pass # ignore embed-only messages
    #except Exception as e:
    #    print(e)


@bot.command()
async def join(ctx):
    """Join the user's voice channel."""
    _ = await connect_to_user(ctx)


@bot.command()
async def leave(ctx):
    """Leave the voice channel, if any."""
    if ctx.voice_client and ctx.voice_client.is_connected():
        loc = os.path.join('resources', 'soundclips', 'leave')
        sounds = make_sounds_dict(loc)
        soundname = random.choice(list(sounds.values()))
        sound = os.path.join(loc, soundname)
        play_sound_file(sound, ctx.voice_client)
        time.sleep(1)
        await ctx.guild.voice_client.disconnect()


@bot.command()
async def perish(ctx):
    """Shut down the bot."""
    await bot.close()


@bot.command()
async def jermahelp(ctx):
    """Send the user important info about JermaBot."""
    avatar = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='avatar.png')
    thumbnail = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='thumbnail.png')
    await ctx.author.send(files=[avatar, thumbnail], embed=helpEmbed)
    await ctx.message.add_reaction("✉")


@bot.command(name='list')
async def _list(ctx):
    """Send the user a list of sounds that can be played."""
    ginfo = guilds[ctx.guild.id]
    avatar = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='avatar.png')
    thumbnail = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='thumbnail.png')
    await ctx.author.send(files=[avatar, thumbnail], embed=get_list_embed(ginfo))
    await ctx.message.add_reaction("✉")


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
async def jermalofi(ctx):
    """Chill with a sick jam."""
    print('jermalofi')
    vc = await connect_to_user(ctx)
    id = ctx.guild.id
    vc.play(LoopingSource(os.path.join('resources', 'soundclips', 'birthdayloop.wav'), source_factory, id))


@bot.command()
async def birthday(ctx, *args):
    """Wish someone a happy birthday!"""
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    vc = await connect_to_user(ctx)
    vc.play(discord.FFmpegPCMAudio(birthday_wave(
                                     text_to_wav(to_speak, ctx, 'birthday_name'), ctx)))


@bot.command()
async def speakfile(ctx, *args):
    """Send the input text as a sound file from text-to-speech."""
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    await ctx.send(file=discord.File(text_to_wav(to_speak, ctx, 'speakfile', speed=0)))


@bot.command()
async def adderall(ctx, *args):
    """Text-to-speech but f a s t."""
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    vc = await connect_to_user(ctx)
    play_text(vc, to_speak, ctx, 'adderall', _speed=7)


@bot.command()
async def speak(ctx, *args):
    """Play your input text through text-to-speech."""
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    vc = await connect_to_user(ctx)
    play_text(vc, to_speak, ctx, 'speak')


@bot.command()
async def speakdrunk(ctx, *args):
    """Text-to-speech but more drunk."""
    if not args:
        raise discord.InvalidArgument
    to_speak = ''.join(args)
    vc = await connect_to_user(ctx)
    play_text(vc, to_speak, ctx, 'speakdrunk', _speed=-10)


@bot.command()
async def loopaudio(ctx, *args):
    """Play a sound and loop it forever."""
    vc = await connect_to_user(ctx)
    vc.play(LoopingSource(args, source_factory, ctx.guild.id))


@bot.command()
async def play(ctx, *args):
    """Play a sound."""
    if not args:
        raise JermaException('No sound specified in play command.',
                             'Gamer, you gotta tell me which sound to play.')

    sound = ' '.join(args)
    current_sound = get_sound(sound, ctx.guild)
    if not current_sound:
        raise JermaException('Sound ' + sound + ' not found.',
                             'Hey gamer, that sound doesn\'t exist.')

    vc = await connect_to_user(ctx)
    play_sound_file(current_sound, vc)


@bot.command()
@commands.check(is_major)
async def addsound(ctx):
    """Add a sound to the sounds list."""
    await ctx.send('Alright gamer, send the new sound.')

    def check(message):
        return message.author is ctx.author and has_sound_file(message)

    message = await bot.wait_for('message', timeout=20, check=check)

    await add_sound_to_guild(message.attachments[0], ctx.guild)
    await ctx.send('Sound added, gamer.')


@bot.command()
async def remove(ctx, *args):
    """Remove a sound clip."""
    if not args:
        raise JermaException('No sound specified in play command.',
                             'Gamer, you gotta tell me which sound to remove.')

    sound_name = ' '.join(args)
    sound = get_sound(sound_name, ctx.guild)

    if not sound:
        raise JermaException('Sound ' + sound + ' not found.',
                             'Hey gamer, that sound doesn\'t exist.')

    delete_sound(sound, ctx.guild)
    await ctx.send('The sound has been eliminated, gamer.')


@commands.check(is_major)
@bot.command()
async def rename(ctx, *args):
    """Rename a sound clip."""
    if not args:
        raise JermaException('No sound specified in play command.',
                             'Gamer, this is the usage: `rename <original name>, <new name>`')

    old, new = ' '.join(args).split(', ')

    old_filename = get_sound(old, ctx.guild)
    if old_filename:
        new_filename = old_filename[:33] + new + old_filename[-4:]
        rename_file(old_filename, new_filename)


@bot.command()
async def stop(ctx):
    """Stops any currently playing audio."""
    vc = ctx.voice_client
    stop_audio(vc)


@bot.command()
async def volume(ctx, vol: int):
    """Allow the user to change the volume of all played sounds."""
    fvol = vol / 100
    ginfo = guilds[ctx.guild.id]
    old_vol = ginfo.volume
    ginfo.volume = fvol
    if ctx.voice_client and ctx.voice_client.source: # short-circuit statement
        ctx.voice_client.source.volume = fvol

    react = ctx.message.add_reaction
    speakers = ['🔈','🔉','🔊']
    await react('🔇' if vol is 0 else speakers[min(int(fvol * len(speakers)), 2)])
    await react('⬆' if fvol > old_vol else '⬇')


@bot.event
async def on_ready():
    """Initialize some important data and indicate startup success."""
    global guilds
    await bot.change_presence(activity=get_rand_activity())

    guilds = dict()
    for guild in bot.guilds:
        guilds[guild.id] = GuildInfo(guild)

    # with open('avatar.png', 'rb') as file:
    #     await bot.user.edit(avatar=file.read()) # move to on_guild_add
    print(f'Logged into {str(len(guilds))} guilds:')
    for guild in list(guilds.values()):
        print(f'\t{guild.name}:{guild.id}')
    print("Let's fucking go, bois.")


@bot.event
async def on_voice_state_update(member, before, after):
    """Play a join noise when a user joins a channel."""
    # member (Member) – The member whose voice states changed.
    # before (VoiceState) – The voice state prior to the changes.
    # after (VoiceState) – The voice state after the changes.
    try:
        old_vc = get_existing_voice_client(member.guild)

        if guilds[member.guild.id].is_snapping:
            return

        if member.id is bot.user.id:
            if old_vc and not after.channel:
                await old_vc.disconnect()
            return

        if after.channel and after.channel is not before.channel: # join sound
            join_sound = get_sound(member.name, member.guild)
            if join_sound:
                vc = await connect_to_channel(member.voice.channel, old_vc)
                await asyncio.sleep(0.1)
                play_sound_file(join_sound, vc)
            return

        if old_vc and len(old_vc.channel.members) <= 1: # leave if server empty
            y = t.YELLOW + Style.BRIGHT
            c = t.CYAN + Style.NORMAL
            print(f'{y}Disconnecting from {c}{old_vc.guild} #{old_vc.channel} {y}because it is empty.')
            await old_vc.disconnect()
            return
    except discord.errors.ClientException as e:
        perish(None)


@bot.event
async def on_command_error(ctx, e):
    """Catch errors and handle them."""
    if type(e) is commands.errors.CommandInvokeError:
        e = e.original
        if type(e) is JermaException:
            print(f'{t.RED}Caught JermaException: ' + str(e))
            await ctx.send(e.message)
        #else:
            #ben = get_ben()
            #mention = ben.mention + ' something went bonkers.'
            #await ctx.send(mention if ben else 'Something went crazy wrong. Sorry gamers.')
    else:
        raise e


class LoopingSource(discord.AudioSource):
    """This class acts the same as a discord.py AudioSource except it will loop
    forever."""
    def __init__(self, param, source_factory, guild_id):
        self.factory = source_factory
        self.param = param
        self.source = source_factory(self.param)
        self.source.volume = guilds[guild_id].volume
        self.guild_id = guild_id

    def read(self):
        self.source.volume = guilds[self.guild_id].volume
        ret = self.source.read()
        if not ret:
            self.source.cleanup()
            self.source = self.factory(self.param)
            self.source.volume = guilds[self.guild_id].volume
            ret = self.source.read()
        return ret


class JermaException(Exception):
    """Use this exception to halt command processing if a different error is found.

    Only use if the original error is gracefully handled and you need to stop
    the rest of the command from processing. E.g. a file is not found or args
    are invalid."""
    def __init__(self, error, msg):
        self.error = error
        self.message = msg
        #super.__init__(error)

    def __str__(self):
        return self.error


if __name__ == '__main__':
    global source_path
    source_path = os.path.dirname(os.path.abspath(__file__)) # /a/b/c/d/e

    logging.basicConfig(level=logging.ERROR)

    file = open('secret.txt')
    secret = file.read()
    file.close()

    try:
        os.makedirs(os.path.join('resources','soundclips','temp'))
    except:
        pass

    bot.run(secret)
