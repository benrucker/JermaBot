'''

 A bot made for discord.py 1.0.1 by Ethan Fidler

 client id :: 574750451564806144
 permissions integer :: 8

 https://discordapp.com/oauth2/authorize?client_id=574750451564806144&scope=bot&permissions=8

 Emojis
 tada :: 🎉

 Todo:
 - looping
 - change images in embed from links to attachments
 - add fire weather effect, among other effects
 - When It's done, Take all of the weather based functions and **offload them**

 Other Features:

 - code spelling out words (alpha, bravo, delta)
 - poi
 - tarot card readings
 - get actual weather readings

 '''

import discord
import asyncio

from help import helpEmbed,get_help_files,help_files
from dice import dice
from cards import drawCard

#globals
client = discord.Client()
authorId = 173839815400357888 # fops#1969
currentWeather = '.\\sounds\\rain.wav'
currStatus = discord.Activity(name="the rain | !weather help", type=discord.ActivityType.listening)
source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(currentWeather))
vol = 0.25
currentClient = 0
#attachment images

#functions

def getText(file):
    with open(file, "r") as f:
        lines = f.read()
        return lines.strip()

# weather / audio functions start here

def updateClient(message): #doesn't do anything because the bot can't run voice on multiple servers at the same time due to my incompetence
    VCs = client.voice_clients
    print(message.guild)
    for x in range(len(VCs)):
        print(VCs[x].guild)
        if VCs[x].guild == message.guild:
            return x
    return 0

def updateSource():
    global source
    global currentWeather
    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(currentWeather))

async def setVol(num,message):
    global vol
    global source
    if num > 100 or num < 0:
        await message.add_reaction("❌")
    else:
        vol = num * 0.01
        source.volume = vol
        await message.add_reaction("✔")

async def join(message):
    if message.author.voice is not None:
        await message.author.voice.channel.connect()

async def leave(message):
    await message.guild.voice_client.disconnect()

async def setWeather(weatherIn,message):
    global currentWeather
    global currentClient
    global source

    currentClient = updateClient(message)

    if weatherIn == 'clear':
        currentWeather = 'clear'
        await leave(message)
    else:
        try:
            await join(message)
        except:
            pass
        currentWeather = ".\\sounds\\" + weatherIn
        while 'clear' not in currentWeather:
            updateSource()
            if client.voice_clients[currentClient].is_playing():
                client.voice_clients[currentClient].stop()
            client.voice_clients[currentClient].play(source)
            source.volume = vol
            print('Playing', currentWeather, '| at volume:', source.volume, '| In:',client.voice_clients[currentClient].guild)
            await asyncio.sleep(59)

async def weather(message):

    if " help" in message.content.lower(): #help command, tells user the working commands
        get_help_files()
        await message.author.send(files=help_files,embed=helpEmbed)
        await message.add_reaction("✉")

    elif " rain" in message.content.lower(): # set the weather to rain
        await message.add_reaction("🌧")
        await setWeather("rain.wav",message)

    elif " thunderstorm" in message.content.lower(): # set the weather to a thunderstorm
        await message.add_reaction("⛈")
        await setWeather("storm.wav",message)

    elif " thunder" in message.content.lower(): # set the weather to just thunder
        await message.add_reaction("🌩")
        await setWeather("thunderonly.wav",message)

    elif " fire" in message.content.lower():
        await message.add_reaction("🔥")
        await setWeather("bonfire.wav",message)

    elif " clear" in message.content.lower(): # clears the weather
        await message.add_reaction("☀")
        await setWeather("clear",message)

    else: # error
        await message.add_reaction("❔")

# weather functions end here

async def play(message):
    global currentClient
    currentClient = updateClient(message)
    client.voice_clients[currentClient].play(discord.FFmpegPCMAudio('.\\sounds\\rain.wav'))

@client.event
async def on_ready():
    print(f" {client.user}, ready to sortie")
    await client.change_presence(activity=currStatus)

@client.event
async def on_message(message): # Basically my Main
    print(f"{message.channel}: {message.author}: {message.content}")

    if message.author == client.user:
        return

    if "!join" in message.content.lower(): # bot joins voice channel
        await join(message)

    if "!leave" in message.content.lower(): #bot leaves voice channel
        await leave(message)

    if "!drip" in message.content.lower(): # simple ping
        await message.channel.send("drop!")

    if "!flip" in message.content.lower(): # simple ping alt
        await message.channel.send("flop!")

    if "!say" in message.content.lower():
        await play(message)

    if "!volume" in message.content.lower():
        msg = int(message.content.strip("!volume").strip())
        await setVol(msg,message)

    if "!bye" in message.content.lower(): # makes bot go offline
        if message.author.id == authorId:
            await message.add_reaction("👋")
            await client.close()

    if "!r" in message.content.lower():
        await message.add_reaction("🎲")
        await message.channel.send(dice(message.content.strip("!r")))

    if "!domt" in message.content.lower():
        await message.add_reaction("🃏")
        card_file = discord.File(".\\images\\avdol.jpg", filename="avdol.jpg")
        await message.channel.send(file=card_file,embed=drawCard())

    if "!weather" in message.content.lower(): # weather commands (plays into an internal function for simplicity)
        await weather(message)

    elif "!toggledownfall" in message.content.lower(): #toggles between 'clear' and 'rain'
        if currentWeather == 'clear':
            await setWeather('rain',message)
            await message.add_reaction("🌧")
        else:
            await setWeather('clear',message)
            await message.add_reaction("☀")

    if "nigger" in message.content.lower():
        await message.add_reaction('👀')
        await message.channel.send("Silence, white person")
        await message.channel.send(file=discord.File('.\\images\\thenperish.jpg'))


client.run(getText("token.txt"))
