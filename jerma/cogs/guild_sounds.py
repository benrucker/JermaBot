import os
import random
import time
from pathlib import Path
from typing import Optional, Union, List

import discord

from cogs.control import Control, JoinFailedError
from cogs.sound_player import SoundPlayer
from colorama import Fore as t
from colorama import Style
from discord import AppCommandType, Guild, Interaction, Member, Message, VoiceClient, VoiceState, app_commands, Attachment
from discord.embeds import Embed
from discord.ext import commands
from discord.ext.commands import Context
from guild_info import GuildInfo

from cogs.utils.guild_interaction import GuildInteraction, assert_guild_interaction
from jermabot import JermaBot
from .utils.guild_context import GuildContext, assert_guild_context
from .utils.error_with_ui_message import ErrorWithUiMessage
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


async def manage_sounds_check(ctx: Context) -> bool:
    if ctx.guild is None:
        raise commands.NoPrivateMessage('This command cannot be used in DMs.')
    member = ctx.guild.get_member(ctx.author.id)
    if member is None:
        raise commands.NoPrivateMessage('You are not a member of this guild.')
    p = ctx.channel.permissions_for(member)
    return p.kick_members or \
        p.ban_members or \
        p.administrator or \
        p.manage_guild or \
        p.move_members or \
        p.manage_nicknames or \
        p.manage_roles or \
        p.deafen_members or \
        p.mute_members


async def setup(bot: JermaBot) -> None:
    await bot.add_cog(GuildSounds(bot))


