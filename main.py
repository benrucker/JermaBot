import sys
import time
import discord
import random
import os
import colorama
from colorama import Fore as t
from glob import glob
from discord.ext import commands
import subprocess
from pydub import AudioSegment


# TODO:
#  - say something upon join

colorama.init(autoreset=True)

tts_path = 'voice.exe'
#source_path = None

prefix = '$'
bot = commands.Bot(prefix)


@bot.event
async def on_message(message):
    if message.content.startswith('$$'):
        return # protection against hackerbot commands

    if message.content.startswith(prefix):
        print(f'{message.author.name} - {message.guild} #{message.channel}: {t.CYAN}{message.content}')
    elif message.author == message.guild.me:
        print(f'{message.author.name} - {message.guild} #{message.channel}: {message.content}')
    try:
        await bot.process_commands(message)
    except JermaException as e:
        print('Caught JermaException: ' + str(e))
    #except Exception as e:
    #    print(e)


@bot.command()
async def join(ctx):
    vc = await connect_to_user(ctx)


@bot.command()
async def leave(ctx):
    await ctx.guild.voice_client.disconnect()


@bot.command()
async def perish(ctx):
    await bot.close()


@bot.command()
async def testsnap(ctx):
    print('testjermasnap')
    if not is_major(ctx.author):
        return

    vc = await connect_to_user(ctx)
    soul_stone = get_soul_stone_channel(ctx)

    users = ctx.author.voice.channel.members
    users.remove(ctx.me)
    snapees = random.sample(users, k=len(users) // 2)

    vc = await connect_to_user(ctx)
    soul_stone = get_soul_stone_channel(ctx)

    sound, delay, length = get_snap_sound()
    vc.play(discord.FFmpegPCMAudio(sound))
    time.sleep(delay)

    #for snapee in snapees:
    #    await snapee.move_to(soul_stone)

    time.sleep(length - delay)
    do_moonlight = random.random() < 0.25
    if do_moonlight:
        ctx.guild.voice_client.move_to(soul_stone)
        vc.play(discord.FFmpegPCMAudio(os.path.join('soundclips', 'moonlight.wav')))
    else:
        vc.play(discord.FFmpegPCMAudio(os.path.join('soundclips', 'snaps', 'up in smoke.mp3')))


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
    else:
        vc.play(discord.FFmpegPCMAudio(os.path.join('soundclips', 'snaps', 'up in smoke.mp3')))


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
async def play(ctx, sound):
    try:
        vc = await connect_to_user(ctx)
        current_sound = get_sound(sound)
        if not current_sound:
            raise IOError('Sound ' + sound + ' not found.')
        print(current_sound)
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(current_sound))
        stop_audio(vc)
        vc.play(source)
        print('Playing', current_sound, '| at volume:', source.volume, '| In:', ctx.guild)
    except IOError as e:
        print(e)
        await ctx.send('That sound doesn\'t exist, gamer. Try being a pro like me next time.')
        raise JermaException('Invalid sound name.')


@bot.command()
async def stop(ctx):
    """Stops any currently playing audio."""
    vc = ctx.voice_client
    stop_audio(vc)


@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(name='my heart.',
                                                        type=discord.ActivityType(2)))
    # with open('avatar.png', 'rb') as file:
    #     await bot.user.edit(avatar=file.read())
    print("Let's fucking go, bois.")


def play_text(vc, to_speak, ctx, label, _speed=0):
    # vc.play(discord.FFmpegPCMAudio(text_to_wav(to_speak, ctx, 'speakdrunk', speed=-10)))
    sound_file = text_to_wav(to_speak, ctx, label, speed=_speed)
    vc.play(discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(sound_file)))

def stop_audio(vc):
    if vc.is_playing():
        vc.stop()
        vc.send_audio_packet(b'0')


def loop_factory(filename):
    return discord.FFmpegPCMAudio(filename)


def make_sounds_dict():
    sounds = {}
    sound_folder = os.path.join(source_path, 'sounds')
    print('Finding sounds in:', sound_folder)
    for filepath in glob(os.path.join(sound_folder, '*')): # find all files in folder w/ wildcard
        filename = os.path.basename(filepath)
        extension = filename.split('.')[1]
        if extension not in ['mp3', 'wav']:
            continue
        sounds[filename.split('.')[0]] = filename
    return sounds


def get_sound(sound):
    sounds = make_sounds_dict()
    try:
        return os.path.join('sounds', sounds[sound])
    except KeyError as e:
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
        if not vc:
            vc = await ctx.author.voice.channel.connect()
        elif not vc.channel == user_channel:
            await ctx.voice_client.move_to(user_channel)
        return vc
    except:
        await ctx.send("Hey gamer, you're not in a voice channel. Totally uncool.")
        raise JermaException("User was not in a voice channel or something.")


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
    #print(make_sounds_dict())
    file = open('secret.txt')
    secret = file.read()
    file.close()
    bot.run(secret)
