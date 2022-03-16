from glob import glob
import os
import time
import pickle
import traceback
import sys

FOUR_HOURS = 4*60*60


class GuildInfo():
    """This class holds information about the state of the bot in a given guild."""

    def __init__(self, guild):
        self.name = guild.name
        self.id = guild.id
        self.is_snapping = False
        self.snooze_resume = None
        self.volume = .6
        self.folder = os.path.join('guilds', str(self.id))
        self.sound_folder = os.path.join(self.folder, 'sounds')
        self.sounds = self.make_sounds_dict()

    def __repr__(self):
        return 'GuildInfo Object: ' + self.name + ':' + self.id

    def toggle_snooze(self, duration=4):
        if self.is_snoozed():
            self.snooze_resume = None
        else:
            self.snooze_resume = time.time() + FOUR_HOURS
            return self.snooze_resume

    def is_snoozed(self):
        if self.snooze_resume is None:
            return False
        elif self.snooze_resume < time.time():
            self.snooze_resume = None
            return False
        return True

    def make_sounds_dict(self):
        sounds = {}
        for filepath in glob(os.path.join(self.sound_folder, '*')):
            filename = os.path.basename(filepath)
            name, extension = filename.rsplit('.', 1)
            if extension not in ['mp3', 'wav']:
                continue
            sounds[name] = filename
        return sounds

    def reload_sounds(self):
        self.sounds = self.make_sounds_dict()

    def add_sound(self, sound):
        if any([sound.endswith(x) for x in ['.mp3', '.wav']]):
            name, extension = sound.rsplit('.', 1)
            self.sounds[name] = sound
        else:
            print('Invalid file type')

    def remove_sound(self, sound):
        if any([sound.endswith(x) for x in ['.mp3', '.wav']]):
            name, extension = sound.rsplit('.', 1)
            del self.sounds[name]
        else:
            del self.sounds[sound]
