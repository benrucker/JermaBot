import discord
from discord import Activity, ActivityType, Embed
from discord.ext import commands
import os
import random
from glob import glob

source_path = os.path.dirname(os.path.abspath(__file__))

def make_sounds_dict(folder):
    sounds = {}
    sound_folder = os.path.join(source_path, folder)
    #print('Finding sounds in:', sound_folder)
    for filepath in glob(os.path.join(sound_folder, '*')): # find all files in folder w/ wildcard
        filename = os.path.basename(filepath)
        extension = filename.split('.')[1]
        if extension not in ['mp3', 'wav']:
            continue
        sounds[filename.split('.')[0]] = filename
    return sounds


"""Embed information."""

helpEmbed = Embed(title=" help | list of all useful commands", description="Fueling epic gamer moments since 2011", color=0x66c3cb)
helpEmbed.set_author(name="Jermabot Help", url="https://www.youtube.com/watch?v=fnbvTOcNFhU", icon_url="attachment://avatar.png")
helpEmbed.set_thumbnail(url="attachment://thumbnail.png")
helpEmbed.add_field(name="speak <words>", value="Jerma joins the channel and says what you input in the command using voice.exe .", inline=True)
helpEmbed.add_field(name="speakdrunk <stuff>", value="Same as speak but more like a streamer during a Labo build.", inline=True)
helpEmbed.add_field(name="adderall <things>", value="Same as speak but more like a streamer during a bitcoin joke.", inline=True)
helpEmbed.add_field(name="play <sound>", value="Jerma joins the channel and plays the sound specified by name. Do not include the file extension. Will only seach in discord-jerma\\sounds.", inline=True)
helpEmbed.add_field(name="birthday <name>", value="Jerma joins the channel and plays a birthday song for the person with the given name.", inline=True)
helpEmbed.add_field(name="join", value=" Jerma joins the channel.", inline=True)
helpEmbed.add_field(name="leave", value="Jerma leaves the channel.", inline=True)
helpEmbed.add_field(name="jermalofi", value="Jerma joins the channel and plays some rats lofi.", inline=True)
helpEmbed.set_footer(text="Message @bebenebenebeb#9414 or @fops#1969 with any questions")


def get_list_embed(guild_info):
    sounds = make_sounds_dict(guild_info.sound_folder)

    soundEmbed = Embed(title=" list | all of the sounds in Jerma's directory", description="call these with the prefix to play them in your server, gamer!", color=0x66c3cb)
    soundEmbed.set_author(name="Jermabot Help", url="https://www.youtube.com/watch?v=fnbvTOcNFhU", icon_url="attachment://avatar.png")
    soundEmbed.set_thumbnail(url="attachment://thumbnail.png")
    soundEmbed.add_field(name='Sounds:', value='\n'.join(sounds), inline=True)
    soundEmbed.set_footer(text="Message your server owner to get custom sounds added!")

    return soundEmbed


"""Activity options."""

activities = [(ActivityType.listening, 'my heart.'),
              (ActivityType.listening, 'rats lofi'),
              (ActivityType.listening, 'Shigurain.'),
              (ActivityType.listening, 'Kim!'),
              (ActivityType.streaming, 'DARK SOULS 3'),
              (ActivityType.streaming, 'Just Cause 4 for 3DS'),
              (ActivityType.watching, 'chat make fun of me.'),
              (ActivityType.watching, 'GrillMasterxBBQ\'s vids.'),
              (ActivityType.watching, 'the byeahs.'), # comma on last item is good
              ]

def get_rand_activity():
    info = random.choice(activities)
    return Activity(name=info[1], type=info[0])


"""Help command."""




