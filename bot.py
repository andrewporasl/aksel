import discord
from discord import app_commands
import os
import io
import re
import tempfile
import asyncio
import aiohttp
import textwrap
import secrets
import math
from typing import Literal
from PIL import Image, ImageChops, ImageFilter
import emoji as _emoji_lib
from dotenv import load_dotenv

# ── Config ───────────────────────────────────────────────────────────────────
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN or TOKEN == "your_bot_token_here":
    raise RuntimeError("Missing DISCORD_TOKEN. Add your bot token to .env before launching.")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
GIF_EXTENSIONS   = {".gif"}
ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | GIF_EXTENSIONS

MAX_SOURCE_FILE_MB = 5000
EFFECT_ANIMATION_MS = 2000
EFFECT_HOLD_MS = 4000
FADE_ANIMATION_MS = 3500
FADE_HOLD_MS = 6000


# ── Per-user style preference (default: light) ────────────────────────────────
user_styles: dict[int, str] = {}

def get_style(user_id: int) -> str:
    return user_styles.get(user_id, "light")

def toggle_style(user_id: int) -> str:
    current = get_style(user_id)
    new = "dark" if current == "light" else "light"
    user_styles[user_id] = new
    return new

# ── Per-user last media ───────────────────────────────────────────────────────
# (guild_id, user_id) -> (url, filename, force_gif)
last_media: dict[tuple[int, int], tuple[str, str, bool]] = {}


def media_key(guild_id: int | None, user_id: int) -> tuple[int, int]:
    return (guild_id or 0, user_id)


def remember_media(guild_id: int | None, user_id: int, media: tuple[str, str, bool]):
    last_media[media_key(guild_id, user_id)] = media


def get_remembered_media(guild_id: int | None, user_id: int):
    return last_media.get(media_key(guild_id, user_id))

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


async def prepare_interaction_send(interaction: discord.Interaction, status_message: str | None = None):
    """
    Prefer interaction followups, but fall back to a normal channel message if
    the interaction token has already expired.
    """
    use_followup = False

    try:
        if not interaction.response.is_done():
            if status_message is not None:
                await interaction.response.send_message(status_message)
            else:
                await interaction.response.defer(thinking=True)
        use_followup = True
    except discord.NotFound:
        print(f"[warn] Interaction expired before initial response for /{interaction.command.name if interaction.command else 'unknown'}")
        use_followup = False

    async def send(content=None, **kwargs):
        try:
            if use_followup:
                await interaction.followup.send(content, **kwargs)
                return

            channel = interaction.channel
            if channel is None:
                raise RuntimeError("Interaction channel is unavailable.")

            kwargs.pop("ephemeral", None)

            if content is None:
                content = interaction.user.mention
            elif content:
                content = f"{interaction.user.mention} {content}"
            else:
                content = interaction.user.mention

            await channel.send(content, **kwargs)
        except discord.HTTPException as exc:
            print(f"[upload] status={exc.status} code={exc.code} text={exc.text}")
            if exc.code == 40005:
                fallback = "❌ Upload failed: Discord says the file is too large."
            else:
                fallback = f"❌ Upload failed ({exc.status}/{exc.code})."
            if use_followup:
                await interaction.followup.send(fallback, ephemeral=True)
                return

            channel = interaction.channel
            if channel is None:
                raise RuntimeError("Interaction channel is unavailable.")
            await channel.send(f"{interaction.user.mention} {fallback}")

    return send


# ── ffprobe: get media dimensions ─────────────────────────────────────────────
async def get_dimensions(input_path: str) -> tuple[int, int]:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        input_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    parts = stdout.decode().strip().split(",")
    return int(parts[0]), int(parts[1])


import platform

def find_bold_font() -> str:
    """Return path to a bold font that actually exists on this system."""
    system = platform.system()
    candidates = {
        "Windows": [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
            "C:/Windows/Fonts/verdanab.ttf",
        ],
        "Darwin": [
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ],
    }.get(system, [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ])
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""  # ffmpeg default fallback

