import logging
import os
from functools import partial
from pathlib import Path

import yaml
from httpx import AsyncClient
from pyrogram import Client
from pyrogram.methods.utilities.idle import idle

from userbot.commands import commands, download
from userbot.constants import GH_PATTERN
from userbot.hooks import check_hooks, hooks
from userbot.shortcuts import github, shortcuts
from userbot.storage import PickleStorage, Storage

logging.basicConfig(level=logging.WARNING)
_log = logging.getLogger(__name__)
_log.setLevel(logging.INFO)


async def _main(client: Client, storage: Storage, github_client: AsyncClient) -> None:
    async with client, storage, github_client:
        _log.info("Bot started")
        await idle()


def _get_project_root() -> Path:
    return Path(__file__).parent / "userbot"


def main() -> None:
    for file in ("config.yaml", "/data/config.yaml", "/config.yaml"):
        try:
            with open(file) as f:
                config = yaml.safe_load(f)
        except FileNotFoundError:
            continue
        else:
            break
    else:
        raise FileNotFoundError("Config file not found!")
    data_dir = Path(f'{_get_project_root()}/{config.get("data_location", "data")}')
    print(data_dir)
    if not data_dir.exists():
        data_dir.mkdir()
    if not data_dir.is_dir():
        raise NotADirectoryError("config.yaml: `data_location` must be a directory")
    os.chdir(data_dir)
    client = Client(
        name=config["session"],
        api_id=config["api_id"],
        api_hash=config["api_hash"],
        app_version="karych/userbot 0.1.x",
        device_model="Linux",
        workdir=str(data_dir),
        **(config.get("kwargs") or {}),
    )
    storage = PickleStorage(data_dir / f"{config['session']}.pkl")
    github_client = AsyncClient(base_url="https://api.github.com/", http2=True)

    commands.add_handler(check_hooks, ["hookshere", "hooks_here"], kwargs={"storage": storage})
    commands.add_handler(
        download,
        ["download", "dl"],
        usage="[reply] [filename]",
        waiting_message="<i>Downloading file(s)...</i>",
        kwargs={"data_dir": data_dir},
    )
    shortcuts.add_handler(partial(github, client=github_client), GH_PATTERN)

    commands.register(client, with_help=True)
    hooks.register(client, storage)
    shortcuts.register(client)

    client.run(_main(client, storage, github_client))


main()