class JermaHelpCommand(commands.HelpCommand):
    """Help command for jermabot.

    Most of this class is ripped straight from discord.py as of now."""

    def __init__(**options):
        self.width = options.pop('width', 80)
        self.indent = options.pop('indent', 2)
        self.show_hidden = options.pop('show_hidden', False)
        self.sort_commands = options.pop('sort_commands', True)
        self.dm_help = options.pop('dm_help', False)
        self.dm_help_threshold = options.pop('dm_help_threshold', 1000)
        self.commands_heading = options.pop('commands_heading', "Commands:")
        self.no_category = options.pop('no_category', 'No Category')
        self.paginator = options.pop('paginator', None)

        if self.paginator is None:
            self.paginator = Paginator()

        super().__init__(**options)

    def shorten_text(self, text):
        """Shortens text to fit into the :attr:`width`."""
        if len(text) > self.width:
            return text[:self.width - 3] + '...'
        return text

    def get_ending_note(self):
        """Returns help command's ending note. This is mainly useful to override for i18n purposes."""
        command_name = self.invoked_with
        return "Type {0}{1} command for more info on a command.\n" \
               "You can also type {0}{1} category for more info on a category.".format(self.clean_prefix, command_name)

    def add_indented_commands(self, commands, *, heading, max_size=None):
        """Indents a list of commands after the specified heading.
        The formatting is added to the :attr:`paginator`.
        The default implementation is the command name indented by
        :attr:`indent` spaces, padded to ``max_size`` followed by
        the command's :attr:`Command.short_doc` and then shortened
        to fit into the :attr:`width`.
        Parameters
        -----------
        commands: Sequence[:class:`Command`]
            A list of commands to indent for output.
        heading: :class:`str`
            The heading to add to the output. This is only added
            if the list of commands is greater than 0.
        max_size: Optional[:class:`int`]
            The max size to use for the gap between indents.
            If unspecified, calls :meth:`get_max_size` on the
            commands parameter.
        """

        if not commands:
            return

        self.paginator.add_line(heading)
        max_size = max_size or self.get_max_size(commands)

        get_width = discord.utils._string_width
        for command in commands:
            name = command.name
            width = max_size - (get_width(name) - len(name))
            entry = '{0}{1:<{width}} {2}'.format(self.indent * ' ', name, command.short_doc, width=width)
            self.paginator.add_line(self.shorten_text(entry)) # this line seems
                               # pivotal in changing functionality to embedding

    async def send_pages(self):
        """A helper utility to send the page output from :attr:`paginator` to the destination."""
        destination = self.get_destination()
        for page in self.paginator.pages:
            await destination.send(page)

    def add_command_formatting(self, command):
        """A utility function to format the non-indented block of commands and groups.
        Parameters
        ------------
        command: :class:`Command`
            The command to format.
        """

        if command.description:
            self.paginator.add_line(command.description, empty=True)

        signature = self.get_command_signature(command)
        self.paginator.add_line(signature, empty=True)

        if command.help:
            try:
                self.paginator.add_line(command.help, empty=True)
            except RuntimeError:
                for line in command.help.splitlines():
                    self.paginator.add_line(line)
                self.paginator.add_line()

    def get_destination(self):
        ctx = self.context
        if self.dm_help is True:
            return ctx.author
#        elif self.dm_help is None and len(self.paginator) > self.dm_help_threshold:
#            return ctx.author
        else:
            return ctx.channel

    async def prepare_help_command(self, ctx, command):
        self.paginator.clear()
        await super().prepare_help_command(ctx, command)

    async def send_bot_help(self, mapping):
        ctx = self.context
        bot = ctx.bot

        if bot.description:
            # <description> portion
            self.paginator.add_line(bot.description, empty=True)

        no_category = '\u200b{0.no_category}:'.format(self)
        def get_category(command, *, no_category=no_category):
            cog = command.cog
            return cog.qualified_name + ':' if cog is not None else no_category

        filtered = await self.filter_commands(bot.commands, sort=True, key=get_category)
        max_size = self.get_max_size(filtered)
        to_iterate = itertools.groupby(filtered, key=get_category)

        # Now we can add the commands to the page.
        for category, commands in to_iterate:
            commands = sorted(commands, key=lambda c: c.name) if self.sort_commands else list(commands)
            self.add_indented_commands(commands, heading=category, max_size=max_size)

        note = self.get_ending_note()
        if note:
            self.paginator.add_line()
            self.paginator.add_line(note)

        await self.send_pages()

    async def send_command_help(self, command):
        self.add_command_formatting(command)
        self.paginator.close_page()
        await self.send_pages()