class GuildSounds(commands.Cog):
    """Cog for maintaining guild-specific sound functionality."""

    def __init__(self, bot: JermaBot) -> None:
        self.bot: JermaBot = bot
        self.ctx_menu = app_commands.ContextMenu(
            name="Make user's join sound",
            callback=self.add_join_sound_via_context_menu,
            type=AppCommandType.message,
        )
        self.bot.tree.add_command(self.ctx_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(
            self.ctx_menu.name, type=self.ctx_menu.type)

    async def cog_command_error(self, ctx: Context, error: Exception) -> None:
        if isinstance(error, ErrorWithUiMessage):
            print(error.error)
            await ctx.send(error.msg)
        elif isinstance(error, JoinFailedError):
            await ctx.send(str(error))
        else:
            raise error

    @commands.hybrid_command()
    @app_commands.describe(sound="The sound to play")
    async def play(self, ctx: Context, *, sound: str):
        """Play a sound."""
        if not sound:
            raise ErrorWithUiMessage('No sound specified in play command.',
                                     'Gamer, you gotta tell me which sound to play.')
        sound_name = sound.lower()
        sound_filepath = self.get_sound_filepath(sound_name, ctx.guild)
        if not sound_filepath:
            raise ErrorWithUiMessage('Sound ' + sound + ' not found.',
                                     'Hey gamer, that sound doesn\'t exist.')

        if ctx.guild is None:
            print(f'{t.RED}Play was called in a non-guild context')
            raise RuntimeError(
                "You can't use this command outside of a guild.")
        member = ctx.guild.get_member(ctx.author.id)
        if member is None:
            print(f'{t.RED}Play was called by a member that is not in the guild')
            raise RuntimeError("Seems like you're not in this guild.")
        if not member.voice:
            print(
                f'{t.RED}Play was called by a member that is not in a voice channel')
            raise JoinFailedError()

        control: Control = self.bot.get_cog('Control')
        player: SoundPlayer = self.bot.get_cog('SoundPlayer')

        vc = await control.connect_to_user(member.voice, ctx.guild)
        if vc is None:
            print(f'{t.RED}Failed to connect to voice channel for {member.name}')
            raise RuntimeError()

        player.play_sound_file(sound_filepath, vc)

        if ctx.interaction:
            await ctx.send(f"Playing **{sound_name}**")

    @play.autocomplete('sound')
    async def play_sound_autocomplete(self, intr: Interaction, query: str) -> List[app_commands.Choice[str]]:
        if intr.guild_id is None:
            print(f'{t.RED}Play autocomplete was called in a non-guild context')
            raise RuntimeError(
                "You can't use this command outside of a guild.")

        return self.sound_autocomplete(intr.guild_id, query)

    def sound_autocomplete(self, guild_id: int, query: str) -> List[app_commands.Choice[str]]:
        sounds = self.bot.get_guildinfo(guild_id).sounds.keys()
        return autocomplete(query.lower(), list(sounds))

    def get_sound_filepath(self, sound_name: str, guild: Optional[Guild]) -> Optional[str]:
        if not guild:
            return None

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
        if ctx.guild is None:
            raise ErrorWithUiMessage("Random was called in a non-guild context",
                                     "You can't use this command outside of a guild.")
        member = ctx.guild.get_member(ctx.author.id)
        if member is None:
            raise ErrorWithUiMessage(
                "Random was called by a member that is not in the guild", "Seems like you're not in this guild.")
        if not member.voice:
            print(
                f'{t.RED}Random was called by a member that is not in a voice channel')
            raise JoinFailedError()

        sound, sound_name = self.get_random_sound(ctx.guild)
        if not sound:
            raise ErrorWithUiMessage('Guild has no sounds.',
                                     'Sorry gamer, but you need to add some sounds for me to play!')

        control: Control = self.bot.get_cog('Control')
        player: SoundPlayer = self.bot.get_cog('SoundPlayer')
        vc = await control.connect_to_user(member.voice, ctx.guild)
        player.play_sound_file(sound, vc)

        await ctx.send(f"Playing **{sound_name}**")

    def get_random_sound(self, guild: Guild) -> tuple[str, str]:
        ginfo: GuildInfo = self.bot.get_guildinfo(guild.id)
        sound_name, sound_filename = random.choice(list(ginfo.sounds.items()))
        return os.path.join(ginfo.sound_folder, sound_filename), sound_name

    @commands.hybrid_command(name='list', aliases=['sounds'])
    @app_commands.default_permissions(use_application_commands=True)
    async def _list(self, ctx: Context):
        """Send the user a list of sounds that can be played."""
        if ctx.guild is None:
            raise ErrorWithUiMessage("$list was called in a non-guild context",
                                     "You can't use this command outside of a guild.")

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
        ctx = assert_guild_context(ctx)

        attachment = await self.get_or_ask_for_sound_file(ctx)

        filename = self.create_sound_filename_with_extension(
            attachment, sound_name
        )

        # remove old sound if there
        should_continue = await self.validate_existing_sound_removal(ctx, filename)
        if not should_continue:
            return

        await self.add_sound_to_guild(attachment, ctx.guild, filename=filename)
        await ctx.send('Sound added, gamer.')

    @app_commands.describe()
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.guild_only()
    async def add_join_sound_via_context_menu(self, intr: Interaction, message: Message) -> None:
        """A context menu command to turn a sound file that a user has sent into their join sound."""
        # TODO: Make GuildInteraction class and type guard
        intr = assert_guild_interaction(intr)

        attachment = self.get_sound_file_from_message(message)

        if not attachment:
            await intr.response.send_message("I couldn't find a sound file there 🥴", ephemeral=True)
            return

        filename = self.create_sound_filename_with_extension(
            attachment, message.author.name
        )

        should_continue = await self.validate_existing_sound_removal_via_message_components(intr, filename)
        if not should_continue:
            return

        await self.add_sound_to_guild(attachment, intr.guild, filename=filename)

        if intr.response.is_done():
            await intr.edit_original_response(content='Sound added, gamer.')
        else:
            await intr.response.send_message('Sound added, gamer.')

    async def get_or_ask_for_sound_file(self, ctx: GuildContext) -> Attachment:
        attachment = ctx.message.attachments[0] if len(
            ctx.message.attachments
        ) else None

        if not attachment:
            await ctx.send('Alright gamer, send the new sound.')

            def is_message_from_author_with_sound_file(message: Message):
                return message.author == ctx.author and self.does_message_have_sound_file(message)

            message: Message = await self.bot.wait_for('message', timeout=20, check=is_message_from_author_with_sound_file)
            attachment = message.attachments[0]

        return attachment

    async def validate_existing_sound_removal(self, ctx: GuildContext, filename: str) -> bool:
        name = self.strip_extension_from_filename(filename)
        existing = self.get_sound_filepath(name, ctx.guild)
        if existing:
            await ctx.send(f'There\'s already a sound called _{name}_, bucko. Sure you want to replace it? (yeah/nah)')

            def is_message_from_author(message: Message):
                return message.author.id == ctx.author.id

            replace_msg: Message = await self.bot.wait_for('message', timeout=20, check=is_message_from_author)
            if replace_msg.content.lower().strip() in YES:
                await ctx.send('Expunging the old sound...')
                self.delete_sound(os.path.split(existing)[1], ctx.guild)
            else:
                await ctx.send('Yeah, I like the old one better too.')
                return False

        return True

    async def validate_existing_sound_removal_via_message_components(self, intr: GuildInteraction, filename: str) -> bool:
        name = self.strip_extension_from_filename(filename)
        existing = self.get_sound_filepath(name, intr.guild)
        if existing:
            confirmation_prompt = self.ConfirmSoundReplaceMessageView()
            await intr.response.send_message(
                f'There\'s already a sound called _{name}_, bucko. Sure you want to replace it?',
                view=confirmation_prompt,
            )

            await confirmation_prompt.wait()

            if confirmation_prompt.interaction is not None and confirmation_prompt.is_confirmed:
                await confirmation_prompt.interaction.response.edit_message(content='Expunging the old sound...', view=None)
                self.delete_sound(os.path.split(existing)[1], intr.guild)
            else:
                await intr.edit_original_response(content='Yeah, I like the old one better too.', view=None)
                return False

        return True

    class ConfirmSoundReplaceMessageView(discord.ui.View):
        def __init__(self):
            super().__init__()
            self.is_confirmed = None
            self.interaction = None

        @discord.ui.button(label='Cancel', style=discord.ButtonStyle.grey)
        async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
            self.interaction = interaction
            self.is_confirmed = False
            self.stop()

        @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
        async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
            self.interaction = interaction
            self.is_confirmed = True
            self.stop()

    def strip_extension_from_filename(self, filename):
        return filename.rsplit('.', 1)[0].lower()

    def create_sound_filename_with_extension(self, attachment: Attachment, target_filename: Optional[str] = None) -> str:
        target_filename = target_filename.lower() if target_filename else None
        if target_filename:
            if target_filename.endswith(('.mp3', '.wav')):
                # Filename is already well-formatted, use it as is
                filename = target_filename
            else:
                # Add the correct file extension based on the attachment
                filename = target_filename + '.' + \
                    attachment.filename.split('.')[-1]
        else:
            filename = attachment.filename
        return filename.lower()

    @commands.hybrid_command(aliases=['removesound'])
    @app_commands.describe(sound="The sound to remove")
    @app_commands.default_permissions(manage_roles=True)
    @commands.check(manage_sounds_check)
    async def remove(self, ctx: Context, *, sound: str):
        """Remove a sound clip."""
        if ctx.guild is None:
            raise ErrorWithUiMessage('removesound was called in a non-guild context.',
                                     'You can\'t use this command outside of a guild.')

        if not sound:
            raise ErrorWithUiMessage('No sound specified in remove command.',
                                     'Gamer, you gotta tell me which sound to remove.')

        sound_name = sound.lower()
        sound_filepath = self.get_sound_filepath(sound_name, ctx.guild)

        if not sound_filepath:
            raise ErrorWithUiMessage('Sound ' + sound_name + ' not found.',
                                     'Hey gamer, that sound doesn\'t exist.')

        self.delete_sound(sound_filepath, ctx.guild)
        await ctx.send('The sound has been eliminated, gamer.')

    @remove.autocomplete('sound')
    async def remove_sound_autocomplete(self, intr: Interaction, query: str) -> List[app_commands.Choice[str]]:
        if intr.guild_id is None:
            print(f'{t.RED}Remove autocomplete was called in a non-guild context')
            return []

        return self.sound_autocomplete(intr.guild_id, query)

    @commands.command(aliases=['renamesound'])
    @commands.check(manage_sounds_check)
    async def rename(self, ctx: Context, *, args: str):
        """Rename a sound clip."""
        if not args:
            raise ErrorWithUiMessage('No sound specified in rename command.',
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
    async def rename_sound_autocomplete(self, intr: Interaction, query: str) -> List[app_commands.Choice[str]]:
        if intr.guild_id is None:
            print(f'{t.RED}Rename autocomplete was called in a non-guild context')
            return []

        return self.sound_autocomplete(intr.guild_id, query)

    async def rename_sound(self, ctx: Union[Context, Interaction], old: str, new: str) -> None:
        if ctx.guild is None:
            raise ErrorWithUiMessage('rename was called in a non-guild context.',
                                     'You can\'t use this command outside of a guild.')

        send_method = (
            ctx.response.send_message if isinstance(
                ctx, Interaction) else ctx.send
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
                raise ErrorWithUiMessage(f'Error {type(e)} while renaming sound:\n{e}',
                                         'Something went wrong, zoomer. Make sure no other sound has the new name, okay?')
        else:
            await send_method(f'I couldn\'t find a sound with the name {old}, aight?')

    @commands.hybrid_command(aliases=['sleep'])
    @app_commands.default_permissions(use_application_commands=True)
    async def snooze(self, ctx: Context):
        """Disable join sounds for 4 hours or until you call snooze again."""
        if ctx.guild is None:
            raise ErrorWithUiMessage('snooze was called in a non-guild context.',
                                     'You can\'t use this command outside of a guild.')

        bot_member = ctx.guild.get_member(ctx.me.id)
        if not bot_member:
            raise ErrorWithUiMessage('Bot is not a member of this guild.',
                                     'I\'m not a member of your server, dude.')

        maybe_snooze_end_time = self.bot.get_guildinfo(
            ctx.guild.id).toggle_snooze()
        if maybe_snooze_end_time:
            duration = int(maybe_snooze_end_time)
            await bot_member.edit(nick=snoozed_nickname)
            await ctx.send(f'Join sounds will come back <t:{duration}:R> at <t:{duration}:t>. See you then, champ!')
        else:
            await bot_member.edit(nick=regular_nickname)
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

    def does_message_have_sound_file(self, message: Message) -> bool:
        return self.get_sound_file_from_message(message) is not None

    def get_sound_file_from_message(self, message: Message) -> Attachment | None:
        if len(message.attachments) == 0:
            return None
        attachment = message.attachments[0]
        if attachment.filename.endswith('.mp3') or attachment.filename.endswith('.wav'):
            return attachment

    async def add_sound_to_guild(self, sound: Attachment, guild: Guild, filename: Optional[str] = None) -> None:
        sound_folder = self.get_guild_sound_path(guild)
        if not filename:
            filename = sound.filename.lower()
        path = Path(sound_folder) / filename
        await sound.save(path)
        self.bot.get_guildinfo(guild.id).add_sound(filename)

    def delete_sound(self, filepath: str, guild: Guild) -> None:
        if 'sounds' not in filepath:
            sound_folder = self.get_guild_sound_path(guild)
            filepath = os.path.join(sound_folder, filepath)
        print('deleting ' + filepath)
        os.remove(filepath)
        sound_name = os.path.basename(filepath)
        self.bot.get_guildinfo(guild.id).remove_sound(sound_name)

    def rename_file(self, old_filepath: str, new_filepath: str) -> None:
        os.rename(old_filepath, new_filepath)

    def get_guild_sound_path(self, guild: Union[Guild, int]) -> str:
        guild_id = guild.id if isinstance(guild, Guild) else guild
        ginfo = self.bot.get_guildinfo(guild_id)
        return ginfo.sound_folder

    def make_list_embed(self, guild_info: GuildInfo) -> Embed:
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
        if self.bot.user and member.id == self.bot.user.id:
            print(f'{y}Voice update was self')
            if old_vc and not after.channel:
                print('Attempting to disconnect from old voice client')
                await old_vc.disconnect()
            return

        if self.user_joined_channel(before, after) and member.voice and not member.voice.afk:
            await self.play_join_sound(member, old_vc)
        elif old_vc and self.old_voice_channel_has_no_people(old_vc):
            await self.disconnect_from_voice(old_vc)
        elif old_vc and self.user_left_channel(before, after) and before.channel == old_vc.channel:
            self.play_leave_sound(member, old_vc)
        elif old_vc and self.was_user_server_muted(before, after) and before.channel == old_vc.channel:
            self.play_muted(old_vc)

    def voice_state_diff_str(self, v1: VoiceState, v2: VoiceState) -> str:
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

    def user_joined_channel(self, before: VoiceState, after: VoiceState) -> bool:
        return after.channel is not None and before.channel != after.channel

    async def play_join_sound(self, member: Member, vc: Optional[VoiceClient]) -> None:
        if not member.voice or not member.voice.channel:
            print(f'{y}Member {member.name} is not in a voice channel.')
            return

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

    def old_voice_channel_has_no_people(self, vc: VoiceClient) -> bool:
        return len(vc.channel.members) == 0 or all(x.bot for x in vc.channel.members)

    async def disconnect_from_voice(self, vc: VoiceClient) -> None:
        print(
            f'[{time.ctime()}] {y}Disconnecting from {c}{vc.guild} #{vc.channel} {y}because it is empty.')
        await vc.disconnect()

    def was_user_server_muted(self, before: VoiceState, after: VoiceState) -> bool:
        return (not before.mute) and after.mute

    def user_left_channel(self, before: VoiceState, after: VoiceState) -> bool:
        was_in_vc = before != None
        not_in_vc = (
            not after or
            not hasattr(after, 'channel') or
            not after.channel
        )
        return was_in_vc and not_in_vc

    def play_muted(self, vc: VoiceClient) -> None:
        sound = self.get_muted_sound()
        if sound:
            player: SoundPlayer = self.bot.get_cog('SoundPlayer')
            player.play_sound_file(sound, vc)

    def play_leave_sound(self, member: Member, vc: VoiceClient) -> None:
        if member.id == 196742230659170304:
            leave_sound = self.get_yoni_leave_sound()
            if leave_sound:
                player: SoundPlayer = self.bot.get_cog('SoundPlayer')
                player.play_sound_file(leave_sound, vc)

    def get_muted_sound(self) -> str:
        return os.path.join('resources', 'soundclips', 'muted.mp3')

    def get_yoni_leave_sound(self) -> str:
        return os.path.join('resources', 'soundclips', 'workhereisdone.wav')

    @commands.Cog.listener()
    async def on_guild_join(self, guild: Guild) -> None:
        """Initialize guild sounds directory for new guild."""
        os.makedirs(os.path.join(
            'guilds', f'{guild.id}', 'sounds'
        ), exist_ok=True)
        self.bot.make_guildinfo(guild)
        print(f'Added to {guild.name}:{guild.id}!')
