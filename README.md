# Jerma985 Bot

<img src="thumbnail.png" width="200" height="200" align="right" />

This bot is inspired by Jerma985 and I promise that it's slightly taller than the real thing. JermaBot is currently geared towards adding sound functionality to servers, but more Jerma-centric commands are planned for the future.

## Setup
Clone this repo then store your bot token in a file called `secret.txt` in the base directory. Custom files should be stored in `discord-jerma\sounds\`. Currently only works on Windows due to reliance on `voice.exe`. Commands that don't use `voice.exe` should work on other platforms (**not tested**).

#### Requirements:
Install the needed python packages by running this command in the repo:
```pip install -r requirements.txt```

You will also need ffmpeg to be on your system path.

## Commands
All commands are prefixed with `$` by default.

* `speak <words>` - Jerma joins the channel and says what you input in the command using `voice.exe`.
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
