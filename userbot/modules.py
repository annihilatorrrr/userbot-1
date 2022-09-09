import asyncio
import functools
import html
import inspect
import logging
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from traceback import extract_tb
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

from httpx import AsyncClient, HTTPError
from pyrogram import Client
from pyrogram import filters as flt
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified, MessageTooLong, SlowmodeWait
from pyrogram.handlers import EditedMessageHandler, MessageHandler
from pyrogram.types import Message

from .storage import Storage
from .utils import is_prod

if TYPE_CHECKING:
    from traceback import FrameSummary


_SNIP = "<...snip...>"
_DEFAULT_TIMEOUT = 30
_DEFAULT_PREFIX = "." if is_prod() else ","

nekobin = AsyncClient(base_url="https://nekobin.com/")


def _filter_kwargs(func: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    suitable_kwargs = {}
    sig = inspect.signature(func)
    for name, param in sig.parameters.items():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            suitable_kwargs = kwargs  # pass all kwargs
        if name in kwargs:
            suitable_kwargs[name] = kwargs[name]
    return suitable_kwargs


class CommandHandler(Protocol):
    async def __call__(
        self,
        client: Client,
        message: Message,
        args: str,
        **kwargs: Any,
    ) -> str | None:
        pass


class Handler(Protocol):
    async def __call__(
        self,
        client: Client,
        message: Message,
    ) -> None:
        pass


class TransformHandler(Protocol):
    async def __call__(
        self,
        match: re.Match[str],
    ) -> str | None:
        pass


_log = logging.getLogger(__name__)


@dataclass()
class _CommandHandler:
    command: str | list[str]
    prefix: str
    handler: CommandHandler
    handle_edits: bool
    usage: str
    doc: str | None
    waiting_message: str | None
    category: str | None
    hidden: bool
    timeout: int | None

    def __post_init__(self):
        self.doc = re.sub(r"\n(\n?)\s+", r"\n\1", self.doc)  # Remove extra whitespaces

    def _report_exception(self, message: Message, e: Exception) -> str:
        """Logs an exception to the logger and returns a message to be sent to the user."""
        _log.exception(
            "An error occurred during executing %r",
            message.text,
            extra={"command": self.command},
        )
        last_own_frame: FrameSummary | None = None
        last_frame: FrameSummary | None = None
        own_package_name = Path(__file__).parent
        for frame in extract_tb(e.__traceback__):
            if frame.filename is not None and own_package_name in Path(frame.filename).parents:
                last_own_frame = frame
            last_frame = frame
        tb = ""
        if last_own_frame is not None and last_frame is not None:
            tb += '  {snip}\n  File "{}", line {}, in {}\n    {}\n'.format(
                last_own_frame.filename,
                last_own_frame.lineno,
                last_own_frame.name,
                last_own_frame.line.strip(),
                snip=_SNIP,
            )
            if last_frame is not last_own_frame:
                tb += '  {snip}\n  File "{}", line {}, in {}\n    {}\n'.format(
                    last_frame.filename,
                    last_frame.lineno,
                    last_frame.name,
                    last_frame.line.strip(),
                    snip=_SNIP,
                )
        tb += type(e).__qualname__
        if exc_value := str(e):
            tb += f": {exc_value}"
        tb = f"<pre><code class='language-python'>{html.escape(tb)}</code></pre>"
        return (
            f"<b>[‼] An error occurred during executing command.</b>\n\n"
            f"<b>Command:</b> <code>{html.escape(message.text)}</code>\n"
            f"<b>Traceback:</b>\n{tb}\n\n"
            f"<i>More info can be found in logs.</i>"
        )

    async def _waiting_task(self, message: Message) -> None:
        await asyncio.sleep(0.75)
        text = self.waiting_message or f"<i>Executing</i> <code>{html.escape(message.text)}</code>"
        await message.edit(f"⌚ {text}")

    async def __call__(self, client: Client, message: Message):
        args = message.text.lstrip(self.prefix)
        if not isinstance(self.command, str):
            for cmd in self.command:
                args = args.removeprefix(cmd)
        else:
            args = args.removeprefix(self.command)
        args = args.lstrip()
        waiting_task = asyncio.create_task(self._waiting_task(message))
        try:
            result: str | None = await asyncio.wait_for(
                self.handler(client, message, args),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError as e:
            self._report_exception(message, e)  # just log the exception
            await message.edit(
                f"<b>[‼] Command timed out after {self.timeout} seconds.</b>\n\n"
                f"<b>Command:</b> <code>{html.escape(message.text)}</code>\n\n"
                f"<i>More info can be found in logs.</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            waiting_task.cancel()
            text = self._report_exception(message, e)
            await message.edit(text, parse_mode=ParseMode.HTML)
        else:
            waiting_task.cancel()
            if result is None:
                return
            try:
                await message.edit(result, parse_mode=ParseMode.HTML)
            except MessageTooLong as e:
                text = (
                    f"<b>[✅] Successfully executed.</b>\n\n"
                    f"<b>Command:</b> <code>{html.escape(message.text)}</code>\n"
                    f"<b>Result:</b>"
                )
                try:
                    res = await nekobin.post("/api/documents", json={"content": result})
                    res.raise_for_status()
                except HTTPError:
                    # Nekobin is down, try to send result as a file
                    try:
                        await message.reply_document(
                            BytesIO(result.encode("utf-8")),
                            file_name="result.html",  # default parse mode for the bot is html
                        )
                    except SlowmodeWait:
                        # Cannot send file, report `MessageTooLong` exception.
                        # It's ok to log entire exception chain here
                        text = self._report_exception(message, e)
                        await message.edit(text, parse_mode=ParseMode.HTML)
                    else:
                        await message.edit(f"{text} <i>See reply</i>", parse_mode=ParseMode.HTML)
                else:
                    await message.edit(
                        f"{text} {nekobin.base_url.join(res.json()['result']['key'])}.html",
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
            except MessageNotModified as e:
                self._report_exception(message, e)
                if not is_prod():
                    await message.edit(
                        f"{result}\n\n⚠ <i><b>MessageNotModified</b> was raised, check that there"
                        f" is only one userbot instance is running</i>",
                        parse_mode=ParseMode.HTML,
                    )
            except Exception as e:
                text = self._report_exception(message, e)
                await message.edit(text, parse_mode=ParseMode.HTML)


@dataclass()
class _HookHandler:
    name: str
    filters: flt.Filter
    handler: Handler
    handle_edits: bool

    async def add_handler(self, _: Client, message: Message, __: str, *, storage: Storage) -> None:
        await storage.enable_hook(self.name, message.chat.id)
        await message.delete()

    async def del_handler(self, _: Client, message: Message, __: str, *, storage: Storage) -> None:
        await storage.disable_hook(self.name, message.chat.id)
        await message.delete()

    async def __call__(self, client: Client, message: Message, storage: Storage) -> None:
        if await storage.is_hook_enabled(self.name, message.chat.id):
            await self.handler(client, message)


@dataclass()
class _ShortcutHandler:
    regex: re.Pattern[str]
    handler: TransformHandler
    handle_edits: bool

    async def __call__(self, client: Client, message: Message):
        raw_text = message.text or message.caption
        if raw_text is None:
            return
        text = raw_text.html
        while match := self.regex.search(text):
            if (result := await self.handler(match)) is not None:
                text = f"{text[:match.start()]}{result}{text[match.end():]}"
        await message.edit(text, parse_mode=ParseMode.HTML)


def _format_handler_usage(handler: _CommandHandler, full: bool = False) -> str:
    if isinstance(handler.command, str):
        commands = handler.command
    else:
        commands = "|".join(handler.command)
    usage = f" {handler.usage}".rstrip()
    doc = handler.doc or ""
    if not full:
        doc = doc.strip().split("\n")[0].strip()
    description = f" — {doc}" if handler.doc else ""
    return f"{commands}{usage}{description}"


def _command_handler_sort_key(handler: _CommandHandler) -> tuple[str, str]:
    category = handler.category or ""
    if isinstance(handler.command, str):
        return category, handler.command
    return category, handler.command[0]


class CommandsModule:
    def __init__(self, category: str | None = None):
        self._handlers: list[_CommandHandler] = []
        self._category = category

    def add(
        self,
        command: str | list[str],
        prefix: str = _DEFAULT_PREFIX,
        *,
        handle_edits: bool = True,
        usage: str = "",
        doc: str | None = None,
        waiting_message: str | None = None,
        category: str | None = None,
        hidden: bool = False,
        timeout: int | None = _DEFAULT_TIMEOUT,
    ) -> Callable[[CommandHandler], CommandHandler]:
        def _decorator(f: CommandHandler) -> CommandHandler:
            self.add_handler(
                handler=f,
                command=command,
                prefix=prefix,
                handle_edits=handle_edits,
                usage=usage,
                doc=doc,
                waiting_message=waiting_message,
                category=category,
                hidden=hidden,
                timeout=timeout,
            )
            return f

        return _decorator

    def add_handler(
        self,
        handler: CommandHandler,
        command: str | list[str],
        prefix: str = _DEFAULT_PREFIX,
        *,
        handle_edits: bool = True,
        usage: str = "",
        doc: str | None = None,
        waiting_message: str | None = None,
        category: str | None = None,
        hidden: bool = False,
        timeout: int | None = _DEFAULT_TIMEOUT,
    ) -> None:
        self._handlers.append(
            _CommandHandler(
                command=command,
                prefix=prefix,
                handler=handler,
                handle_edits=handle_edits,
                usage=usage,
                doc=doc or getattr(handler, "__doc__", None),
                waiting_message=waiting_message,
                category=category or self._category,
                hidden=hidden,
                timeout=timeout,
            )
        )

    def add_submodule(self, module: "CommandsModule") -> None:
        self._handlers.extend(module._handlers)

    def register(
        self,
        client: Client,
        *,
        with_help: bool = False,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        if kwargs is None:
            kwargs = {}
        if with_help:
            self.add_handler(
                self._auto_help_handler,
                command="help",
                usage="[command]",
                doc="Sends help for all commands or for a specific one",
                category="About",
            )
        for handler in self._handlers:
            # Pass only suitable kwargs for the handler
            handler_kwargs = _filter_kwargs(handler.handler, kwargs)
            handler.handler = functools.partial(handler.handler, **handler_kwargs)
            f = flt.me & ~flt.scheduled & flt.command(handler.command, handler.prefix)
            client.add_handler(MessageHandler(handler.__call__, f))
            if handler.handle_edits:
                client.add_handler(EditedMessageHandler(handler.__call__, f))

    async def _auto_help_handler(self, _: Client, __: Message, args: str) -> str:
        if args:
            for h in self._handlers:
                if (isinstance(h.command, str) and h.command == args) or (
                    not isinstance(h.command, str) and args in h.command
                ):
                    usage = _format_handler_usage(h, full=True)
                    return f"<b>Help for {args}:</b>\n{html.escape(usage)}"
            else:
                return f"<b>No help found for {args}</b>"
        text = "<b>List of userbot commands available:</b>\n\n"
        prev_cat = ""
        for handler in sorted(self._handlers, key=_command_handler_sort_key):
            if handler.hidden:
                continue
            usage = _format_handler_usage(handler)
            if (handler.category or "") != prev_cat:
                text += f"\n<i>{handler.category}:</i>\n"
                prev_cat = handler.category
            text += f"• {html.escape(usage)}\n"
        # This will happen if there are no handlers without category
        text = text.replace("\n\n\n", "\n\n")
        return text


class HooksModule:
    def __init__(self):
        self._handlers: list[_HookHandler] = []

    def add(
        self,
        name: str,
        filters: flt.Filter,
        *,
        handle_edits: bool = False,
    ) -> Callable[[Handler], Handler]:
        def _decorator(f: Handler) -> Handler:
            self.add_handler(
                handler=f,
                name=name,
                filters=filters,
                handle_edits=handle_edits,
            )
            return f

        return _decorator

    def add_handler(
        self,
        handler: Handler,
        name: str,
        filters: flt.Filter,
        *,
        handle_edits: bool = False,
    ) -> None:
        self._handlers.append(
            _HookHandler(
                name=name,
                filters=filters,
                handler=handler,
                handle_edits=handle_edits,
            )
        )

    def add_submodule(self, module: "HooksModule") -> None:
        self._handlers.extend(module._handlers)

    @staticmethod
    def _wrapper(
        f: Callable[[Client, Message, Storage], Awaitable[None]],
        storage: Storage,
    ) -> Handler:
        @functools.wraps(f)
        async def wrapper(client: Client, message: Message) -> None:
            return await f(client, message, storage)

        return wrapper

    def register(self, client: Client, storage: Storage, commands: CommandsModule) -> None:
        cmds = CommandsModule("Hooks")
        for handler in self._handlers:
            cmds.add_handler(
                handler.add_handler,
                [f"{handler.name}here", f"{handler.name}_here"],
                doc=f"Enable {handler.name} hook for this chat",
                hidden=True,
            )
            cmds.add_handler(
                handler.del_handler,
                [f"no{handler.name}here", f"no_{handler.name}_here"],
                doc=f"Disable {handler.name} hook for this chat",
                hidden=True,
            )
            f = flt.incoming & handler.filters
            client.add_handler(MessageHandler(self._wrapper(handler, storage=storage), f))
            if handler.handle_edits:
                client.add_handler(EditedMessageHandler(self._wrapper(handler, storage=storage), f))
        cmds.add_handler(
            self._check_hooks,
            ["hookshere", "hooks_here"],
        )
        cmds.add_handler(
            self._list_hooks,
            ["hooklist", "hook_list"],
        )
        commands.add_submodule(cmds)

    @staticmethod
    async def _check_hooks(_: Client, message: Message, __: str, *, storage: Storage) -> str:
        """List enabled hooks in the chat"""
        hooks = ""
        async for hook in storage.list_enabled_hooks(message.chat.id):
            hooks += f"• <code>{hook}</code>\n"
        return f"Hooks in this chat:\n{hooks}"

    async def _list_hooks(self, _: Client, __: Message, ___: str) -> str:
        """List all available hooks"""
        hooks = ""
        for handler in self._handlers:
            hooks += f"• <code>{handler.name}</code>\n"
        return f"Available hooks:\n{hooks}"


class ShortcutTransformersModule:
    def __init__(self):
        self._handlers: list[_ShortcutHandler] = []

    def add(
        self,
        pattern: str | re.Pattern[str],
        *,
        handle_edits: bool = True,
    ) -> Callable[[TransformHandler], TransformHandler]:
        def _decorator(f: TransformHandler) -> TransformHandler:
            self.add_handler(
                handler=f,
                pattern=pattern,
                handle_edits=handle_edits,
            )
            return f

        return _decorator

    def add_handler(
        self,
        handler: TransformHandler,
        pattern: str | re.Pattern[str],
        *,
        handle_edits: bool = True,
    ) -> None:
        if isinstance(pattern, str):
            pattern = re.compile(pattern)
        self._handlers.append(
            _ShortcutHandler(
                regex=pattern,
                handler=handler,
                handle_edits=handle_edits,
            )
        )

    def add_submodule(self, module: "ShortcutTransformersModule") -> None:
        self._handlers.extend(module._handlers)

    def register(self, client: Client) -> None:
        for handler in self._handlers:
            f = flt.outgoing & ~flt.scheduled & flt.regex(handler.regex)
            client.add_handler(MessageHandler(handler.__call__, f))
            if handler.handle_edits:
                client.add_handler(EditedMessageHandler(handler.__call__, f))
