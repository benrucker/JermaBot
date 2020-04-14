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
        try:
            self.leaderboard = pickle.load(open(os.path.join(self.folder, 'leaderboard'), 'rb'))
        except Exception as e:
            print(traceback.format_exception(None,  # <- type(e) by docs, but ignored
                                             e, e.__traceback__),
                  file=sys.stderr, flush=True)
            self.leaderboard = dict()

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
    
    def exit(self):
        if len(self.leaderboard) > 0:
            with open(os.path.join(self.folder, 'leaderboard'), 'wb') as file:
                pickle.dump(self.leaderboard, file)

    def add_point(self, user, amount=1):
        try:
            self.leaderboard[user] += amount
        except KeyError:
            self.leaderboard[user] = amount

    def reset_score(self, user):
        try:
            self.leaderboard.pop(user)
        except KeyError:
            pass
