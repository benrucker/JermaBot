import discord
from discord import Activity, ActivityType, Embed
import os
import random
from glob import glob

source_path = os.path.dirname(os.path.abspath(__file__))

def make_sounds_dict():
    sounds = {}
    sound_folder = os.path.join(source_path, 'sounds')
    #print('Finding sounds in:', sound_folder)
    for filepath in glob(os.path.join(sound_folder, '*')): # find all files in folder w/ wildcard
        filename = os.path.basename(filepath)
        extension = filename.split('.')[1]
        if extension not in ['mp3', 'wav']:
            continue
        sounds[filename.split('.')[0]] = filename
    return sounds


"""Embed information."""

helpEmbed = Embed(title=" help | list of all useful commands", description="Fueling epic gamer moments since 2011", color=0x66c3cb)
helpEmbed.set_author(name="Jermabot Help", url="https://www.youtube.com/watch?v=fnbvTOcNFhU", icon_url="attachment://avatar.png")
helpEmbed.set_thumbnail(url="attachment://thumbnail.png")
helpEmbed.add_field(name="speak <words>", value="Jerma joins the channel and says what you input in the command using voice.exe .", inline=True)
helpEmbed.add_field(name="speakdrunk <stuff>", value="Same as speak but more like a streamer during a Labo build.", inline=True)
helpEmbed.add_field(name="adderall <things>", value="Same as speak but more like a streamer during a bitcoin joke.", inline=True)
helpEmbed.add_field(name="play <sound>", value="Jerma joins the channel and plays the sound specified by name. Do not include the file extension. Will only seach in discord-jerma\\sounds.", inline=True)
helpEmbed.add_field(name="birthday <name>", value="Jerma joins the channel and plays a birthday song for the person with the given name.", inline=True)
helpEmbed.add_field(name="join", value=" Jerma joins the channel.", inline=True)
helpEmbed.add_field(name="leave", value="Jerma leaves the channel.", inline=True)
helpEmbed.add_field(name="jermalofi", value="Jerma joins the channel and plays some rats lofi.", inline=True)
helpEmbed.set_footer(text="Message @bebenebenebeb#9414 or @fops#1969 with any questions")

sounds = make_sounds_dict()

soundEmbed = Embed(title=" list | all of the sounds in Jerma's directory", description="call these with the prefix to play them in your server, gamer!", color=0x66c3cb)
soundEmbed.set_author(name="Jermabot Help", url="https://www.youtube.com/watch?v=fnbvTOcNFhU", icon_url="attachment://avatar.png")
soundEmbed.set_thumbnail(url="attachment://thumbnail.png")
soundEmbed.add_field(name='Sounds:', value='\n'.join(sounds), inline=True)
soundEmbed.set_footer(text="Message your server owner to get custom sounds added!")


"""Activity options."""

activities = [(ActivityType.listening, 'my heart.'),
              (ActivityType.listening, 'rats lofi'),
              (ActivityType.listening, 'Shigurain.'),
              (ActivityType.listening, 'Kim!'),
              (ActivityType.streaming, 'DARK SOULS 3'),
              (ActivityType.streaming, 'Just Cause 4 for 3DS'),
              (ActivityType.watching, 'chat make fun of me.'),
              (ActivityType.watching, 'GrillMasterxBBQ\'s vids.'),
              (ActivityType.watching, 'the byeahs.'), # comma on last item is good
              ]

def get_rand_activity():
    info = random.choice(activities)
    return Activity(name=info[1], type=info[0])
