import os


class GuildInfo():
    """This class holds information about the state of the bot in a given guild."""


    def __init__(self, guild):
        self.name = guild.name
        self.id = guild.id
        self.is_snapping = False
        self.volume = .6
        self.sound_folder = os.path.join('guilds', str(self.id), 'sounds')

    def __repr__(self):
        return 'GuildInfo Object: ' + self.name + ':' + self.id