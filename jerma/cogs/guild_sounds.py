from colorama import Fore as t
from colorama import Back, Style
import discord
from discord.ext import commands
import time
import os
from glob import glob
from discord.embeds import Embed


YES = ['yes','yeah','yep','yeppers','of course','ye','y','ya','yah']
NO  = ['no','n','nope','start over','nada', 'nah']


async def manage_sounds_check(ctx):
    p = ctx.channel.permissions_for(ctx.author)
    return p.kick_members or \
           p.ban_members or \
           p.administrator or \
           p.manage_guild or \
           p.move_members or \
           p.manage_nicknames or \
           p.manage_roles or \
           p.deafen_members or \
           p.mute_members or \
           p.mute_members


class GuildSounds(commands.Cog):
    def __init__(self, bot, path_to_guilds):
        self.bot = bot
        self.path_to_guilds = path_to_guilds
        #self.sounds_dict  # keep this static until add,rm,or rename

    def get_sound(self, sound, guild: discord.Guild):
        ginfo = self.bot.get_guildinfo(guild.id)
        sounds = self.make_sounds_dict(ginfo.sound_folder)
        try:
            return os.path.join(ginfo.sound_folder, sounds[sound.lower()])
        except KeyError:
            return None

    def has_sound_file(self, message):
        if len(message.attachments) == 0:
            return False
        attachment = message.attachments[0]
        return attachment.filename.endswith('.mp3') or attachment.filename.endswith('.wav')

    def get_guild_sound_path(self, guild):
        ginfo = self.bot.get_guildinfo(guild.id)
        return ginfo.sound_folder

    async def add_sound_to_guild(self, sound, guild, filename=None):
        folder = self.get_guild_sound_path(guild)
        if not filename:
            filename = sound.filename.lower()

        path = os.path.join(folder, filename)

        await sound.save(path)

    @commands.command()
    @commands.check(manage_sounds_check)
    async def addsound(self, ctx, *args):
        """Add a sound to the sounds list. Requires certain server perms."""
        arg = ' '.join(args).lower()

        # get sound file from user
        await ctx.send('Alright gamer, send the new sound.')
        def check(message):
            return message.author is ctx.author and self.has_sound_file(message)
        message = await self.bot.wait_for('message', timeout=20, check=check)

        # construct name from arg or attachment
        if arg:
            if arg.endswith(('.mp3','.wav')):
                filename = arg
            else:
                filename = arg + '.' + message.attachments[0].filename.split('.')[-1]
        else:
            filename = message.attachments[0].filename
        filename = filename.lower()

        # remove old sound if there
        name = filename.split('.')[0].lower()
        existing = self.get_sound(name, ctx.guild)
        if existing:
            await ctx.send(f'There\'s already a sound called _{name}_, bucko. Sure you want to replace it? (yeah/nah)')
            def check2(message):
                return message.author is ctx.author
            replace_msg = await self.bot.wait_for('message', timeout=20, check=check2)
            if replace_msg.content.lower().strip() in YES:
                self.delete_sound(os.path.split(existing)[1], ctx.guild)
            else:
                await ctx.send('Yeah, I like the old one better too.')
                return

        await self.add_sound_to_guild(message.attachments[0], ctx.guild, filename=filename)
        await ctx.send('Sound added, gamer.')

    def delete_sound(self, filepath, guild: discord.Guild):
        path = self.bot.get_guildinfo(guild.id).sound_folder
        if 'sounds' not in filepath:
            filepath = os.path.join(path,filepath)
        print('deleting ' + filepath)
        os.remove(filepath)

    @commands.command()
    @commands.check(manage_sounds_check)
    async def remove(self, ctx, *args):
        """Remove a sound clip."""
        if not args:
            raise self.bot.JermaException('No sound specified in remove command.',
                                'Gamer, you gotta tell me which sound to remove.')

        sound_name = ' '.join(args)
        sound = self.get_sound(sound_name, ctx.guild)

        if not sound:
            raise self.bot.JermaException('Sound ' + sound + ' not found.',
                                'Hey gamer, that sound doesn\'t exist.')

        self.delete_sound(sound, ctx.guild)
        await ctx.send('The sound has been eliminated, gamer.')


    def rename_file(self, old_filepath, new_filepath):
        os.rename(old_filepath, new_filepath)

    @commands.command()
    @commands.check(manage_sounds_check)
    async def rename(self, ctx, *args):
        """Rename a sound clip."""
        if not args:
            raise self.bot.JermaException('No sound specified in rename command.',
                                'Gamer, do it like this: `$rename old name, new name`')

        old, new = ' '.join(args).split(', ')
        print(f'renaming {old} to {new} in {ctx.guild.name}')
        old_filename = self.get_sound(old, ctx.guild)
        if old_filename:
            new_filename = old_filename[:33] + new.lower() + old_filename[-4:]
            try:
                self.rename_file(old_filename, new_filename)
                await ctx.send('Knuckles: cracked. Headset: on. **Sound: renamed.**\nYup, it\'s Rats Movie time.')
            except Exception as e:
                raise self.bot.JermaException(f'Error {type(e)} while renaming sound',
                                    'Something went wrong, zoomer. Make sure no other sound has the new name, okay?')
        else:
            await ctx.send(f'I couldn\'t find a sound with the name {old}, aight?')

    def make_sounds_dict(self, id):
        sounds = {}
        sound_folder = os.path.join(self.path_to_guilds, id, 'sounds')
        #print('Finding sounds in:', sound_folder)
        for filepath in glob(os.path.join(sound_folder, '*')): # find all files in folder w/ wildcard
            filename = os.path.basename(filepath)
            extension = filename.split('.')[1]
            if extension not in ['mp3', 'wav']:
                continue
            sounds[filename.split('.')[0]] = filename
        return sounds

    def get_list_embed(self, guild_info):
        sounds = self.make_sounds_dict(guild_info.sound_folder)

        soundEmbed = Embed(title=" list | all of the sounds in Jerma's directory", description="call these with the prefix to play them in your server, gamer!", color=0x66c3cb)
        soundEmbed.set_author(name="Jermabot Help", url="https://www.youtube.com/watch?v=fnbvTOcNFhU", icon_url="attachment://avatar.png")
        soundEmbed.set_thumbnail(url="attachment://thumbnail.png")
        soundEmbed.add_field(name='Sounds:', value='\n'.join(sounds), inline=True)
        soundEmbed.set_footer(text="Message your server owner to get custom sounds added!")

        return soundEmbed

    @commands.command(name='list')
    async def _list(self, ctx):
        """Send the user a list of sounds that can be played."""
        ginfo = self.bot.get_guildinfo(ctx.guild.id)
        # avatar = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='avatar.png')
        # thumbnail = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='thumbnail.png')
        await ctx.author.send(embed=self.get_list_embed(ginfo),
                              #files=[avatar, thumbnail], 
                              )
        await ctx.message.add_reaction("✉")

    @commands.command()
    async def play(self, ctx, *args):
        """Play a sound."""
        if not args:
            raise self.bot.JermaException('No sound specified in play command.',
                                'Gamer, you gotta tell me which sound to play.')

        sound = ' '.join(args)
        current_sound = self.get_sound(sound, ctx.guild)
        if not current_sound:
            raise self.bot.JermaException('Sound ' + sound + ' not found.',
                                'Hey gamer, that sound doesn\'t exist.')

        control = self.bot.get_cog('Control')
        vc = await control.connect_to_user(ctx)
        self.bot.get_cog('SoundPlayer').play_sound_file(current_sound, vc)