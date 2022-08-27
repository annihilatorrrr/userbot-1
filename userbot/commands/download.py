__all__ = [
    "download",
]

from pathlib import Path

import aiofiles
import magic
from pyrogram import Client
from pyrogram.enums import MessageMediaType
from pyrogram.types import Message

_CHUNK_SIZE = 1048576 * 4  # 4 MiB


async def _downloader(client: Client, message: Message, filename: str, data_dir: Path) -> str:
    if message.media in (
        None,
        MessageMediaType.CONTACT,
        MessageMediaType.LOCATION,
        MessageMediaType.VENUE,
        MessageMediaType.POLL,
        MessageMediaType.WEB_PAGE,
        MessageMediaType.DICE,
        MessageMediaType.GAME,
    ):
        return "⚠ <b>No downloadable media found</b>"
    media_type = message.media.value
    media_dir = data_dir
    if not filename:
        media_dir /= media_type
        if not media_dir.exists():
            media_dir.mkdir()
    media = getattr(message, media_type)
    filename = filename or getattr(media, "file_name", None)
    output_io = await client.download_media(message, in_memory=True)
    output_io.seek(0)
    if not filename:
        filename = f"{message.date.strftime('%Y%m%d%H%M%S')}_{message.chat.id}_{message.id}"
        mime = getattr(media, "mime_type", None)
        if not mime:
            mime = magic.from_buffer(output_io.read(2048), mime=True)
        ext = client.guess_extension(mime)
        if not ext:
            ext = ".bin"
        filename += ext
    output_io.seek(0)
    output = media_dir / filename
    async with aiofiles.open(output, "wb") as f:
        while chunk := output_io.read(_CHUNK_SIZE):
            await f.write(chunk)
    return f"The file has been downloaded to <code>{output}</code>"


async def download(client: Client, message: Message, args: str, *, data_dir: Path) -> str:
    """Downloads a file or files"""
    msg = message.reply_to_message or message
    all_messages = await msg.get_media_group() if msg.media_group_id else [msg]
    t = ""
    for m in all_messages:
        try:
            t += await _downloader(client, m, args if len(all_messages) == 1 else "", data_dir)
        except Exception as e:
            t += f"⚠ <code>{type(e).__name__}: {e}</code>"
        finally:
            t += "\n"
    return t
