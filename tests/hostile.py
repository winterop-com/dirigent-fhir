"""A hostile service on loopback: an answer far larger decoded than sent, and a body that never ends."""

import asyncio
import functools
import gzip
import json
from collections.abc import Awaitable, Callable
from typing import Final

MIB: Final = 1024 * 1024

Handler = Callable[[asyncio.StreamWriter], Awaitable[None]]


@functools.lru_cache(maxsize=8)
def bomb(decoded_bytes: int) -> bytes:
    """A JSON object of about that many decoded bytes, gzipped twice: under a KiB per MiB on the wire."""
    document = json.dumps({"pad": "x" * decoded_bytes}).encode()
    return gzip.compress(gzip.compress(document))


def answer(status: str, body: bytes, *headers: str) -> Handler:
    """A handler that writes one complete answer."""

    async def write(writer: asyncio.StreamWriter) -> None:
        head = [f"HTTP/1.1 {status}", f"Content-Length: {len(body)}", "Connection: close", *headers]
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode() + body)
        await writer.drain()

    return write


def json_answer(value: object, status: str = "200 OK", *headers: str) -> Handler:
    """A handler that writes one JSON answer."""
    return answer(status, json.dumps(value).encode(), "Content-Type: application/json", *headers)


def double_gzip(decoded_bytes: int, status: str = "200 OK") -> Handler:
    """A handler that writes a JSON body gzipped twice."""
    return answer(status, bomb(decoded_bytes), "Content-Encoding: gzip, gzip", "Content-Type: application/json")


async def drip(writer: asyncio.StreamWriter) -> None:
    """A handler that never finishes its body, sending a byte well inside any read timeout."""
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n")
    while True:
        writer.write(b"1\r\nx\r\n")
        await writer.drain()
        await asyncio.sleep(0.01)


class Hostile:
    """A loopback server answering each ``METHOD /path`` with its handler, and keeping what it was asked."""

    def __init__(self) -> None:
        """Start with no routes."""
        self.handlers: dict[str, Handler] = {}
        self.requests: list[str] = []
        self.url = ""
        self._server: asyncio.Server | None = None

    def on(self, route: str, handler: Handler) -> None:
        """Answer one route, written like ``GET /health``."""
        self.handlers[route] = handler

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Answer one request and close, reading whatever body it carries."""
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode()
            lines = head.split("\r\n")
            headers = dict(line.lower().split(": ", 1) for line in lines[1:] if line)
            await reader.readexactly(int(headers.get("content-length", "0")))
            method, target, _ = lines[0].split(" ")
            route = f"{method} {target.split('?')[0]}"
            self.requests.append(route)
            await self.handlers.get(route, answer("404 Not Found", b"{}"))(writer)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def start(self) -> None:
        """Listen on a free loopback port."""
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    def stop(self) -> None:
        """Stop listening and drop every connection still open."""
        if self._server is not None:
            self._server.close()
            self._server.abort_clients()
