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
        vc.play(discord.FFmpegPCMAudio('soundclips\\moonlight.wav'))
    else:
        vc.play(discord.FFmpegPCMAudio('soundclips\\snaps\\up in smoke.mp3'))


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

    vc = await connect_to_user(ctx)
    soul_stone = get_soul_stone_channel(ctx)

    sound, delay, length = get_snap_sound()
    vc.play(discord.FFmpegPCMAudio(sound))
    time.sleep(delay)

    for snapee in snapees:
        await snapee.move_to(soul_stone)

    time.sleep(length - delay)
    do_moonlight = random.random() < 0.25
    if do_moonlight:
        await ctx.guild.voice_client.move_to(soul_stone)
        vc.play(discord.FFmpegPCMAudio('soundclips\\moonlight.wav'))
    else:
        vc.play(discord.FFmpegPCMAudio('soundclips\\snaps\\up in smoke.mp3'))


@bot.command()
async def jermalofi(ctx):
    print('jermalofi')
    vc = await connect_to_user(ctx)
    vc.play(LoopingSource('soundclips\\birthdayloop.wav', loop_factory))


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
    vc.play(discord.FFmpegPCMAudio(text_to_wav(to_speak, ctx, 'adderall', speed=7)))


@bot.command()
async def speak(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    vc = await connect_to_user(ctx)
    vc.play(discord.FFmpegPCMAudio(text_to_wav(to_speak, ctx, 'speak')))


@bot.command()
async def speakdrunk(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ''.join(args)
    vc = await connect_to_user(ctx)
    vc.play(discord.FFmpegPCMAudio(text_to_wav(to_speak, ctx, 'speakdrunk', speed=-10)))


@bot.command()
async def loopaudio(ctx, *args):
    vc = await connect_to_user(ctx)
    vc.play(LoopingSource(args, loop_factory))


@bot.event
async def on_testevent(ctx):
    print('testevent event triggered')
    await ctx.send('testevent triggered')


@bot.event
async def on_keyword_heard(ctx):
    await ctx.send('keyword detected')


@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(name='rats lofi.',
                                                        type=discord.ActivityType(2)))
    print("Let's fucking go, bois.")


def loop_factory(filename):
    return discord.FFmpegPCMAudio(filename)


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
    with open('soundclips\\snaps\\sounds.txt', 'r', encoding='utf-8') as file:
        for sound in file.read().split('\n'):
            sounds.append(sound.split(' '))
    print(sounds)
    choice = random.choice(sounds)
    choice[0] = 'soundclips\\snaps\\' + choice[0]
    choice[1] = float(choice[1])
    choice[2] = float(choice[2])
    return choice


def text_to_wav(text, ctx, label, speed=0):
    soundclip = generate_id_path(label, ctx)
    file = source_path + soundclip
    subprocess.call([tts_path, '-r', str(speed), '-o', file, text])
    return soundclip


def generate_id_path(label, ctx):
    return 'soundclips\\temp\\' + label + str(ctx.guild.id) + '.wav'


async def connect_to_user(ctx):
    try:
        vc = ctx.guild.voice_client
        user_channel = ctx.author.voice.channel
        if not vc:
            vc = await ctx.author.voice.channel.connect()
        elif not vc.channel == user_channel:
            await ctx.guild.voice_client.move_to(user_channel)
        return vc
    except:
        #print('Caught ' + type(e) + ':', e.message)
        #print('User was probably not in a channel or something.')
        await ctx.send("Hey gamer, you're not in a voice channel. Totally uncool.")
        raise JermaException("User was not in a voice channel or something.")



def birthday_wave(name, ctx):
    song = AudioSegment.from_wav('soundclips\\blank_birthday.wav')
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
    pass


if __name__ == '__main__':
    global source_path
    source_path = os.path.dirname(os.path.abspath(__file__)) + '\\' # /a/b/c/d/e
    file = open('secret.txt')
    secret = file.read()
    file.close()
    bot.run(secret)
