# JermaBot

<img src="jerma/resources/images/thumbnail.png" width="200" height="250" align="right" />

[![Maintainability](https://api.codeclimate.com/v1/badges/11bccccb374395ea9f7d/maintainability)](https://codeclimate.com/github/benrucker/discord-jerma/maintainability)
[![GPLv3 License](https://img.shields.io/badge/License-GPL%20v3-yellow.svg)](LICENSE)

JermaBot is a Discord bot inspired by Jerma985. It is a 24/7 general-purpose bot with an emphasis on adding sound functionality to your server.

## Feature Highlights

* **Text-To-Speech** - For those who cannot talk in voice, JermaBot offers TTS. Just use the `speak` command.
* **Sound File Management** - Add a sound file to JermaBot using `addsound` and you can play it back in a voice channel at any time with `play`.
* **Join Sounds** - A grand entrance is a good entrance, so if a sound's name matches your username, JermaBot will play it automatically when you join a voice channel.
* **Fun** - A plethora of random commands are at your disposal, such as `downsmash`, `jermalofi`, and `drake`.

# Setup

You can add this bot to your server or install it locally.

## Adding JermaBot to your server (recommended)

> [**Click here to invite JermaBot to your server!**](https://discord.com/api/oauth2/authorize?client_id=579445833938763816&permissions=412404181056&scope=bot%20applications.commands)

Inviting Jerma to your server is the easiest way to get to use him! I recommend this over installing him locally.

Note: **some commands you might see in the code are guild-specfic**. Send `$help` to see what commands you can call.

## Installing JermaBot locally

### Setup:

1. Install FFMPEG
    * `ffmpeg` needs to be on your system path.
2. Set up TTS (Required only for `speak`)
    * If you want to use `speak`, you will need [voice.exe](https://www.elifulkerson.com/projects/commandline-text-to-speech.php) if you're on Windows, or `libespeak1` if you're on Linux.
    * The official JermaBot instance uses [Mycroft Mimic](https://mimic.mycroft.ai/) as the TTS engine.
      1. Follow the mimic install directions
      2. Substitute `https://sourceforge.net/projects/pcre/files/pcre2/10.23/pcre2-10.23.zip` for the `ftp` link in `mimic1/dependency.sh`
      3. Add the path to `mimic` to the launch flags
3. Set up Japanese TTS (Required only for `speakanime`)
    1. Install `espeak` from [here](http://espeak.sourceforge.net/), if you're on Windows, or run `sudo apt install espeak` on Linux.
    2. Make sure that `espeak` is on your system path.
    3. Install `open_jtalk` with `sudo apt install open-jtalk`.
    4. Download the voice "Mei" from [here](https://sourceforge.net/projects/mmdagent/files/MMDAgent_Example/MMDAgent_Example-1.8/MMDAgent_Example-1.8.zip).
    5. Put the "Mei" voice files somewhere safe, you will need to add that path to JermaBot's startup parameters.
4. Clone the repo by pasting this in the command line:

```
git clone https://github.com/benrucker/discord-jerma.git
```

5. `cd` into the repo with this command:

```
cd discord-jerma
```

6. Install the needed Python packages by pasting this in the command line:

```
pip install -r requirements.txt
```

7. Store your bot's token in a file called `secret.txt` in the folder `discord-jerma/`.

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

# Usage

Once JermaBot has joined your server, send `$help` to see a list of commands. Do `$help <command>` to see more details about a certain command (e.g. `$help speak`).
