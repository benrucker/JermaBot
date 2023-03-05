from typing import Sequence

from discord import app_commands

from .search import search


def autocomplete(query: str, possibilities: Sequence[str]) -> Sequence[str]:
    results = search(query, possibilities)[:25]
    return [
        app_commands.Choice(name=s, value=s)
        for s in results
    ]
