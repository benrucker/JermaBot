import os

from discord import Guild
from discord.ext import commands
from guild_info import GuildInfo


class JermaBot(commands.Bot):
    """Base JermaBot class."""

    def __init__(self, path: str, **kwargs):
        """
        Construct JermaBot.

        Sets the path and guild_info attrs to be accessed by cogs. Calls
        its superclass's constructor as well.
        """
        self.path = path
        self.guild_infos: dict[int, GuildInfo] = dict()
        super().__init__(**kwargs)

    async def setup_hook(self) -> None:
        await self.load_extension('cogs.admin')
        await self.load_extension('cogs.sound_player')
        await self.load_extension('cogs.control')
        await self.load_extension('cogs.tts')
        await self.load_extension('cogs.fun')
        await self.load_extension('cogs.presence')
        await self.load_extension('cogs.guild_sounds')
        await self.load_extension("cogs.s_guild_sounds")
        return await super().setup_hook()

    async def on_ready(self):
        """Initialize some important data and indicate startup success."""
        if len(self.get_guildinfos()) == 0:
            self.initialize_guild_infos()

        print(f'Logged into {len(self.guilds)} guilds:')
        for guild in list(self.guilds):
            print(f'\t{guild.name}:{guild.id}')
        print("Let's fucking go, bois.")

    def get_guildinfos(self) -> dict[int, GuildInfo]:
        """Return a GuildInfo object for the given guild id."""
        return self.guild_infos

    def get_guildinfo(self, gid: int) -> GuildInfo:
        """Return a GuildInfo object for the given guild id."""
        return self.guild_infos[gid]

    def make_guildinfo(self, guild: Guild):
        """Set a guildinfo object to a certain id."""
        self.guild_infos[guild.id] = GuildInfo(guild)

    def initialize_guild_infos(self):
        """Construct dictionary of guildinfo objects."""
        for guild in self.guilds:
            os.makedirs(os.path.join(
                'guilds', f'{guild.id}', 'sounds'), exist_ok=True)
            self.guild_infos[guild.id] = GuildInfo(guild)
