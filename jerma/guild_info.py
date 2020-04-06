import os
import pickle
import traceback
import sys


class GuildInfo():
    """This class holds information about the state of the bot in a given guild."""


    def __init__(self, guild):
        self.name = guild.name
        self.id = guild.id
        self.is_snapping = False
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

    def exit(self):
        if len(self.leaderboard) > 0:
            with open(os.path.join(self.folder, 'leaderboard'), 'wb') as file:
                pickle.dump(self.leaderboard, file)

    def add_point(self, user):
        try:
            self.leaderboard[user] += 1
        except KeyError:
            self.leaderboard[user] = 1