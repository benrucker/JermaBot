import os
import random
import time
from typing import Optional

from cogs.control import Control, JoinFailedError
from cogs.sound_player import SoundPlayer
from colorama import Fore as t
from colorama import Style
from discord import Guild, Interaction, Member, Message, Permissions, VoiceClient, VoiceState, app_commands
from discord.embeds import Embed
from discord.ext import commands
from discord.ext.commands import Context
from guild_info import GuildInfo

from jermabot import JermaBot
from .utils.autocomplete import autocomplete

# will move these up to a broader scope later
YES = ['yes', 'yeah', 'yep', 'yeppers', 'of course', 'ye', 'y', 'ya', 'yah']
NO = ['no', 'n', 'nope', 'start over', 'nada', 'nah']

y = t.YELLOW + Style.BRIGHT
c = t.CYAN + Style.NORMAL
b = Style.BRIGHT
n = Style.NORMAL

regular_nickname = 'JermaBot'
snoozed_nickname = 'JermaSnore'

ADMIN_GUILDS = [
    571004411137097731,
    173840048343482368,
]


async def manage_sounds_check(ctx: Context):
    p: Permissions = ctx.channel.permissions_for(ctx.author)
    return p.kick_members or \
        p.ban_members or \
        p.administrator or \
        p.manage_guild or \
        p.move_members or \
        p.manage_nicknames or \
        p.manage_roles or \
        p.deafen_members or \
        p.mute_members


async def setup(bot: JermaBot):
    await bot.add_cog(GuildSounds(bot))


class GuildSoundsError(commands.CommandError):
    def __init__(self, error, msg):
        self.error = error
        self.msg = msg


