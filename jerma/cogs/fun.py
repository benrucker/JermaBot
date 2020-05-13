from discord.ext import commands
import os
import asyncio
import discord
import time
import random


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

    @commands.command()
    @is_whid()
    async def jermasnap(self, ctx):
        """Snap the user's voice channel."""
        print('jermasnap')
        if 374095810868019200 not in ctx.author.roles:
            return

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

    @commands.command()
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

    @commands.command()
    async def drake(self, ctx, *args):
        """Add a drake clapping reaction to the last message sent."""
        if not args:
            return
        try:
            msg = await ctx.channel.fetch_message(args[0])
        except Exception as e:
            print(e.with_traceback())
        await ctx.message.delete(delay=1)
        await msg.add_reaction('drake:679179726740258826')
        await asyncio.sleep(5)
        await msg.remove_reaction('drake:679179726740258826', self.bot.user)

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
                    id)
                )
