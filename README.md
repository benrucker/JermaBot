# Jerma985 Bot

<img src="jerma/resources/images/thumbnail.png" width="200" height="250" align="right" />

This bot is inspired by Jerma985. I promise that it's slightly taller than the real thing. JermaBot is currently geared towards adding sound functionality to servers, but more Jerma-centric commands are planned for the future.

## Setup

You can add this bot to your server or install it locally.

### Adding JermaBot to your server

Click [this link](https://discordapp.com/api/oauth2/authorize?client_id=579445833938763816&permissions=0&scope=bot) and select your server! Currently, uptime is at 99%, although the `speak` command has been broken to reach this goal (will fix soon I promise).

Note: **some commands will be broken on your server**. Fixes for these commands will be applied in future updates. If you want these commands, you can install JermaBost locally and edit them yourself.

### Installing JermaBot locally

Clone this repo,
```git clone https://github.com/benrucker/discord-jerma.git```

then store your bot token in a file called `secret.txt` in the `discord-jerma/`. The, run the bot by doing
```
cd jerma
python jerma.py
```
or
```
cd jerma
python3 jerma.py
```


#### Requirements:
Install the needed python packages by running this command in the repo:
```pip install -r requirements.txt```

You will also need ffmpeg to be on your system path. If you're on Linux, you might need to install libespeck1

## Commands
All commands are prefixed with `$` by default.

* `speak <words>` - Jerma joins the channel and says what you input.
  * E.g. `$speak What's up guys, Jermabot here.`
* `speakdrunk <stuff>` - Same as `speak` but more like a streamer during a Labo build.
* `adderall <things>` - Same as `speak` but more like a streamer during a bitcoin joke.
* `play <sound>` - Jerma joins the channel and plays the sound specified by name. Do not include the file extension. Will only seach in `discord-jerma\sounds`.
* `birthday <name>` - Jerma joins the channel and plays a birthday song for the person with the given name.
* `join` - Jerma joins the channel.
* `leave` - Jerma leaves the channel.
* `jermalofi` - Jerma joins the channel and plays some rats lofi.
* `help` - Lists all commands available to users.
* `stop` - Stops audio from playing, if any.
* `jermahelp` - Sends a DM to the command caller detailing commands.
* `list` - Lists the sounds available in the `play` command.
* `volume` - Change the volume for sounds to be played at.

Good luck, gamer!
