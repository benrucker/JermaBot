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

from help import helpEmbed, soundEmbed, make_sounds_dict, get_rand_activity


colorama.init(autoreset=True) # set up colored console out

tts_path = 'voice.exe'

prefix = '$'
bot = commands.Bot(prefix)

@bot.event
async def on_message(message):
    if message.content.startswith('$$'):
        return # protection against hackerbot commands

    if message.content.startswith(prefix):
        print(f'{message.author.name} - {message.guild} #{message.channel}: {t.CYAN}{message.content}')
    elif message.author == bot.user:
        print(f'{message.author.name} - {message.guild} #{message.channel}: {message.content}')
    try:
        await bot.process_commands(message)
    except JermaException as e:
        print(f'{t.RED}Caught JermaException: ' + str(e))
    #except AttributeError as _:
    #    pass # ignore embed-only messages
    #except Exception as e:
    #    print(e)


@bot.command()
async def join(ctx):
    _ = await connect_to_user(ctx)


@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        loc = os.path.join('soundclips', 'leave')
        sounds = make_sounds_dict(loc)
        soundname = random.choice(list(sounds.values()))
        sound = os.path.join(loc, soundname)
        play_sound_file(sound, ctx.voice_client)
        time.sleep(1)
        await ctx.guild.voice_client.disconnect()


@bot.command()
async def perish(ctx):
    await bot.close()


@bot.command()
async def jermahelp(ctx):
    help_files = [discord.File("avatar.png", filename="avatar.png"),
                  discord.File("thumbnail.png", filename="thumbnail.png")]
    await ctx.author.send(files=help_files, embed=helpEmbed)
    await ctx.message.add_reaction("✉")


@bot.command(name='list')
async def _list(ctx):
    help_files = [discord.File("avatar.png", filename="avatar.png"),
                  discord.File("thumbnail.png", filename="thumbnail.png")]
    await ctx.author.send(files=help_files, embed=soundEmbed)
    await ctx.message.add_reaction("✉")


@bot.command()
async def jermasnap(ctx):
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
        vc.play(discord.FFmpegPCMAudio(os.path.join('soundclips', 'snaps', 'up in smoke.mp3')))
        await asyncio.sleep(1)
        guilds[ctx.guild.id].is_snapping = False


@bot.command()
async def jermalofi(ctx):
    print('jermalofi')
    vc = await connect_to_user(ctx)
    vc.play(LoopingSource(os.path.join('soundclips', 'birthdayloop.wav'), loop_factory))


