# Jerma985 Bot
Introduce some real gamer moments to your Discord server. This bot is inspired by Jerma985 and I promise that it's slightly taller than the real thing.

## Setup
Clone this repo then store your bot token in a file called `secret.txt` in the base directory. Custom files should be stored in `discord-jerma\sounds\`. Currently only works on Windows due to reliance on `voice.exe`. Commands that don't use `voice.exe` should work on other platforms (**not tested**).

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


### WIP Commands
These commands are not guaranteed to work.

* `jermasnap` - Jerma joins the server and moves half the server to a different channel. 1/4 chance of Jerma following the snapped and playing bass boosted Moonlight to rub salt in the wound. Currently hard coded for a specific server, so it will not work on other servers.
* `testsnap` - Same as above but doesn't move anyone.
* `loopaudio <filepath>` - Plays an audio file forever. Send command `leave` to make it stop.
* `speakfile <words>` - Uses tts to say the words and sends it in a file to the text channel.
