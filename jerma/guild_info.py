import os
import time

class GuildInfo():
    """This class holds information about the state of the bot in a given guild."""


    def __init__(self, guild):
        self.name = guild.name
        self.id = guild.id
        self.is_snapping = False
        self.snooze_resume = None
        self.volume = .6
        self.sound_folder = os.path.join('guilds', str(self.id), 'sounds')

    def __repr__(self):
        return 'GuildInfo Object: ' + self.name + ':' + self.id

    def snooze(self, duration=4):
        if self.snooze_resume:
            self.snooze_resume = None
        self.snooze_resume = time.time() + 4*60*60
        return self.snooze_resume

    def is_snoozed(self):
        if self.snooze_resume and self.snooze_resume < time.time():
            self.snooze_resume = None
            return False
        return True