@bot.command()
async def birthday(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    vc = await connect_to_user(ctx)
    vc.play(discord.FFmpegPCMAudio(birthday_wave(
                                     text_to_wav(to_speak, ctx, 'birthday_name'), ctx)))


@bot.command()
async def speakfile(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    await ctx.send(file=discord.File(text_to_wav(to_speak, ctx, 'speakfile', speed=0)))


@bot.command()
async def adderall(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    vc = await connect_to_user(ctx)
    play_text(vc, to_speak, ctx, 'adderall', speed=7)


@bot.command()
async def speak(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    vc = await connect_to_user(ctx)
    play_text(vc, to_speak, ctx, 'speak')


@bot.command()
async def speakdrunk(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ''.join(args)
    vc = await connect_to_user(ctx)
    play_text(vc, to_speak, ctx, 'speakdrunk', speed=-10)


@bot.command()
async def loopaudio(ctx, *args):
    vc = await connect_to_user(ctx)
    vc.play(LoopingSource(args, loop_factory))


@bot.command()
async def play(ctx, *args):
    if not args:
        raise JermaException('No sound specified in play command.')
    sound = ' '.join(args)
    current_sound = get_sound(sound)
    if not current_sound:
        raise JermaException('Sound ' + sound + ' not found.')

    vc = await connect_to_user(ctx)
    play_sound_file(current_sound, vc)


@bot.command()
async def stop(ctx):
    """Stops any currently playing audio."""
    vc = ctx.voice_client
    stop_audio(vc)


@bot.event
async def on_ready():
    global guilds
    #await bot.change_presence(activity=discord.Activity(name='my heart.',
    #                                                    type=discord.ActivityType(2)))
    await bot.change_presence(activity=get_rand_activity())

    guilds = dict()
    for guild in bot.guilds:
        guilds[guild.id] = GuildInfo(guild.id)

    # with open('avatar.png', 'rb') as file:
    #     await bot.user.edit(avatar=file.read())
    print("Let's fucking go, bois.")


@bot.event
async def on_voice_state_update(member, before, after):
    """Play a join noise when a user joins a channel."""
    # member (Member) – The member whose voice states changed.
    # before (VoiceState) – The voice state prior to the changes.
    # after (VoiceState) – The voice state after the changes.
    if member.id is bot.user.id:
        return

    if guilds[member.guild.id].is_snapping:
        return

    old_vc = get_existing_voice_client(member.guild)

    if after.channel and after.channel is not before.channel: # join sound
        join_sound = get_sound(member.name)
        if join_sound:
            print('Playing join sound for', member.name, 'in', member.guild)
            vc = await connect_to_channel(member.voice.channel, old_vc)
            play_sound_file(join_sound, vc)
    elif old_vc and len(old_vc.channel.members): # leave if server empty
        y = t.YELLOW + Style.BRIGHT
        c = t.CYAN
        print(f'{y}Disconnecting from {c}{old_vc.guild} #{old_vc.channel} {y}because it is empty.')
        await old_vc.disconnect()


def play_sound_file(sound, vc):
    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(sound))
    stop_audio(vc)
    vc.play(source)
    print(f'Playing {sound} | at volume: {source.volume} | in: {vc.guild}')


def play_text(vc, to_speak, ctx, label, _speed=0):
    sound_file = text_to_wav(to_speak, ctx, label, speed=_speed)
    vc.play(discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(sound_file)))


def stop_audio(vc):
    if vc.is_playing():
        vc.stop()
        vc.play(discord.FFmpegPCMAudio('soundclips\\silence.wav'))
        time.sleep(.065)
        #asyncio.sleep(.051)
        #vc.send_audio_packet(1024*b'\x00')


def loop_factory(filename):
    return discord.FFmpegPCMAudio(filename)


def get_sound(sound):
    sounds = make_sounds_dict('sounds')
    try:
        return os.path.join('sounds', sounds[sound.lower()])
    except KeyError:
        return None


def get_soul_stone_channel(ctx):
    for channel in ctx.guild.voice_channels:
        if channel.id == 343939767068655616:
            return channel
    raise BaseException('channel not found')


def is_major(member):
    for role in member.roles:
        if role.id == 374095810868019200:
            return True
    return False


def get_snap_sound():
    sounds = []
    snaps_folder = os.path.join('soundclips', 'snaps')
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
    return os.path.join('soundclips', 'temp', label + str(ctx.guild.id) + '.wav')


async def connect_to_user(ctx):
    try:
        vc = ctx.voice_client
        user_channel = ctx.author.voice.channel
        return await connect_to_channel(user_channel, vc)
        # if not vc:
        #     vc = await user_channel.connect()
        # elif not vc.channel == user_channel:
        #     await vc.move_to(user_channel)
        # return vc
    except:
        await ctx.send("Hey gamer, you're not in a voice channel. Totally uncool.")
        raise JermaException("User was not in a voice channel or something.")


async def connect_to_channel(channel, vc=None):
    if not channel:
        raise AttributeError('channel cannot be None.')

    if not vc:
        vc = await channel.connect()

    if vc.channel is not channel:
        await vc.move_to(channel)

    return vc


def get_existing_voice_client(guild):
    for vc in bot.voice_clients:
        if vc.guild is guild:
            return vc


def birthday_wave(name, ctx):
    song = AudioSegment.from_wav(os.path.join('soundclips', 'blank_birthday.wav'))
    name = AudioSegment.from_wav(name)
    insert_times = [7.95 * 1000, 12.1 * 1000]

    for insert_time in insert_times:
    	song = song.overlay(name, position=insert_time)

    outpath = generate_id_path('birthday_name', ctx)
    song.export(outpath)
    return outpath


class LoopingSource(discord.AudioSource):
    def __init__(self, param, source_factory):
        self.factory = source_factory
        self.param = param
        self.source = source_factory(self.param)

    def read(self):
        ret = self.source.read()
        if not ret:
            self.source.cleanup()
            self.source = self.factory(self.param)
            ret = self.source.read()
        return ret


class JermaException(BaseException):
    """Use this exception to halt command processing if a different error is found.

    Only use if the original error is gracefully handled and you need to stop
    the rest of the command from processing. E.g. a file is not found."""
    pass


if __name__ == '__main__':
    global source_path
    source_path = os.path.dirname(os.path.abspath(__file__)) # /a/b/c/d/e

    logging.basicConfig(level=logging.ERROR)

    file = open('secret.txt')
    secret = file.read()
    file.close()

    bot.run(secret)