class GuildSounds(commands.Cog):
    """Cog for maintaining guild-specific sound functionality."""

    def __init__(self, bot: JermaBot):
        self.bot: JermaBot = bot

    async def cog_command_error(self, ctx: Context, error):
        if isinstance(error, GuildSoundsError):
            print(error.error)
            await ctx.send(error.msg)
        elif isinstance(error, JoinFailedError):
            await ctx.send(error)
        else:
            raise error

    @commands.hybrid_command()
    @app_commands.describe(sound="The sound to play")
    async def play(self, ctx: Context, *, sound: str):
        """Play a sound."""
        if not sound:
            raise GuildSoundsError('No sound specified in play command.',
                                   'Gamer, you gotta tell me which sound to play.')
        sound_name = sound.lower()
        sound_filepath = self.get_sound_filepath(sound_name, ctx.guild)
        if not sound_filepath:
            raise GuildSoundsError('Sound ' + sound + ' not found.',
                                   'Hey gamer, that sound doesn\'t exist.')

        control: Control = self.bot.get_cog('Control')
        player: SoundPlayer = self.bot.get_cog('SoundPlayer')
        vc = await control.connect_to_user(ctx.author.voice, ctx.guild)
        player.play_sound_file(sound_filepath, vc)

        if ctx.interaction:
            await ctx.send(f"Playing **{sound_name}**")

    @play.autocomplete('sound')
    async def play_sound_autocomplete(self, intr: Interaction, query: str) -> list[app_commands.Choice[str]]:
        return self.sound_autocomplete(intr, query)

    def sound_autocomplete(self, intr: Interaction, query: str):
        sounds = self.bot.get_guildinfo(intr.guild.id).sounds.keys()
        return autocomplete(query.lower(), sounds)

    def get_sound_filepath(self, sound_name: str, guild: Guild):
        ginfo: GuildInfo = self.bot.get_guildinfo(guild.id)
        sounds = ginfo.sounds
        sound_folder = ginfo.sound_folder
        try:
            sound_filename = sounds[sound_name.lower()]
            return os.path.join(sound_folder, sound_filename)
        except KeyError:
            return None

    @commands.hybrid_command()
    @app_commands.default_permissions(use_application_commands=True)
    async def random(self, ctx: Context):
        """Play a random sound!"""
        sound, sound_name = self.get_random_sound(ctx.guild)
        if not sound:
            raise GuildSoundsError('Guild has no sounds.',
                                   'Sorry gamer, but you need to add some sounds for me to play!')

        control: Control = self.bot.get_cog('Control')
        player: SoundPlayer = self.bot.get_cog('SoundPlayer')
        vc = await control.connect_to_user(ctx.author.voice, ctx.guild)
        player.play_sound_file(sound, vc)
        
        await ctx.send(f"Playing **{sound_name}**")

    def get_random_sound(self, guild: Guild):
        ginfo: GuildInfo = self.bot.get_guildinfo(guild.id)
        sound_name, sound_filename = random.choice(list(ginfo.sounds.items()))
        return os.path.join(ginfo.sound_folder, sound_filename), sound_name

    @commands.hybrid_command(name='list', aliases=['sounds'])
    @app_commands.default_permissions(use_application_commands=True)
    async def _list(self, ctx: Context):
        """Send the user a list of sounds that can be played."""
        ginfo: GuildInfo = self.bot.get_guildinfo(ctx.guild.id)
        await ctx.author.send(embed=self.make_list_embed(ginfo))
        if ctx.interaction:
            await ctx.send("List sent!", ephemeral=True)
        else:
            await ctx.message.add_reaction("✉")

    @commands.hybrid_command(aliases=['add'])
    @commands.check(manage_sounds_check)
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(sound_name="If present, the new sound will have this name")
    async def addsound(self, ctx: Context, *, sound_name: Optional[str]):
        """Add a sound to the sounds list. Requires elevated server perms."""

        attachment = ctx.message.attachments[0] if len(
            ctx.message.attachments
        ) else None

        if not attachment:
            # wait for sound file
            await ctx.send('Alright gamer, send the new sound.')

            def check(message: Message):
                return message.author == ctx.author and self.has_sound_file(message)

            message: Message = await self.bot.wait_for('message', timeout=20, check=check)
            attachment = message.attachments[0]

        # determine name of sound
        sound_name = sound_name.lower() if sound_name else None
        if sound_name:
            if sound_name.endswith(('.mp3', '.wav')):
                filename = sound_name
            else:
                filename = sound_name + '.' + \
                    attachment.filename.split('.')[-1]
        else:
            filename = attachment.filename
        filename = filename.lower()

        # remove old sound if there
        name = filename.rsplit('.', 1)[0].lower()
        existing = self.get_sound_filepath(name, ctx.guild)
        if existing:
            await ctx.send(f'There\'s already a sound called _{name}_, bucko. Sure you want to replace it? (yeah/nah)')

            def check2(message):
                return message.author is ctx.author

            replace_msg: Message = await self.bot.wait_for('message', timeout=20, check=check2)
            if replace_msg.content.lower().strip() in YES:
                await ctx.send('Expunging the old sound...')
                self.delete_sound(os.path.split(existing)[1], ctx.guild)
            else:
                await ctx.send('Yeah, I like the old one better too.')
                return

        await self.add_sound_to_guild(attachment, ctx.guild, filename=filename)
        await ctx.send('Sound added, gamer.')

    @commands.hybrid_command(aliases=['removesound'])
    @app_commands.describe(sound="The sound to remove")
    @app_commands.default_permissions(manage_roles=True)
    @commands.check(manage_sounds_check)
    async def remove(self, ctx: Context, *, sound: str):
        """Remove a sound clip."""
        if not sound:
            raise GuildSoundsError('No sound specified in remove command.',
                                   'Gamer, you gotta tell me which sound to remove.')

        sound_name = sound.lower()
        sound_filepath = self.get_sound_filepath(sound_name, ctx.guild)

        if not sound_filepath:
            raise GuildSoundsError('Sound ' + sound_name + ' not found.',
                                   'Hey gamer, that sound doesn\'t exist.')

        self.delete_sound(sound_filepath, ctx.guild)
        await ctx.send('The sound has been eliminated, gamer.')

    @remove.autocomplete('sound')
    async def remove_sound_autocomplete(self, intr: Interaction, query: str) -> list[app_commands.Choice[str]]:
        return self.sound_autocomplete(intr, query)

    @commands.command(aliases=['renamesound'])
    @commands.check(manage_sounds_check)
    async def rename(self, ctx: Context, *, args: str):
        """Rename a sound clip."""
        if not args:
            raise GuildSoundsError('No sound specified in rename command.',
                                   'Yo gamer, do it like this: `$rename old name, new name`')

        old, new = args.lower().split(', ')
        await self.rename_sound(ctx, old, new)

    @app_commands.command(name='rename')
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(sound="The sound to rename", new_name="The new name of the sound")
    async def rename_slash(self, intr: Interaction, sound: str, new_name: str):
        """Rename a sound clip."""
        await self.rename_sound(intr, sound, new_name)

    @rename_slash.autocomplete('sound')
    async def rename_sound_autocomplete(self, intr: Interaction, query: str) -> list[app_commands.Choice[str]]:
        return self.sound_autocomplete(intr, query)

    async def rename_sound(self, ctx: Context | Interaction, old: str, new: str):
        send_method = (
            ctx.response.send_message if type(ctx) is Interaction else ctx.send
        )

        print(f'renaming {old} to {new} in {ctx.guild.name}')
        guild_info = self.bot.get_guildinfo(ctx.guild.id)
        folder = guild_info.sound_folder
        old_filepath = self.get_sound_filepath(old, ctx.guild)
        if old_filepath:
            extension = os.path.splitext(old_filepath)[1]
            new_filepath = os.path.join(folder, new + extension)
            try:
                self.rename_file(old_filepath, new_filepath)
                guild_info.remove_sound(old)
                guild_info.add_sound(new + extension)
                await send_method('Knuckles: cracked. Headset: on. **Sound: renamed.**\nYup, it\'s Rats Movie time.')
            except Exception as e:
                raise GuildSoundsError(f'Error {type(e)} while renaming sound:\n{e}',
                                       'Something went wrong, zoomer. Make sure no other sound has the new name, okay?')
        else:
            await send_method(f'I couldn\'t find a sound with the name {old}, aight?')

    @commands.hybrid_command(aliases=['sleep'])
    @app_commands.default_permissions(use_application_commands=True)
    async def snooze(self, ctx: Context):
        """Disable join sounds for 4 hours or until you call snooze again."""
        r = self.bot.get_guildinfo(ctx.guild.id).toggle_snooze()
        if r:
            r = int(r)
            await ctx.me.edit(nick=snoozed_nickname)
            await ctx.send(f'Join sounds will come back <t:{r}:R> at <t:{r}:t>. See you then, champ!')
        else:
            await ctx.me.edit(nick=regular_nickname)
            await ctx.send(f'**I HAVE AWOKEN**')

    @commands.hybrid_command()
    @commands.is_owner()
    @app_commands.guilds(*ADMIN_GUILDS)
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def reload_sounds(self, ctx: Context):
        """Reload the in-memory sound paths cache"""
        for guild in self.bot.guilds:
            self.bot.get_guildinfo(guild.id).reload_sounds()
        await ctx.send('Sounds reloaded.')

    def has_sound_file(self, message):
        if len(message.attachments) == 0:
            return False
        attachment = message.attachments[0]
        return attachment.filename.endswith('.mp3') or attachment.filename.endswith('.wav')

    async def add_sound_to_guild(self, sound, guild, filename=None):
        sound_folder = self.get_guild_sound_path(guild)
        if not filename:
            filename = sound.filename.lower()
        path = os.path.join(sound_folder, filename)
        await sound.save(path)
        self.bot.get_guildinfo(guild.id).add_sound(filename)

    def delete_sound(self, filepath, guild: Guild):
        if 'sounds' not in filepath:
            sound_folder = self.get_guild_sound_path(guild)
            filepath = os.path.join(sound_folder, filepath)
        print('deleting ' + filepath)
        os.remove(filepath)
        sound_name = os.path.basename(filepath)
        self.bot.get_guildinfo(guild.id).remove_sound(sound_name)

    def rename_file(self, old_filepath, new_filepath):
        os.rename(old_filepath, new_filepath)

    def get_guild_sound_path(self, guild: Guild | int):
        guild_id = guild.id if type(guild) is Guild else guild
        ginfo = self.bot.get_guildinfo(guild_id)
        return ginfo.sound_folder

    def make_list_embed(self, guild_info: GuildInfo):
        _lim = 1024
        sounds = '\n'.join(sorted(guild_info.sounds))
        overflow = None
        if len(sounds) > _lim:
            _split = sounds.rindex('\n', 0, len(sounds) // 2)
            overflow = sounds[_split:]
            sounds = sounds[:_split]
        sound_embed = Embed(title="JermaBot Sounds",
                            description="Send `$play <sound>` to play them in your server, gamer!",
                            color=0x66c3cb)
        sound_embed.add_field(name='Sounds?', value=sounds, inline=True)
        if overflow:
            sound_embed.add_field(name='Sounds!', value=overflow, inline=True)
        sound_embed.set_footer(
            text="Message your server admins to get custom sounds added!")

        return sound_embed

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: Member, before: VoiceState, after: VoiceState):
        """
        Manage functionality when someone changes voice state.

        Play a join noise when a user joins a channel,
        cleanup if kicked from voice,
        play a leave sound for a certain user,
        or disconnect if connected to an empty voice channel.
        """
        # member (Member) – The member whose voice states changed.
        # before (VoiceState) – The voice state prior to the changes.
        # after (VoiceState) – The voice state after the changes.
        print(
            f'[{time.ctime()}] {y}Voice state update: {member.name} in {c}{member.guild}')
        print(f'{self.voice_state_diff_str(before, after)}')

        control: Control = self.bot.get_cog('Control')
        old_vc = control.get_existing_voice_client(member.guild)
        g = self.bot.get_guildinfo(member.guild.id)

        if g.is_snapping or g.is_snoozed():
            print(f'{y}Ignoring voice state update due to snap or snooze')
            return
        elif not g.is_snoozed() and member.guild.me.display_name == snoozed_nickname:
            print(f'{y}Reset nickname in {member.guild}')
            await member.guild.me.edit(nick=regular_nickname)

        # cleanup connection if kicked
        if member.id == self.bot.user.id:
            print(f'{y}Voice update was self')
            if old_vc and not after.channel:
                print('Attempting to disconnect from old voice client')
                await old_vc.disconnect()
            return

        if self.user_joined_channel(before, after) and not member.voice.afk:
            await self.play_join_sound(member, old_vc)
        elif old_vc and self.old_voice_channel_has_no_people(old_vc):
            await self.disconnect_from_voice(old_vc)
        elif old_vc and self.user_left_channel(before, after) and before.channel == old_vc.channel:
            self.play_leave_sound(member, old_vc)
        elif old_vc and self.was_user_server_muted(before, after) and before.channel == old_vc.channel:
            self.play_muted(old_vc)

    def voice_state_diff_str(self, v1, v2) -> str:
        """Return a descriptive string of differences between two voice states."""
        attrs = ['afk', 'channel', 'deaf', 'mute', 'self_deaf',
                 'self_mute', 'self_stream', 'self_video']
        out = ''
        for attr in attrs:
            a1 = getattr(v1, attr)
            a2 = getattr(v2, attr)
            if a1 != a2:
                out += f'\t{attr} from {b}{a1}\t{n}to {b}{a2}\n'
        return out[:-1]

    def user_joined_channel(self, before: VoiceState, after: VoiceState):
        joined_voice = not before.channel and after.channel
        moved_chan = (
            (before.channel and after.channel) and before.channel != after.channel
        )
        return joined_voice or moved_chan

    async def play_join_sound(self, member: Member, vc: VoiceClient):
        print(f'{y}Checking if {member.voice.channel} has a user limit...')
        print(member.voice.channel.user_limit)
        if member.voice.channel.user_limit:
            return

        print(f'{y}Playing join sound if exists...')
        join_sound = self.get_sound_filepath(member.name, member.guild)
        if join_sound:
            print(f'{y}Found join sound')
            control: Control = self.bot.get_cog('Control')
            player: SoundPlayer = self.bot.get_cog('SoundPlayer')
            vc = await control.connect_to_channel(vc, member.voice.channel)
            print(f'{y}{vc}')
            if not vc:
                return
            player.play_sound_file(join_sound, vc)
        else:
            print('No join sound for user')

    def old_voice_channel_has_no_people(self, vc: VoiceClient):
        return len([x for x in vc.channel.members if not x.bot]) == 0

    async def disconnect_from_voice(self, vc: VoiceClient):
        print(
            f'[{time.ctime()}] {y}Disconnecting from {c}{vc.guild} #{vc.channel} {y}because it is empty.')
        await vc.disconnect()

    def was_user_server_muted(self, before: VoiceState, after: VoiceState):
        return (not before.mute) and after.mute

    def user_left_channel(self, before: VoiceState, after: VoiceState):
        was_in_vc = before != None
        not_in_vc = (
            not after or
            not hasattr(after, 'channel') or
            not after.channel
        )
        return was_in_vc and not_in_vc

    def play_muted(self, vc: VoiceClient):
        sound = self.get_muted_sound()
        if sound:
            player: SoundPlayer = self.bot.get_cog('SoundPlayer')
            player.play_sound_file(sound, vc)

    def play_leave_sound(self, member: Member, vc: VoiceClient):
        if member.id == 196742230659170304:
            leave_sound = self.get_yoni_leave_sound()
            if leave_sound:
                player: SoundPlayer = self.bot.get_cog('SoundPlayer')
                player.play_sound_file(leave_sound, vc)

    def get_muted_sound(self):
        return os.path.join('resources', 'soundclips', 'muted.mp3')

    def get_yoni_leave_sound(self):
        return os.path.join('resources', 'soundclips', 'workhereisdone.wav')

    @commands.Cog.listener()
    async def on_guild_join(self, guild: Guild):
        """Initialize guild sounds directory for new guild."""
        os.makedirs(os.path.join(
            'guilds', f'{guild.id}', 'sounds'
        ), exist_ok=True)
        self.bot.make_guildinfo(guild)
        print(f'Added to {guild.name}:{guild.id}!')
