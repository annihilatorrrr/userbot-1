__all__ = [
    "commands",
]

from os import getenv

from pyrogram import Client
from pyrogram.types import Message

from ..modules import CommandsModule

commands = CommandsModule()


@commands.add("about", usage="")
async def about(_: Client, __: Message, ___: str) -> str:
    """Shows information about this userbot"""
    base_url = "https://github.com/devkarych/userbot"
    commit = getenv("GITHUB_SHA", None)
    # Maybe get this from the git repo, but there's no need for it now
    t = (
        f"ℹ️ <b>About userbot</b>\n"
        f"<i>Repo:</i> <a href='{base_url}'>devkarych/userbot</a>\n"
        f"<i>Commit:</i> <code>{commit or 'staging'}</code>"
    )
    if commit:
        t += f" (<a href='{base_url}/deployments'>deployments</a>)"
    return t
