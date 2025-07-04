from discord import Guild
from discord.ext.commands import Context


class GuildContext(Context):
    guild: Guild  # type: ignore


def assert_guild_context(ctx: Context) -> GuildContext:
    """Ensure that a Context has a non-None guild and return it as GuildContext type."""
    if ctx.guild is None:
        raise Exception('Operation called in non-guild context.',
                        'This command can only be used in a server.')
    return ctx  # type: ignore
