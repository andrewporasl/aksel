# aksel

Aksel is small Discord bot for adding clean meme-style caption bars to images, GIFs, and videos. It also turns still images into simple animated GIF effects.

The bot uses `discord.py`, `Pillow`, `emoji`, `aiohttp`, and `ffmpeg`.

## Features

- `/caption` adds a top caption bar to your most recent image, GIF, video, embed, or direct media URL.
- `/image_to_gif` turns your most recent still image into a short GIF effect.
- `/dark` toggles your personal caption style between white and black caption bars.
- Media detection works from uploads, Discord embeds, Tenor/Giphy embeds, and direct media links.
- Captions support manual line breaks with `\n`, Unicode emoji, Discord custom emoji, and `:shortcode:` emoji names.

## Supported Media

| Media | Formats |
| --- | --- |
| Images | `.jpg`, `.jpeg`, `.png`, `.webp` |
| GIFs | `.gif`, animated `.webp` |
| Videos | `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm` |
| Embeds | Tenor, Giphy, image/video embeds |

## Commands

### `/caption`

Adds a caption to your last posted media.

1. Send an image, GIF, video, embed, or direct media URL in Discord.
2. Run `/caption`.
3. Choose `White background` or `Black background`.
4. Enter the caption text.

Use `\n` inside the caption for a manual line break:

```text
first line\nsecond line
```

### `/image_to_gif`

Turns your last still image into an animated GIF.

Available effects:

- `Fade in from black`
- `Fade in from white`
- `Slow zoom out`
- `Pulse`
- `Blur reveal`

### `/dark`

Toggles your default caption style for the current bot session.

This setting is stored in memory only. It resets when the bot restarts.

## Requirements

- Python 3.10 or newer
- ffmpeg installed and available on your `PATH`
- A Discord bot token

Install ffmpeg:

```bash
# macOS
brew install ffmpeg

# Debian/Ubuntu
sudo apt install ffmpeg
```

Windows users can install ffmpeg from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add the `bin` folder to `PATH`.

Check ffmpeg:

```bash
ffmpeg -version
```

## Setup

### macOS, Linux, WSL, or Git Bash

```bash
bash setup.sh
```

Edit `.env` and replace the placeholder:

```env
DISCORD_TOKEN=your_real_bot_token_here
```

Launch the bot:

```bash
bash launch.sh
```

### Windows

Run:

```bat
setup.bat
```

Edit `.env` and replace the placeholder:

```env
DISCORD_TOKEN=your_real_bot_token_here
```

Launch the bot:

```bat
launch.bat
```

## Manual Setup

If you do not want to use the scripts:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate with:

```bat
.venv\Scripts\activate.bat
```

Then edit `.env` and run:

```bash
python bot.py
```

## Create The Discord App

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click `New Application`.
3. Give it a name, then open the `Bot` page.
4. Click `Reset Token` and copy the token into your local `.env` file.
5. Under `Privileged Gateway Intents`, enable `Message Content Intent`.
6. Open `OAuth2` -> `URL Generator`.
7. Select these scopes:
   - `bot`
   - `applications.commands`
8. Select these bot permissions:
   - `View Channels`
   - `Send Messages`
   - `Attach Files`
   - `Read Message History`
9. Open the generated invite URL and add the bot to your server.

Permission integer for the listed bot permissions: `101376`.

The bot needs `Message Content Intent` because it watches recent messages for media uploads, embeds, and direct media URLs. It needs `Read Message History` so slash commands can find your recent media if the bot missed the original message event.

## How It Works

The bot remembers the most recent supported media for each user in each server. When a user runs a command, it downloads that media into a temporary folder, processes it, sends the result back to Discord, and deletes the temporary files automatically.

Static images are captioned with Pillow. Videos use ffmpeg with a rendered caption overlay. Animated GIFs and animated WebP files are processed frame-by-frame with Pillow. Tenor and Giphy embeds are handled as GIF output when Discord provides a video-style embed.

## Project Files

```text
aksel/
├── bot.py
├── requirements.txt
├── .env.example
├── .gitignore
├── setup.sh
├── launch.sh
├── setup.bat
├── launch.bat
└── README.md
```

Your local `.env` file is intentionally ignored by Git.

## Public GitHub Safety

Never commit a real Discord bot token. Keep real secrets only in `.env`, hosting-provider environment variables, or another secret manager.

If a token is ever pasted into code, chat, logs, screenshots, or a public repo, treat it as compromised:

1. Open the Discord Developer Portal.
2. Select your application.
3. Go to `Bot`.
4. Click `Reset Token`.
5. Put the new token in your local `.env`.

This repo should include `.env.example`, but not a real `.env` with a real token.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `Missing DISCORD_TOKEN` | Add your real bot token to `.env`. |
| `Improper token has been passed` | Reset the bot token in Discord and update `.env`. |
| Slash commands do not appear | Wait for global command sync, or restart the bot and check the terminal. |
| Bot cannot find media | Send media first, then run the command in the same channel. |
| `ffmpeg` not found | Install ffmpeg and add it to `PATH`. |
| Upload failed because file is too large | Discord rejected the output size. Try smaller or shorter media. |
| Bot is offline | Keep the launch script running and check the terminal for errors. |
