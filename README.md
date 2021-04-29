# JermaBot

<img src="jerma/resources/images/thumbnail.png" width="200" height="250" align="right" />

[![Maintainability](https://api.codeclimate.com/v1/badges/11bccccb374395ea9f7d/maintainability)](https://codeclimate.com/github/benrucker/discord-jerma/maintainability)
[![GPLv3 License](https://img.shields.io/badge/License-GPL%20v3-yellow.svg)](LICENSE)

This bot is inspired by Jerma985! I promise that it's slightly taller than the real thing. JermaBot is currently geared towards adding sound functionality to servers, but more Jerma-centric commands are planned for the future.

# Setup

You can add this bot to your server (recommended) or install it locally.

## Adding JermaBot to your server

Inviting Jerma to your server is the easiest way to get to use him! I recommend this over installing him locally. Click [this link](https://discordapp.com/api/oauth2/authorize?client_id=579445833938763816&permissions=120859712&scope=bot) and select your server! Currently, uptime is at 99%.

Note: **some commands you might see in the code are guild-specfic**. Send `$help` to see what commands you can call.

## Installing JermaBot locally

### Setup:
Install the needed python packages by running this command in the repo:
```pip install -r requirements.txt```

You will also need ffmpeg to be on your system path. If you want to use TTS features, you will need [voice.exe](https://www.elifulkerson.com/projects/commandline-text-to-speech.php) if you're on Windows, or `libespeak1` if you're on Linux.

If you want to use the `speakanime` command, you will need the `espeak` command to be on your path (regardless of platform).

Clone this repo,
```git clone https://github.com/benrucker/discord-jerma.git```

Then, store your bot token in a file called `secret.txt` in the folder `discord-jerma/`.

### Running JermaBot:

Run the bot through the command line like this:
```
cd jerma
python jerma.py [-s SECRET_FILENAME]
                [ (-mycroft MYCROFT_PATH | -voice VOICE_PATH | -espeak),
                  (-mv MYCROFT_VOICE)]
                [-jd JTALK_PATH]
                [-jv JAPANESE_VOICE]
```
* `-s` allows you to specify a specific text file that your bot token is stored in. If not included, Jerma will look for your token in a file called `secret.txt` in the base dirctory. This flag is optional.
* `-mycroft`, `-voice`, `-espeak`: You **must** include _one_ of these flags on startup to specify which tts engine Jerma will use. If you do mycroft or voice, you must also include the path to the mimic.exe or voice.exe after the flag.
    * e.g. `-mycroft tts/mimic.exe`
* `-mv PATH_TO_VOICE` allows you to specify which voice you want to use with mycroft mimic. This flag is optional.


On Linux, use `python3` instead of `python`.

# Notable Commands
All commands are prefixed with `$` by default.

* `speak <words>` - Jerma joins the channel and says what you input.
  * E.g. `$speak What's up guys, Jermabot here.`
* `speakdrunk <stuff>` - Same as `speak` but more like a streamer during a Labo build.
* `adderall <things>` - Same as `speak` but more like a streamer during a bitcoin joke.
* `speakanime <thingz>` - Same as `speak` but with more anime.
* `play <sound>` - Jerma joins the channel and plays the sound specified by name. Do not include the file extension. Will only seach in `discord-jerma\sounds`.
  * NOTE: If a sound is named the same as a member's username, that sound will play each time they join the voice call.
* `birthday <name>` - Jerma joins the channel and plays a birthday song for the person with the given name.
* `join` - Jerma joins the channel.
* `leave` - Jerma leaves the channel.
* `jermalofi` - Jerma joins the channel and plays some rats lofi.
* `help` - Lists all commands available to users.
* `stop` - Stops audio from playing, if any.
* `jermahelp` - Sends a DM to the command caller detailing commands.
* `help` - Autogenerate a list of all commands available to you.
* `list` - Lists the sounds available in the `play` command.
* `volume` - Change the volume for sounds to be played at.

Good luck, gamer!
