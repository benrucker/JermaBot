# JermaBot

<img src="jerma/resources/images/thumbnail.png" width="200" height="250" align="right" />

[![GPLv3 License](https://img.shields.io/badge/License-GPL%20v3-yellow.svg)](LICENSE)

JermaBot is a Discord bot inspired by Jerma985. It is a 24/7 general-purpose bot with an emphasis on adding sound functionality to your server.

## Feature Highlights

- **Text-To-Speech** - For those who cannot talk in voice, JermaBot offers TTS. Just use the `speak` command.
- **Sound File Management** - Add a sound file to JermaBot using `addsound` and you can play it back in a voice channel at any time with `play`.
- **Join Sounds** - A grand entrance is a good entrance, so if a sound's name matches your username, JermaBot will play it automatically when you join a voice channel.
- **Fun** - A plethora of random commands are at your disposal, such as `downsmash`, `jermalofi`, and `drake`.

# Setup

You can add this bot to your server or install it locally.

## Adding JermaBot to your server (recommended)

> [**Click here to invite JermaBot to your server!**](https://discord.com/api/oauth2/authorize?client_id=579445833938763816&permissions=412404181056&scope=bot%20applications.commands)

Inviting Jerma to your server is the easiest way to get to use him! I recommend this over installing him locally.

Note: **some commands you might see in the code are guild-specfic**. Send `$help` to see what commands you can call.

## Installing JermaBot locally

### Setup:

1. Install FFMPEG
   - `ffmpeg` needs to be on your system path.
2. Set up TTS (Required only for `speak`)
   - If you want to use `speak`, you will need [voice.exe](https://www.elifulkerson.com/projects/commandline-text-to-speech.php) if you're on Windows, or `libespeak1` if you're on Linux.
   - The official JermaBot instance uses [Mycroft Mimic](https://github.com/mycroftai/mimic1/) as the TTS engine.
     1. Follow the mimic build directions
        - (You will need to run `./configure CFLAGS="-g -O2 -Wno-error=discarded-qualifiers"`)
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
git clone https://github.com/benrucker/JermaBot.git
```

5. `cd` into the repo with this command:

```
cd JermaBot
```

6. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then install the needed Python packages by pasting this in the command line:

```
uv sync
```

7. Create a file called `.env` in the folder `jerma/` holding your bot's token:

```
DISCORD_TOKEN=...
```

The bot loads `.env` on startup, so no environment configuration is needed in your shell profile or systemd unit. Variables already present in the process environment take precedence over the file.

#### (Optional) Coding agent setup

JermaBot includes a minimal agent harness because it seems fun! These instructions set that up, but are not required for the rest of the bot to function.

8. Install the [Claude Code CLI](https://code.claude.com/docs/en/quickstart)
9. Install the [GitHub CLI](https://github.com/cli/cli#installation) (`gh`)
10. Add the agent's credentials to `jerma/.env`:

```
# From `claude setup-token` (skippable on a dev machine already logged in to `claude`)
CLAUDE_CODE_OAUTH_TOKEN=...
# A fine-grained personal access token: read/write on Contents and Pull
# requests for the target repos, plus read/write on Contents for the
# backup repo below.
# https://github.com/settings/personal-access-tokens/new
GITHUB_TOKEN=...
# A private repo, as `owner/name` (not a URL or a path), that the agent
# backs conversation transcripts and identity records up to, so a
# conversation survives losing this host. Create it empty. It must be
# private: a transcript holds everything the agent read.
JERMABOT_AGENT_BACKUP_REPO=owner/name
```

Without `JERMABOT_AGENT_BACKUP_REPO` the bot still runs and says so at startup, but a conversation is then only as durable as this machine. If the repo is set and can't be cloned, that's reported at startup instead of taking the bot down: conversations that never used the backup keep running from their local transcripts, and ones that did refuse to run until it's reachable rather than answering from a frozen copy.

The agent's directories can be moved, but the defaults are usually fine:

```
# Pristine clones of the target repos
JERMABOT_AGENT_REPOS_DIR=~/jermabot-agent/repos
# Per-conversation checkouts and the conversation state file
JERMABOT_AGENT_CONVERSATIONS_DIR=~/jermabot-agent/conversations
# Local clone of the backup repo
JERMABOT_AGENT_BACKUP_DIR=~/jermabot-agent/backup
```

##### How conversations persist

Ping the bot with a request and it replies in a thread; every later message the owner posts in that thread continues the same conversation, no ping needed, forever. Nothing expires one:

- Agent threads are recognized from Discord itself, not from local state, so a thread keeps working across restarts, deleted checkouts, and a wiped host. Archived threads count — posting in one revives it.
- With the backup repo set, each turn's transcript is written straight to it, and that's what the next turn resumes from, so the agent still remembers its own tool calls and file reads years later.
- A conversation keeps one branch and at most one pull request per repo for its whole life. A recovered turn continues them, and the branch is caught up with its base branch before every turn so the PR stays mergeable. A branch GitHub has merged or deleted starts over, and the thread is told why.
- With no transcript left — a conversation from before the backup existed, or one whose backup is gone — the history is rebuilt from the thread's own messages and images instead. That's lossy, and the thread says so.
- Messages posted while the bot was offline aren't lost: once it reconnects, a background job finds agent threads on Discord and runs whatever the owner said that never got an answer.

Muted subtext lines (`-# _..._`) in a thread are the harness talking, never the agent:

- `Reloading thread history. Some context might be lost.` — no transcript was available, so this turn's context came from the thread.
- `Couldn't merge <base> into this branch: ...` or `Couldn't catch <repo> up: ...` — the turn ran on a stale branch. Resolve it on GitHub and the next turn picks the fix up.
- `The pull request for <repo> was merged/was closed/is gone from GitHub; starting a fresh branch ...` — GitHub is done with the old branch, so the next edit opens a new PR.
- `Backup not pushed to GitHub: ...`, `Part of this turn is missing from the transcript backup: ...`, `This conversation's identity record was not updated: ...` — the answer and its pull request stand, but the durable copy is behind, so it's worth fixing before the next turn needs it.
- `Couldn't fetch the image link ...` — an image in the message couldn't be downloaded, so the agent didn't see it.

### Running JermaBot:

Run the bot through the command line like this:

```
cd jerma
uv run main.py [ (-mycroft MYCROFT_PATH | -voice VOICE_PATH | -espeak),
                 (-mv MYCROFT_VOICE)]
               [-jd JTALK_PATH]
               [-jv JAPANESE_VOICE]
```

- `-mycroft`, `-voice`, `-espeak`: You **must** include _one_ of these flags on startup to specify which tts engine Jerma will use. If you do mycroft or voice, you must also include the path to the mimic.exe or voice.exe after the flag.
  - e.g. `-mycroft tts/mimic.exe`
- `-mv PATH_TO_VOICE` allows you to specify which voice you want to use with mycroft mimic. This flag is optional.

### Running the tests:

pytest lives in the `dev` dependency group, so run it through uv:

```
uv run --group dev pytest
```

# Usage

Once JermaBot has joined your server, send `$help` to see a list of commands. Do `$help <command>` to see more details about a certain command (e.g. `$help speak`).