BOLD_FONT = find_bold_font()


def escape_fontfile(path: str) -> str:
    """Escape a font path for use inside an ffmpeg filter string."""
    return path.replace("\\", "/").replace(":", "\\:")


# ── Caption layout calculator ─────────────────────────────────────────────────
def calc_caption(text: str, media_width: int):
    """
    Splits on manual \\n first, then wraps each segment.
    Font stays large; bar expands to fit.
    """
    side_pad  = max(6, media_width // 60)
    usable_w  = media_width - side_pad * 2
    base_font = max(40, media_width // 7)
    min_font  = max(20, media_width // 20)

    segments = text.split("\n")  # respect manual line breaks

    for fontsize in range(base_font, min_font - 1, -4):
        chars_per_line = max(1, int(usable_w / (fontsize * 0.58)))
        lines = []
        for seg in segments:
            lines.extend(textwrap.wrap(seg, width=chars_per_line) or [seg])
        if len(lines) <= 8:
            line_h     = fontsize
            v_pad      = fontsize // 2
            bar_height = len(lines) * line_h + v_pad * 2
            return fontsize, bar_height, lines, v_pad, line_h

    fontsize       = min_font
    chars_per_line = max(1, int(usable_w / (fontsize * 0.58)))
    lines = []
    for seg in segments:
        lines.extend(textwrap.wrap(seg, width=chars_per_line) or [seg])
    line_h     = fontsize
    v_pad      = fontsize // 2
    bar_height = len(lines) * line_h + v_pad * 2
    return fontsize, bar_height, lines, v_pad, line_h


# ── Build ffmpeg filter ───────────────────────────────────────────────────────
def build_filter(text: str, media_width: int, style: str = "light") -> str:
    fontsize, bar_height, lines, v_pad, line_h = calc_caption(text, media_width)

    bg_color   = "white" if style == "light" else "black"
    font_color = "black" if style == "light" else "white"
    font_arg   = f":fontfile='{escape_fontfile(BOLD_FONT)}'" if BOLD_FONT else ""

    vf = f"pad=width=iw:height=ih+{bar_height}:x=0:y={bar_height}:color={bg_color}"

    for i, line in enumerate(lines):
        safe = (
            line.replace("\\", "\\\\")
                .replace("'",  "\u2019")   # ' → curly apostrophe
                .replace('"',  "\u201c")   # fallback if any straight quotes remain
                .replace(":",  "\\:")
                .replace("%",  "\\%")
                .replace("[",  "\\[")
                .replace("]",  "\\]")
        )
        y = v_pad + i * line_h   # pure Python — no ffmpeg variable math, no overlap
        vf += (
            f",drawtext=text='{safe}'"
            f":fontcolor={font_color}"
            f":fontsize={fontsize}"
            f"{font_arg}"
            f":x=(w-text_w)/2"
            f":y={y}"
        )

    return vf


# ── ffmpeg runner ─────────────────────────────────────────────────────────────
async def run_ffmpeg(cmd: list) -> bool:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        print("ffmpeg error:", stderr.decode())
        return False
    return True


# ── Emoji parsing ────────────────────────────────────────────────────────────
EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")


def convert_shortcodes(text: str) -> str:
    """Convert :joy: → 😂 etc. Uses emoji library aliases."""
    try:
        return _emoji_lib.emojize(text, language="alias")
    except Exception:
        try:
            return _emoji_lib.emojize(text)
        except Exception:
            return text


def unicode_to_twemoji_codepoint(emoji_char: str) -> str:
    """Convert a Unicode emoji to its Twemoji filename codepoint."""
    # Strip variation selectors (FE0F) which Twemoji omits
    return "-".join(f"{ord(c):x}" for c in emoji_char if c != "\ufe0f")


async def fetch_emoji_images(text: str) -> dict:
    """
    Download all emoji references in text:
    - Discord custom: <:name:id> or <a:name:id>
    - Unicode emojis (via Twemoji CDN)
    Returns {emoji_string: PIL.Image}.
    """
    cache = {}

    async with aiohttp.ClientSession() as session:

        async def fetch_image(url):
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.read()
                img = Image.open(io.BytesIO(data))
                if getattr(img, "is_animated", False):
                    img.seek(0)
                return img.convert("RGBA")
            except Exception as e:
                print(f"[emoji] {url}: {e}")
                return None

        # 1) Discord custom emojis
        for m in EMOJI_RE.finditer(text):
            key = m.group(0)
            if key in cache:
                continue
            animated, name, eid = m.groups()
            ext = "gif" if animated else "png"
            img = await fetch_image(f"https://cdn.discordapp.com/emojis/{eid}.{ext}")
            if img:
                cache[key] = img

        # 2) Unicode emojis via Twemoji
        for em in _emoji_lib.emoji_list(text):
            char = em["emoji"]
            if char in cache:
                continue
            codepoint = unicode_to_twemoji_codepoint(char)
            img = await fetch_image(
                f"https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/{codepoint}.png"
            )
            if img:
                cache[char] = img

    return cache


def parse_line_parts(line: str, emoji_cache: dict):
    """Split a line into ordered [('text', str)] and [('emoji', PIL.Image)] segments."""
    # Gather positions of all emoji occurrences
    items = []  # (start, end, key)
    for m in EMOJI_RE.finditer(line):
        items.append((m.start(), m.end(), m.group(0)))
    for em in _emoji_lib.emoji_list(line):
        items.append((em["match_start"], em["match_end"], em["emoji"]))

    items.sort()

    parts = []
    last = 0
    for start, end, key in items:
        if start < last:
            continue  # overlap, skip
        if start > last:
            parts.append(("text", line[last:start]))
        if key in emoji_cache:
            parts.append(("emoji", emoji_cache[key]))
        else:
            parts.append(("text", line[start:end]))
        last = end
    if last < len(line):
        parts.append(("text", line[last:]))
    return parts


# ── Caption bar renderer ─────────────────────────────────────────────────────
def resize_rgba_preserve_color(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """
    Resize RGBA images using alpha-premultiplied scaling so semi-transparent edge
    pixels keep their original color instead of getting muddied by transparency.
    """
    src = img.convert("RGBA")
    r, g, b, a = src.split()
    premult = Image.merge(
        "RGBA",
        (
            ImageChops.multiply(r, a),
            ImageChops.multiply(g, a),
            ImageChops.multiply(b, a),
            a,
        ),
    ).resize(size, Image.LANCZOS)

    pr, pg, pb, pa = premult.split()
    pixels = []
    for rv, gv, bv, av in zip(pr.getdata(), pg.getdata(), pb.getdata(), pa.getdata()):
        if av:
            pixels.append(
                (
                    min(255, (rv * 255 + av // 2) // av),
                    min(255, (gv * 255 + av // 2) // av),
                    min(255, (bv * 255 + av // 2) // av),
                    av,
                )
            )
        else:
            pixels.append((0, 0, 0, 0))

    resized = Image.new("RGBA", size)
    resized.putdata(pixels)
    return resized


def render_caption_bar(text: str, media_width: int, style: str, emoji_cache: dict):
    """Returns (PIL.Image of the caption bar, bar_height)."""
    from PIL import ImageFont, ImageDraw

    fontsize, bar_height, lines, v_pad, line_h = calc_caption(text, media_width)

    try:
        font = ImageFont.truetype(BOLD_FONT, fontsize) if BOLD_FONT else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    bg_color   = (255, 255, 255, 255) if style == "light" else (0,   0,   0,   255)
    text_color = (0,   0,   0)        if style == "light" else (255, 255, 255)

    canvas = Image.new("RGBA", (media_width, bar_height), bg_color)
    draw = ImageDraw.Draw(canvas)

    emoji_size = int(fontsize * 0.95)

    for i, line in enumerate(lines):
        parts = parse_line_parts(line, emoji_cache)

        # Measure total line width
        total_w = 0
        for kind, content in parts:
            if kind == "text":
                total_w += int(draw.textlength(content, font=font))
            else:
                total_w += emoji_size

        x = (media_width - total_w) // 2
        y = v_pad + i * line_h

        for kind, content in parts:
            if kind == "text":
                draw.text((x, y), content, fill=text_color, font=font)
                x += int(draw.textlength(content, font=font))
            else:
                emoji_img = resize_rgba_preserve_color(content, (emoji_size, emoji_size))
                # Slight vertical adjust so emoji baseline matches text
                ey = y + (line_h - emoji_size) // 2
                canvas.paste(emoji_img, (x, ey), emoji_img)
                x += emoji_size

    return canvas, bar_height


# ── Pillow caption (used for animated GIF / WebP) ────────────────────────────
def add_caption_pillow(input_path: str, output_path: str, text: str, style: str, emoji_cache: dict = None) -> bool:
    """Add caption bar to every frame of an animated image. Outputs GIF."""
    if emoji_cache is None:
        emoji_cache = {}
    try:
        img = Image.open(input_path)
        width, height = img.size

        try:
            n_frames = img.n_frames
        except Exception:
            n_frames = 1

        print(f"[debug] frames={n_frames} size={width}x{height} format={img.format}")

        caption_bar, bar_height = render_caption_bar(text, width, style, emoji_cache)
        bg_color = (255, 255, 255) if style == "light" else (0, 0, 0)

        def process_frame(frame_img):
            frame = frame_img.convert("RGBA")
            canvas = Image.new("RGBA", (width, height + bar_height), bg_color + (255,))
            canvas.paste(frame, (0, bar_height), frame)
            canvas.paste(caption_bar, (0, 0), caption_bar)
            return canvas.convert("RGB")

        if n_frames <= 1:
            canvas = process_frame(img)
            canvas.save(output_path.replace(".gif", ".png"), format="PNG")
            return True

        frames, durations = [], []
        for f in range(n_frames):
            img.seek(f)
            frames.append(process_frame(img.copy()))
            durations.append(img.info.get("duration", 80))

        frames[0].save(
            output_path,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            loop=0,
            duration=durations,
            optimize=False,
        )
        return True

    except Exception as e:
        print(f"Pillow error: {e}")
        return False


# ── Static image caption via Pillow (so emojis work) ─────────────────────────
def caption_static_image(input_path: str, output_path: str, text: str, style: str, emoji_cache: dict) -> bool:
    try:
        img = Image.open(input_path).convert("RGBA")
        width, height = img.size

        caption_bar, bar_height = render_caption_bar(text, width, style, emoji_cache)
        bg_color = (255, 255, 255, 255) if style == "light" else (0, 0, 0, 255)

        canvas = Image.new("RGBA", (width, height + bar_height), bg_color)
        canvas.paste(img, (0, bar_height), img)
        canvas.paste(caption_bar, (0, 0), caption_bar)
        canvas.convert("RGB").save(output_path, format="PNG")
        return True
    except Exception as e:
        print(f"Static image error: {e}")
        return False


# ── Video caption via ffmpeg overlay (caption pre-rendered with Pillow) ──────
async def caption_video(input_path: str, output_path: str, text: str, style: str, emoji_cache: dict, as_gif: bool = False) -> bool:
    try:
        width, _ = await get_dimensions(input_path)
        caption_bar, bar_height = render_caption_bar(text, width, style, emoji_cache)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            caption_path = tf.name
            caption_bar.save(caption_path, format="PNG")

        try:
            bg = "white" if style == "light" else "black"
            fc = (
                f"[0:v]pad=width=iw:height=ih+{bar_height}:x=0:y={bar_height}:color={bg}[padded];"
                f"[padded][1:v]overlay=0:0[out]"
            )
            if as_gif:
                fc = (
                    f"[0:v]pad=width=iw:height=ih+{bar_height}:x=0:y={bar_height}:color={bg}[padded];"
                    f"[padded][1:v]overlay=0:0,"
                    f"fps=30,"
                    f"split[s0][s1];"
                    f"[s0]palettegen=stats_mode=diff[p];"
                    f"[s1][p]paletteuse=dither=bayer"
                )
                ok = await run_ffmpeg([
                    "ffmpeg", "-y", "-i", input_path, "-i", caption_path,
                    "-filter_complex", fc,
                    "-loop", "0", output_path,
                ])
            else:
                ok = await run_ffmpeg([
                    "ffmpeg", "-y", "-i", input_path, "-i", caption_path,
                    "-filter_complex", fc,
                    "-map", "[out]", "-map", "0:a?",
                    "-c:a", "copy", "-pix_fmt", "yuv420p",
                    output_path,
                ])
            return ok
        finally:
            if os.path.exists(caption_path):
                os.remove(caption_path)
    except Exception as e:
        print(f"Video caption error: {e}")
        return False


# ── Per-format processing ─────────────────────────────────────────────────────
async def process_file(input_path: str, output_path: str, text: str, ext: str, style: str) -> bool:
    """Handles static images and videos via ffmpeg. Animated is handled separately."""
    width, _ = await get_dimensions(input_path)
    vf = build_filter(text, width, style)

    if ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
        return await run_ffmpeg([
            "ffmpeg", "-y", "-i", input_path,
            "-vf", vf, "-c:a", "copy", "-pix_fmt", "yuv420p", output_path,
        ])
    else:
        # Static image
        return await run_ffmpeg([
            "ffmpeg", "-y", "-i", input_path,
            "-vf", vf, output_path,
        ])


# ── Detect actual media format ───────────────────────────────────────────────
def detect_format(input_path: str, content_type: str = "") -> tuple[str, bool]:
    """
    Returns (extension, is_animated).
    Detects actual file format by reading file bytes + Content-Type header.
    """
    ct = content_type.lower()
    if "video" in ct or ct.endswith(("/mp4", "/webm", "/quicktime")):
        return ".mp4", False

    try:
        img = Image.open(input_path)
        fmt = (img.format or "").upper()
        try:
            n_frames = img.n_frames
        except Exception:
            n_frames = 1

        if n_frames > 1:
            return ".gif", True  # animated → output as GIF
        if fmt == "GIF":
            return ".gif", False
        if fmt == "WEBP":
            return ".png", False
        if fmt in ("JPEG", "JPG"):
            return ".jpg", False
        if fmt == "PNG":
            return ".png", False
        return ".png", False
    except Exception:
        # Not an image — probably video
        return ".mp4", False


# ── Shared caption helper ─────────────────────────────────────────────────────
async def do_caption(send_fn, url: str, filename: str, text: str, style: str, force_gif: bool = False):
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.bin")

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                content_type = resp.headers.get("Content-Type", "")
                with open(input_path, "wb") as f:
                    f.write(await resp.read())

        if os.path.getsize(input_path) > MAX_SOURCE_FILE_MB * 1024 * 1024:
            await send_fn(f"❌ File too large (max {MAX_SOURCE_FILE_MB} MB).")
            return

        ext, is_animated = detect_format(input_path, content_type)
        output_ext = ".gif" if force_gif else ext
        print(f"[debug] ext={ext} animated={is_animated} force_gif={force_gif} → output {output_ext}")

        # Convert :shortcodes: → Unicode emojis (e.g. :joy: → 😂)
        text = convert_shortcodes(text)

        # Pre-download all Discord custom emojis referenced in the text
        emoji_cache = await fetch_emoji_images(text)

        output_path = os.path.join(tmpdir, f"output{output_ext}")

        if is_animated:
            success = await asyncio.get_event_loop().run_in_executor(
                None, add_caption_pillow, input_path, output_path, text, style, emoji_cache
            )
        elif ext == ".mp4" or ext in (".mov", ".avi", ".mkv", ".webm"):
            success = await caption_video(input_path, output_path, text, style, emoji_cache, as_gif=force_gif)
        else:
            # Static image
            output_path = output_path.replace(output_ext, ".png")
            success = await asyncio.get_event_loop().run_in_executor(
                None, caption_static_image, input_path, output_path, text, style, emoji_cache
            )
            output_ext = ".png"

        if not success or not os.path.exists(output_path):
            await send_fn("❌ Processing failed.")
            return

        print(f"[upload] path={output_path} size_bytes={os.path.getsize(output_path)} ext={output_ext}")
        await send_fn("Your captioned media is ready.", file=discord.File(output_path, filename=random_output_filename(output_ext)))


async def do_image_to_gif(send_fn, url: str, effect: str):
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.bin")

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                content_type = resp.headers.get("Content-Type", "")
                with open(input_path, "wb") as f:
                    f.write(await resp.read())

        if os.path.getsize(input_path) > MAX_SOURCE_FILE_MB * 1024 * 1024:
            await send_fn(f"❌ File too large (max {MAX_SOURCE_FILE_MB} MB).")
            return

        ext, is_animated = detect_format(input_path, content_type)
        if ext not in IMAGE_EXTENSIONS or is_animated:
            await send_fn("❌ Send a still image first, then run `/image_to_gif`.")
            return

        output_path = os.path.join(tmpdir, "output.gif")
        success = await asyncio.get_event_loop().run_in_executor(
            None, create_image_effect_gif, input_path, output_path, effect
        )

        if not success or not os.path.exists(output_path):
            await send_fn("❌ GIF conversion failed.")
            return

        print(f"[upload] path={output_path} size_bytes={os.path.getsize(output_path)} ext=.gif")
        await send_fn("Your GIF is ready.", file=discord.File(output_path, filename=random_output_filename(".gif")))


URL_RE = re.compile(r'https?://\S+')


def is_gif_host_url(url: str | None) -> bool:
    if not url:
        return False
    value = url.lower()
    return ("tenor" in value or "giphy" in value)


def random_output_filename(ext: str) -> str:
    return f"{secrets.token_hex(12)}{ext}"


def extract_media_from_message(message: discord.Message):
    for attachment in message.attachments:
        ext = os.path.splitext(attachment.filename)[1].lower()
        if ext in ALLOWED_EXTENSIONS:
            return attachment.url, attachment.filename, False

    for embed in message.embeds:
        url = None
        source_urls = [
            getattr(embed, "url", None),
            embed.video.url if embed.video else None,
            embed.image.url if embed.image else None,
            embed.thumbnail.url if embed.thumbnail else None,
            getattr(embed.provider, "url", None) if embed.provider else None,
            getattr(embed.provider, "name", None) if embed.provider else None,
        ]
        force_gif = embed.type == "gifv" or any(is_gif_host_url(value) for value in source_urls)

        if force_gif and embed.image and embed.image.url:
            url = embed.image.url
        elif embed.type in ("gifv", "video") and embed.video and embed.video.url:
            url = embed.video.url
        elif embed.image and embed.image.url:
            url = embed.image.url
        elif embed.video and embed.video.url:
            url = embed.video.url
        elif embed.thumbnail and embed.thumbnail.url:
            url = embed.thumbnail.url

        if url:
            ext = os.path.splitext(url.split("?")[0])[1].lower()
            return url, f"media{ext or '.bin'}", force_gif

    for url in URL_RE.findall(message.content):
        is_embed_host = ("tenor.com" in url or "giphy.com" in url)
        force_gif = is_embed_host
        ext = os.path.splitext(url.split("?")[0])[1].lower()
        if ext in ALLOWED_EXTENSIONS or is_embed_host:
            return url, f"media{ext or '.bin'}", force_gif

    return None


async def find_recent_user_media(interaction: discord.Interaction, limit: int = 50):
    channel = interaction.channel
    if channel is None or not hasattr(channel, "history"):
        return None

    try:
        async for message in channel.history(limit=limit):
            if message.author.id != interaction.user.id:
                continue
            media = extract_media_from_message(message)
            if media:
                remember_media(interaction.guild.id if interaction.guild else None, interaction.user.id, media)
                return media
    except discord.Forbidden:
        return None

    return None


def fit_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize and crop an image to fully cover the target size."""
    target_w, target_h = size
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    resized = img.resize((max(1, round(src_w * scale)), max(1, round(src_h * scale))), Image.LANCZOS)
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def render_zoom_frame(base: Image.Image, scale: float) -> Image.Image:
    width, height = base.size
    crop_w = max(1, round(width / scale))
    crop_h = max(1, round(height / scale))
    left = max(0, (width - crop_w) // 2)
    top = max(0, (height - crop_h) // 2)
    cropped = base.crop((left, top, left + crop_w, top + crop_h))
    return cropped.resize((width, height), Image.LANCZOS)


def ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


def frame_duration(frame_count: int, total_ms: int = EFFECT_ANIMATION_MS) -> int:
    return max(20, round(total_ms / frame_count))


def append_hold(frames: list[Image.Image], durations: list[int], base: Image.Image, hold_ms: int = EFFECT_HOLD_MS):
    frames.append(base.copy())
    durations.append(hold_ms)


def create_image_effect_gif(input_path: str, output_path: str, effect: str) -> bool:
    """Turn a still image into a short animated GIF using a simple effect preset."""
    try:
        base = Image.open(input_path).convert("RGBA")
        width, height = base.size
        black = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        white = Image.new("RGBA", (width, height), (255, 255, 255, 255))

        frames: list[Image.Image] = []
        durations: list[int] = []

        if effect == "fade_black":
            frame_count = 120
            duration = frame_duration(frame_count, FADE_ANIMATION_MS)
            for i in range(frame_count):
                t = i / (frame_count - 1)
                alpha = ease_in_out(t)
                frames.append(Image.blend(black, base, alpha))
                durations.append(duration)
            append_hold(frames, durations, base, FADE_HOLD_MS)
        elif effect == "fade_white":
            frame_count = 120
            duration = frame_duration(frame_count, FADE_ANIMATION_MS)
            for i in range(frame_count):
                t = i / (frame_count - 1)
                alpha = ease_in_out(t)
                frames.append(Image.blend(white, base, alpha))
                durations.append(duration)
            append_hold(frames, durations, base, FADE_HOLD_MS)
        elif effect == "zoom_in":
            frame_count = 120
            duration = frame_duration(frame_count)
            for i in range(frame_count):
                t = i / (frame_count - 1)
                scale = 1.85 - (0.85 * ease_in_out(t))
                frames.append(render_zoom_frame(base, scale))
                durations.append(duration)
            append_hold(frames, durations, base)
        elif effect == "pulse":
            frame_count = 160
            duration = frame_duration(frame_count)
            for i in range(frame_count):
                t = i / (frame_count - 1)
                scale = 1.0 + (0.065 * (0.5 - 0.5 * math.cos(4 * math.pi * t)))
                frames.append(render_zoom_frame(base, scale))
                durations.append(duration)
            append_hold(frames, durations, base)
        elif effect == "blur":
            frame_count = 80
            duration = frame_duration(frame_count)
            for i in range(frame_count):
                t = i / (frame_count - 1)
                radius = 140 * (1 - ease_in_out(t))
                frames.append(base.filter(ImageFilter.GaussianBlur(radius=radius)))
                durations.append(20)
            append_hold(frames, durations, base)
        else:
            return False

        gif_frames = [frame.convert("P", palette=Image.ADAPTIVE) for frame in frames]
        gif_frames[0].save(
            output_path,
            format="GIF",
            save_all=True,
            append_images=gif_frames[1:],
            loop=0,
            duration=durations,
            optimize=False,
            disposal=2,
        )
        return True
    except Exception as e:
        print(f"Image-to-GIF error: {e}")
        return False

# ── on_message: track last media per user ────────────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    current_guild_id = message.guild.id if message.guild else None

    await asyncio.sleep(1.5)
    try:
        message = await message.channel.fetch_message(message.id)
    except Exception:
        pass

    media = extract_media_from_message(message)
    if media:
        remember_media(current_guild_id, message.author.id, media)


# ── /caption ─────────────────────────────────────────────────────────────────
@tree.command(name="caption", description="Add a caption to your last posted image, video, or GIF")
@app_commands.describe(
    bg="Caption background color.",
    caption="Caption text. Use \\n for manual line breaks.",
)
@app_commands.choices(bg=[
    app_commands.Choice(name="White background", value="white"),
    app_commands.Choice(name="Black background", value="black"),
])
async def caption(interaction: discord.Interaction, bg: Literal["white", "black"], caption: str):
    media = get_remembered_media(interaction.guild.id if interaction.guild else None, interaction.user.id)
    if not media:
        media = await find_recent_user_media(interaction)

    if not media:
        try:
            await interaction.response.send_message(
                "❌ No media found. Send an image, video, or GIF first, then run `/caption`.",
                ephemeral=True,
            )
        except discord.NotFound:
            channel = interaction.channel
            if channel is None:
                raise RuntimeError("Interaction channel is unavailable.")
            await channel.send(f"{interaction.user.mention} ❌ No media found. Send an image, video, or GIF first, then run `/caption`.")
        return

    send_fn = await prepare_interaction_send(interaction, "Processing your caption...")
    url, filename, force_gif = media
    style = {"white": "light", "black": "dark"}[bg]
    caption = caption.replace("\\n", "\n")

    await do_caption(send_fn, url, filename, caption, style, force_gif)


@tree.command(name="image_to_gif", description="Turn your last still image into a GIF effect")
@app_commands.describe(effect="Animation effect to apply.")
@app_commands.choices(effect=[
    app_commands.Choice(name="Fade in from black", value="fade_black"),
    app_commands.Choice(name="Fade in from white", value="fade_white"),
    app_commands.Choice(name="Slow zoom out", value="zoom_in"),
    app_commands.Choice(name="Pulse", value="pulse"),
    app_commands.Choice(name="Blur reveal", value="blur"),
])
async def image_to_gif(interaction: discord.Interaction, effect: Literal["fade_black", "fade_white", "zoom_in", "pulse", "blur"]):
    media = get_remembered_media(interaction.guild.id if interaction.guild else None, interaction.user.id)
    if not media:
        media = await find_recent_user_media(interaction)

    if not media:
        try:
            await interaction.response.send_message(
                "❌ No image found. Send a still image first, then run `/image_to_gif`.",
                ephemeral=True,
            )
        except discord.NotFound:
            channel = interaction.channel
            if channel is None:
                raise RuntimeError("Interaction channel is unavailable.")
            await channel.send(f"{interaction.user.mention} ❌ No image found. Send a still image first, then run `/image_to_gif`.")
        return

    send_fn = await prepare_interaction_send(interaction, "Processing your GIF...")
    url, _filename, _force_gif = media
    await do_image_to_gif(send_fn, url, effect)


# ── /dark - toggle caption style ─────────────────────────────────────────────
@tree.command(name="dark", description="Toggle your caption style between light and dark")
async def dark(interaction: discord.Interaction):
    new_style = toggle_style(interaction.user.id)
    label = "Dark (white text on black)" if new_style == "dark" else "Light (black text on white)"
    await interaction.response.send_message(
        f"Caption style set to **{label}**.",
        ephemeral=True,
    )


# ── Ready ─────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {bot.user} - slash commands synced.")


bot.run(TOKEN)
