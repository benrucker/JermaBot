from discord.ext import commands
import os
import asyncio
import discord
import time
import random
import fuzzywuzzy as fuzz
from fuzzywuzzy import process
from typing import Optional
import pickle


# will move these up to a broader scope later
YES = ['yes','yeah','yep','yeppers','of course','ye','y','ya','yah', 'yea', 'yush']
NO  = ['no','n','nope','nay','nada', 'nah', 'na']


def setup(bot):
    bot.add_cog(Fun(bot))


def is_whid():
    def predicate(ctx):
        return ctx.guild.id == 173840048343482368
    return commands.check(predicate)


class Fun(commands.Cog):
    """Cog for various silly bot functions."""

    def __init__(self, bot):
        self.bot = bot

    def get_soul_stone_channel(self, ctx):
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

    @commands.command(hidden=True)
    @is_whid()
    async def jermasnapeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee(self, ctx):
        """Snap the user's voice channel."""
        print('jermasnap')
        # if 374095810868019200 not in ctx.author.roles:
        #     print('not a role thing')
        #     return

        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        soul_stone = self.get_soul_stone_channel(ctx)

        users = ctx.author.voice.channel.members
        users.remove(ctx.me)
        snapees = random.sample(users, k=len(users) // 2)

        self.bot.get_guildinfo(ctx.guild.id).is_snapping = True

        sound, delay, length = self.get_snap_sound()
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
            self.bot.get_guildinfo(ctx.guild.id).is_snapping = False
        else:
            vc.play(discord.FFmpegPCMAudio(os.path.join('resources', 'soundclips', 'snaps', 'up in smoke.mp3')))
            await asyncio.sleep(1)
            self.bot.get_guildinfo(ctx.guild.id).is_snapping = False

    def get_quarantine_sound(self):
        sound = os.path.join('resources', 'soundclips', '2319.wav')
        with open(os.path.join('resources', 'soundclips', '2319.txt'), 'r', encoding='utf-8') as file:
            a = file.readline().split(' ')
        return [sound, float(a[0]), float(a[1])]

    @commands.command(aliases=['q'])
    @is_whid()
    async def quarantine(self, ctx, *args):
        """Save'm."""
        if not args:
            raise discord.InvalidArgument

        name = ' '.join(args[0:])

        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        dest_channel = self.get_soul_stone_channel(ctx)

        users = ctx.author.voice.channel.members
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

    def get_smash_sound(self):
        sound = os.path.join('resources', 'soundclips', 'smash_kill.wav')
        with open(os.path.join('resources', 'soundclips', 'smash_kill.txt'), 'r', encoding='utf-8') as file:
            a = file.readline().split(' ')
        return [sound, float(a[0]), float(a[1])]

    @commands.command()
    @is_whid()
    async def fsmash(self, ctx, *args):
        """Killem."""
        if not args:
            raise discord.InvalidArgument

        name = ' '.join(args[0:])

        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        dest_channel = self.get_soul_stone_channel(ctx)

        users = ctx.author.voice.channel.members
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

    @commands.command()
    async def downsmash(self, ctx, *args):
        """Killem even more."""
        if not args:
            raise discord.InvalidArgument()  # malformed statement?

        name = ' '.join(args[0:])

        vc = await self.bot.get_cog('Control').connect_to_user(ctx)

        users = ctx.author.voice.channel.members
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

    async def _add_emoji_and_delete_msg(self, emoji, msg_to_react, msg_to_delete):
        await msg_to_delete.delete(delay=1)
        await msg_to_react.add_reaction(str(emoji))
        await asyncio.sleep(5)
        await msg_to_react.remove_reaction(str(emoji), self.bot.user)

    @commands.command()
    async def e(self, ctx, emoji_name: str):
        """Add the specified emoji to the most recent message sent."""
        msg = (await ctx.channel.history(limit=1, before=ctx.message).flatten())[0]
        # find emoji in server
        emoji = discord.utils.get(ctx.guild.emojis, name=emoji_name)
        # if not found, search all
        if not emoji:
            emoji = discord.utils.get(self.bot.emojis, name=emoji_name)
        await self._add_emoji_and_delete_msg(emoji, msg, ctx.message)

    @commands.command()
    async def drake(self, ctx, *args):
        """Add a drake clapping reaction to the last message sent."""
        if not args:
            msg = (await ctx.channel.history(limit=1, before=ctx.message).flatten())[0]
        else:
            try:
                msg = await ctx.channel.fetch_message(args[0])
            except Exception as e:
                print(e)
                return
        emoji = self.bot.get_emoji(679179726740258826)
        await self._add_emoji_and_delete_msg(emoji, msg, ctx.message)

    @commands.command()
    async def jermalofi(self, ctx):
        """Chill with a sick jam."""
        print('jermalofi')
        vc = await self.bot.get_cog('Control').connect_to_user(ctx)
        id = ctx.guild.id
        vc.play(self.bot.get_cog('SoundPlayer')
                .LoopingSource(
                    os.path.join('resources', 'soundclips', 'birthdayloop.wav'),
                    self.bot.get_cog('SoundPlayer').source_factory,
                    id,
                    self.bot)
                )


    def load_movies(self, guild_id: int):
        path = os.path.join(self.bot.path, 'guilds', str(guild_id), 'movies')
        if not os.path.exists(path):
            return list()
        try:
            with open(path, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            print(e)
            return list()

    def save_movies(self, guild_id, movies):
        path = os.path.join(self.bot.path, 'guilds', str(guild_id), 'movies')
        with open(path, 'wb') as f:
            pickle.dump(movies, f)

    @commands.command()
    async def movie(self, ctx, *, title: Optional[str]):
        """Add a movie to the movie list."""
        # load movie list
        movies = self.load_movies(ctx.guild.id)
        # check for similar movies
        highest = process.extractOne(title, movies)
        if highest and highest[1] > 90:
            await ctx.send(f'I hate to say this but... **{highest[0]}** is already on the list. ' +
                           f'Ya still wanna add **{title}**? **(f{random.choice(YES)}/{random.choice(NO)})**')

            def check(message):
                return message.author is ctx.author and message.content.lower().strip() in YES + NO

            replace_msg = await self.bot.wait_for('message', timeout=20, check=check)
            if replace_msg.content.lower().strip() in NO:
                await ctx.send('You got it, boss.')
                return

        # write to movie list
        movies.append(title)
        # save movie list
        self.save_movies(ctx.guild.id, movies)
        await ctx.send('Movie added. `$removie` to delete it or `$movies` to see the list.')

    @commands.command()
    async def movies(self, ctx):
        movies = self.load_movies(ctx.guild.id)
        if len(movies) == 0:
            await ctx.send('Your movie queue is empty. Hear that? EMPTY!')
        else:
            await ctx.send('\n'.join(self.load_movies(ctx.guild.id)))

    @commands.command()
    async def removie(self, ctx, *, title):
        movies = self.load_movies(ctx.guild.id)
        highest = process.extractOne(title, movies)

        if not highest:
            await ctx.send('This is a bit awkward but... I don\'t know what movie you mean.')
            return

        await ctx.send(f'Just\'a confirm, ya wanna remove **{highest[0]}**? **({random.choice(YES)}/{random.choice(NO)})**')

        def check(message):
            return message.author is ctx.author and message.content.lower().strip() in YES + NO

        replace_msg = await self.bot.wait_for('message', timeout=20, check=check)
        if replace_msg.content.lower().strip() in NO:
            await ctx.send('Make up your mind next time, boss.')
            return
        
        movies.remove(highest[0])
        await ctx.send('Movie remov...ied.')
        self.save_movies(ctx.guild.id, movies)
