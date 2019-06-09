import discord

helpEmbed=discord.Embed(title=" help | list of all useful commands", description="Fueling epic gamer moments since 2011", color=0x66c3cb)
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
