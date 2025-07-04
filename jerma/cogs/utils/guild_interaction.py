from discord import Guild, Interaction


class GuildInteraction(Interaction):
    guild: Guild  # type: ignore


def assert_guild_interaction(intr: Interaction) -> GuildInteraction:
    """Ensure that a Interaction has a non-None guild and return it as GuildInteraction type."""
    if intr.guild is None:
        raise Exception('Operation called in non-guild context.',
                        'This command can only be used in a server.')
    return intr  # type: ignore
