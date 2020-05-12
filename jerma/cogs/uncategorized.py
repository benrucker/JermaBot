from colorama import Fore as t
from colorama import Back, Style
from discord.ext import commands
import discord
import time
import os
from discord.embeds import Embed
import random
from discord.activity import Activity
from discord.enums import ActivityType
import traceback
import sys


h = Embed(title=" help | list of all useful commands", description="Fueling epic gamer moments since 2011", color=0x66c3cb)
h.set_author(name="Jermabot Help", url="https://www.youtube.com/watch?v=fnbvTOcNFhU", icon_url="attachment://avatar.png")
h.set_thumbnail(url="attachment://thumbnail.png")
h.add_field(name="speak <words>", 
            value="Jerma joins the channel and says what you input in the command using voice.exe .", inline=True)
h.add_field(name="speakdrunk <stuff>",
            value="Same as speak but more like a streamer during a Labo build.", inline=True)
h.add_field(name="adderall <things>", 
            value="Same as speak but more like a streamer during a bitcoin joke.", inline=True)
h.add_field(name="play <sound>", 
            value="Jerma joins the channel and plays the sound specified by name.", inline=True)
h.add_field(name="birthday <name>", 
            value="Jerma joins the channel and plays a birthday song for the person with the given name.", inline=True)
h.add_field(name="join", value=" Jerma joins the channel.", inline=True)
h.add_field(name="leave", value="Jerma leaves the channel.", inline=True)
h.add_field(name="jermalofi", value="Jerma joins the channel and plays some rats lofi.", inline=True)
h.set_footer(text="Message @bebenebenebeb#9414 or @fops#1969 with any questions")


def setup(bot):
    bot.add_cog(Uncategorized(bot))


class Uncategorized(commands.Cog):
    def __init__(self, bot):
       self.bot = bot
       self.help_embed = h

    @commands.command()
    async def jermahelp(self, ctx):
        """Send the user important info about JermaBot."""
        avatar = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='avatar.png')
        thumbnail = discord.File(os.path.join('resources', 'images', 'avatar.png'), filename='thumbnail.png')
        await ctx.author.send(files=[avatar, thumbnail], embed=self.help_embed)
        await ctx.message.add_reaction("✉")

