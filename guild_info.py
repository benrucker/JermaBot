


class GuildInfo():
    """This class holds information about the state of the bot in a given guild."""


    def __init__(self, id):
        self.id = id
        self.is_snapping = False
        self.volume = .6
