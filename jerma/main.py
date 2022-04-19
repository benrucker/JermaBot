import argparse
import logging
import os

import colorama
import discord
from discord.ext import commands

from cogs.utils import ttsengine
from jermabot import JermaBot

YES = ['yes', 'yeah', 'yep', 'yeppers', 'of course', 'ye', 'y', 'ya', 'yah']
NO = ['no', 'n', 'nope', 'start over', 'nada', 'nah']


colorama.init(autoreset=True)
guilds = dict()
tts = None
intents = discord.Intents.default()

prefixes = ['$']


if __name__ == '__main__':
    global source_path, bot
    source_path = os.path.dirname(os.path.abspath(__file__))

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description='Run JermaBot')
    parser.add_argument('-s', '--secret_filename',
                        help='location of bot token text file',
                        default='secret.txt')
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument('-mycroft', '--mycroft_path',
                       help='tell jerma to use mycroft at the given path')
    group.add_argument('-voice', '--voice_path',
                       help='tell jerma to use windows voice.exe tts engine')
    group.add_argument('-espeak', help='tell jerma to use espeak tts engine',
                       action='store_true')
    parser.add_argument('-mv', '--mycroft_voice',
                        help='name of OR path to the voice to use with mycroft')
    parser.add_argument('-jv', '--japanese_voice',
                        help='path to voice to use with Japanese TTS')
    parser.add_argument('-jd', '--japanese_dict',
                        help='path to dictionary to use with Japanese TTS')
    args = parser.parse_args()

    with open(args.secret_filename) as f:
        secret = f.read().strip()

    if args.mycroft_path:
        voice = args.mycroft_voice if args.mycroft_voice else 'ap'
        tts = ttsengine.construct(
            engine=ttsengine.MYCROFT,
            path=args.mycroft_path,
            voice=voice
        )
    elif args.voice_path:
        tts = ttsengine.construct(
            engine=ttsengine.VOICE,
            path=args.voice_path
        )
    elif args.espeak:
        tts = ttsengine.construct(
            engine=ttsengine.ESPEAK
        )
    else:
        print('Setting TTS to none')
        tts = None

    if args.japanese_voice and args.japanese_dict:
        print('setting JTTS to openjtalk')
        print('openjtalk voice at:', args.japanese_voice)
        jtts = ttsengine.construct(engine=ttsengine.OPEN_JTALK,
                                   voice=args.japanese_voice,
                                   dic=args.japanese_dict)
    else:
        print('setting jtts to None')
        jtts = None

    try:
        os.makedirs(os.path.join('resources', 'soundclips', 'temp'))
    except:
        pass

    bot = JermaBot(
        source_path,
        command_prefix=commands.when_mentioned_or('$', '+'),
        intents=intents
    )
    bot.tts_engine = tts
    bot.jtts_engine = jtts

    bot.run(secret)
