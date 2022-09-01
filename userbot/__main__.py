import logging
import os
from functools import partial

from httpx import AsyncClient
from pyrogram import Client
from pyrogram.handlers import RawUpdateHandler
from pyrogram.methods.utilities.idle import idle

from userbot.commands import commands
from userbot.commands.chat_admin import react2ban_raw_reaction_handler
from userbot.config import Config, RedisConfig
from userbot.constants import GH_PATTERN
from userbot.hooks import commands as hooks_commands
from userbot.hooks import hooks
from userbot.job_manager import AsyncJobManager
from userbot.shortcuts import github, shortcuts
from userbot.storage import RedisStorage, Storage
from userbot.utils import fetch_stickers, is_prod

logging.basicConfig(level=logging.WARNING)
_log = logging.getLogger(__name__)
_log.setLevel(logging.INFO if is_prod() else logging.DEBUG)


async def _main(
    client: Client,
    storage: Storage,
    github_client: AsyncClient,
    job_manager: AsyncJobManager,
) -> None:
    async with client, storage, github_client, job_manager:
        _log.debug("Checking for sticker cache presence...")
        cache = await storage.get_sticker_cache()
        if len(cache) == 0:
            await storage.put_sticker_cache(await fetch_stickers(client))
        job_manager.add_job(storage.sticker_cache_job(lambda: fetch_stickers(client)))
        _log.info("Bot started")
        await idle()


def main() -> None:
    _log.debug("Loading config...")
    config = Config.from_env()
    if not config.data_location.exists():
        config.data_location.mkdir()
    if not config.data_location.is_dir():
        raise NotADirectoryError(f"{config.data_location} must be a directory (`data_location`)")
    os.chdir(config.data_location)
    env_suffix = "-dev" if not is_prod() else ""
    client = Client(
        name=config.session,
        api_id=config.api_id,
        api_hash=config.api_hash,
        app_version=f"evgfilim1/userbot 0.4.x{env_suffix}",
        device_model="Linux",
        workdir=str(config.data_location),
        **config.kwargs,
    )
    redis_config = RedisConfig.from_env()
    storage = RedisStorage(redis_config.host, redis_config.port, redis_config.db)
    github_client = AsyncClient(base_url="https://api.github.com/", http2=True)

    _log.debug("Registering handlers...")
    commands.add_submodule(hooks_commands)
    shortcuts.add_handler(partial(github, client=github_client), GH_PATTERN)
    client.add_handler(
        RawUpdateHandler(partial(react2ban_raw_reaction_handler, storage=storage)),
        group=1,
    )

    commands.register(
        client,
        with_help=True,
        kwargs={
            "storage": storage,
            "data_dir": config.data_location,
        },
    )
    hooks.register(client, storage)
    shortcuts.register(client)

    job_manager = AsyncJobManager()

    client.run(_main(client, storage, github_client, job_manager))


main()
