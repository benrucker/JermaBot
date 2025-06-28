from discord.ext.commands import CommandError


class ErrorWithUiMessage(CommandError):
    def __init__(self, error: str, msg: str) -> None:
        self.error = error
        self.msg = msg
