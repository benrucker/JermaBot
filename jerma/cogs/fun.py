import asyncio
import os
import pickle
import random
import time
from typing import Optional
from colorama import Fore as t

import discord
from discord import Message, app_commands, VoiceClient
from discord.ext import commands
from discord.ext.commands import Context
from fuzzywuzzy import process

from cogs.control import Control, JoinFailedError
from cogs.sound_player import SoundPlayer
from jermabot import JermaBot

# will move these up to a broader scope later
YES = ['yes', 'yeah', 'yep', 'yeppers', 'of course',
       'ye', 'y', 'ya', 'yah', 'yea', 'yush']
NO = ['no', 'n', 'nope', 'nay', 'nada', 'nah', 'na']


class NoClassUnpickler(pickle.Unpickler):
    """Unpickles builtin data types only. Refuses all class/callable lookups."""

    def find_class(self, module, name):
        raise pickle.UnpicklingError(
            f'refusing to load {module}.{name} from movie file'
        )


def safe_load(f):
    return NoClassUnpickler(f).load()


async def setup(bot):
    await bot.add_cog(Fun(bot))


def is_whid():
    def predicate(ctx: Context):
        return ctx.guild is not None and ctx.guild.id == 173840048343482368
    return commands.check(predicate)


class Fun(commands.Cog):
    """Cog for various silly bot functions."""

    def __init__(self, bot: JermaBot):
        self.bot: JermaBot = bot

    @commands.hybrid_command()
    @app_commands.default_permissions(use_application_commands=True)
    async def jermalofi(self, ctx: Context):
        """Chill with a sick jam."""
        print('jermalofi')
        if ctx.guild is None:
            print(f'{t.RED}Command was called in a non-guild context')
            raise RuntimeError("You can't use this command outside of a guild.")
        member = ctx.guild.get_member(ctx.author.id)
        if member is None:
            print(f'{t.RED}Command was called by a member that is not in the guild')
            raise RuntimeError("Seems like you're not in this guild.")
        if not member.voice:
            print(f'{t.RED}Command was called by a member that is not in a voice channel')
            raise JoinFailedError()

        control: Control = self.bot.get_cog('Control')
        vc = await control.connect_to_user(member.voice, ctx.guild)

        sound_player: SoundPlayer = self.bot.get_cog('SoundPlayer')
        vc.play(
            sound_player
                .LoopingSource(
                    os.path.join('resources', 'soundclips',
                                 'birthdayloop.wav'),
                    sound_player.source_factory,
                    ctx.guild.id,
                    self.bot
            )
        )

    @commands.command(aliases=['q'])
    @is_whid()
    async def quarantine(self, ctx: Context, *args):
        """(Deprecated) Save'm."""
        if not args:
            raise ValueError("Missing target in quarantine command")
        
        if ctx.guild is None:
            print(f'{t.RED}Command was called in a non-guild context')
            raise RuntimeError("You can't use this command outside of a guild.")
        member = ctx.guild.get_member(ctx.author.id)
        if member is None:
            print(f'{t.RED}Command was called by a member that is not in the guild')
            raise RuntimeError("Seems like you're not in this guild.")
        if not member.voice or not member.voice.channel:
            print(f'{t.RED}Command was called by a member that is not in a voice channel')
            raise JoinFailedError()

        name = ' '.join(args[0:])

        vc = await self.bot.get_cog('Control').connect_to_user(member.voice, ctx.guild)
        dest_channel = self.get_soul_stone_channel(ctx)

        users = member.voice.channel.members
        user = None
        for u in users:
            if name.lower() in [u.name.lower(), u.display_name.lower()]:
                user = u

        if not user:
            return

        sound, delay, length = self.get_quarantine_sound()

        self.bot.get_guildinfo(ctx.guild.id).is_snapping = True
        self.bot.get_cog('SoundPlayer').play_sound_file(sound, vc)
        time.sleep(delay)
        await user.move_to(dest_channel)
        time.sleep(length - delay)
        self.bot.get_guildinfo(ctx.guild.id).is_snapping = False

    @commands.command()
    @is_whid()
    async def fsmash(self, ctx: Context, *args):
        """(Deprecated) Killem."""
        if not args:
            raise ValueError("Missing target in fmash command")

        if ctx.guild is None:
            print(f'{t.RED}Command was called in a non-guild context')
            raise RuntimeError("You can't use this command outside of a guild.")
        member = ctx.guild.get_member(ctx.author.id)
        if member is None:
            print(f'{t.RED}Command was called by a member that is not in the guild')
            raise RuntimeError("Seems like you're not in this guild.")
        if not member.voice or not member.voice.channel:
            print(f'{t.RED}Command was called by a member that is not in a voice channel')
            raise JoinFailedError()

        name = ' '.join(args[0:])

        vc = await self.bot.get_cog('Control').connect_to_user(member.voice, ctx.guild)
        dest_channel = self.get_soul_stone_channel(ctx)

        users = member.voice.channel.members
        user = None
        for u in users:
            if name.lower() in [u.name.lower(), u.display_name.lower()]:
                user = u

        if not user:
            return

        sound, delay, length = self.get_smash_sound()

        self.bot.get_guildinfo(ctx.guild.id).is_snapping = True
        self.bot.get_cog('SoundPlayer').play_sound_file(sound, vc)
        time.sleep(delay)
        await user.move_to(dest_channel)
        time.sleep(length - delay)
        self.bot.get_guildinfo(ctx.guild.id).is_snapping = False

    @commands.hybrid_command()
    @app_commands.default_permissions(move_members=True, mute_members=True)
    async def downsmash(self, ctx: Context, *, name):
        """Killem even more."""
        if not name:
            raise ValueError("Missing target in fmash command")

        if ctx.guild is None:
            print(f'{t.RED}Command was called in a non-guild context')
            raise RuntimeError("You can't use this command outside of a guild.")
        member = ctx.guild.get_member(ctx.author.id)
        if member is None:
            print(f'{t.RED}Command was called by a member that is not in the guild')
            raise RuntimeError("Seems like you're not in this guild.")
        if not member.voice or not member.voice.channel:
            print(f'{t.RED}Command was called by a member that is not in a voice channel')
            raise JoinFailedError()

        vc = await self.bot.get_cog('Control').connect_to_user(member.voice, ctx.guild)

        users = member.voice.channel.members
        user = None
        for u in users:
            if name.lower() in [u.name.lower(), u.display_name.lower()]:
                user = u

        if not user:
            return

        sound, delay, length = self.get_smash_sound()

        self.bot.get_guildinfo(ctx.guild.id).is_snapping = True
        self.bot.get_cog('SoundPlayer').play_sound_file(sound, vc)
        time.sleep(delay)
        await user.move_to(None)
        time.sleep(length - delay)
        self.bot.get_guildinfo(ctx.guild.id).is_snapping = False

    def get_soul_stone_channel(self, ctx: Context):
        if not ctx.guild:
            raise Exception('No guild')

        for channel in ctx.guild.voice_channels:
            if channel.id == 343939767068655616:
                return channel

        raise Exception('channel not found')

    def get_snap_sound(self):
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

    def get_quarantine_sound(self):
        return self.get_sound('2319')

    def get_smash_sound(self):
        return self.get_sound('smash_kill')

    def get_sound(self, sound_name: str):
        sound = os.path.join('resources', 'soundclips', f'{sound_name}.wav')
        with open(os.path.join('resources', 'soundclips', f'{sound_name}.txt'), 'r', encoding='utf-8') as file:
            delay, length = file.readline().split(' ')
        return [sound, float(delay), float(length)]

    @commands.hybrid_command()
    @app_commands.default_permissions(use_application_commands=True)
    @app_commands.describe(title="The title of the movie")
    async def movie(self, ctx: Context, *, title: str):
        """Add a movie to the movie list."""
        if ctx.guild is None:
            print(f'{t.RED}Command was called in a non-guild context')
            raise RuntimeError("You can't use this command outside of a guild.")

        movies = self.load_movies(ctx.guild.id)
        highest = process.extractOne(title, movies)
        if highest and highest[1] > 90:
            await ctx.send(f'So, uh, **{highest[0]}** is already on the list. ' +
                           f'Ya still wanna add **{title}**? **({random.choice(YES)}/{random.choice(NO)})**')

            def check(message: Message):
                return message.author == ctx.author and message.content.lower().strip() in YES + NO

            replace_msg: Message = await self.bot.wait_for('message', timeout=20, check=check)
            if replace_msg.content.lower().strip() in NO:
                await ctx.send('You got it, boss.')
                return

        movies.append(title)
        self.save_movies(ctx.guild.id, movies)
        await ctx.send('Movie added. `/removie` to delete it or `/movies` to see the list.')

    @commands.hybrid_command()
    @app_commands.default_permissions(use_application_commands=True)
    async def movies(self, ctx: Context):
        """Look at the movie list."""
        if ctx.guild is None:
            print(f'{t.RED}Command was called in a non-guild context')
            raise RuntimeError("You can't use this command outside of a guild.")

        movies = self.load_movies(ctx.guild.id)
        if len(movies) == 0:
            await ctx.send('Your movie queue is empty. Hear that? EMPTY!')
        else:
            await ctx.send('\n'.join(sorted(self.load_movies(ctx.guild.id), key=str.lower)))

    @commands.hybrid_command()
    @app_commands.default_permissions(use_application_commands=True)
    @app_commands.describe(title="The title of the movie")
    async def removie(self, ctx: Context, *, title: str):
        """Removie a movie from the movie list."""
        if ctx.guild is None:
            print(f'{t.RED}Command was called in a non-guild context')
            raise RuntimeError("You can't use this command outside of a guild.")

        movies = self.load_movies(ctx.guild.id)
        highest = process.extractOne(title, movies)

        if not highest:
            await ctx.send('This is a bit awkward but... I don\'t know what movie you mean.')
            return

        await ctx.send(f'Just\'a confirm, ya wanna remove **{highest[0]}**? **({random.choice(YES)}/{random.choice(NO)})**')

        def check(message: Message):
            return message.author == ctx.author and message.content.lower().strip() in YES + NO

        replace_msg: Message = await self.bot.wait_for('message', timeout=20, check=check)
        if replace_msg.content.lower().strip() in NO:
            await ctx.send('Make up your mind next time, boss.')
            return

        movies.remove(highest[0])
        await ctx.send('Movie remov...ied.')
        self.save_movies(ctx.guild.id, movies)

    def load_movies(self, guild_id: int):
        path = os.path.join(self.bot.path, 'guilds', str(guild_id), 'movies')
        if not os.path.exists(path):
            return list()
        try:
            with open(path, 'rb') as f:
                return safe_load(f)
        except Exception as e:
            print(f'{t.RED}caught exception while loading movie list: {e!r}')
            return list()

    def save_movies(self, guild_id, movies):
        path = os.path.join(self.bot.path, 'guilds', str(guild_id), 'movies')
        with open(path, 'wb') as f:
            pickle.dump(movies, f)
