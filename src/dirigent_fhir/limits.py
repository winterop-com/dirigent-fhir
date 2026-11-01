"""What one answer from an outside service may cost: how much it decodes to, and how long it takes."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Final, Self

import httpx2

from dirigent_common import Message
from dirigent_plugin import BlockFailure, ErrorClass

#: How much of an error answer is read, which is more than a refusal quotes.
ERROR_BODY: Final = 64 * 1024


@dataclass(frozen=True)
class Limits:
    """A connection's limits, and the pack's refusals for passing them."""

    max_response: int
    """The most one response may decode to, in bytes."""

    request_deadline: float
    """The longest one request may take, start to last byte, in seconds."""

    too_large: Message
    """The refusal for an answer past ``max_response``, naming ``where`` and ``limit``."""

    past_deadline: Message
    """The refusal for a request past its deadline, naming ``where`` and ``seconds``."""


class BoundedClient:
    """A connection's client, through which every request is held to the connection's limits."""

    def __init__(self, client: httpx2.AsyncClient, limits: Limits) -> None:
        """Wrap the client a connection built."""
        self._client = client
        self._limits = limits

    @property
    def headers(self) -> httpx2.Headers:
        """The headers every request carries."""
        return self._client.headers

    @property
    def base_url(self) -> httpx2.URL:
        """The URL a relative path is resolved against."""
        return self._client.base_url

    def build_request(self, method: str, path: str) -> httpx2.Request:
        """Build the request this client would send, without sending it."""
        return self._client.build_request(method, path)

    @property
    def max_response(self) -> int:
        """The most one response may decode to, unless a block says otherwise."""
        return self._limits.max_response

    async def __aenter__(self) -> Self:
        """Open the client."""
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the client."""
        await self._client.__aexit__(kind, error, traceback)

    @contextlib.asynccontextmanager
    async def stream(
        self, method: str, path: str, *, request_deadline: float | None = None, **kwargs: Any
    ) -> AsyncGenerator[httpx2.Response]:
        """Open a request whose whole life, from sending it to reading what the caller reads, has a deadline."""
        seconds = self._limits.request_deadline if request_deadline is None else request_deadline
        try:
            async with asyncio.timeout(seconds), self._client.stream(method, path, **kwargs) as response:
                yield response
        except TimeoutError as error:
            raise BlockFailure(
                self._limits.past_deadline, error_class=ErrorClass.TRANSIENT, where=f"{method} {path}", seconds=seconds
            ) from error

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx2.Response:
        """Make one request and read its answer whole.

        An answer past ``max_response`` is refused. An error answer is cut short instead, so it
        is still classified by its status.
        """
        async with self.stream(method, path, **kwargs) as response:
            if response.is_error:
                return await read_error(response)
            body = bytearray()
            async with contextlib.aclosing(pieces(response)) as decoded:
                async for piece in decoded:
                    if len(body) + len(piece) > self._limits.max_response:
                        raise BlockFailure(
                            self._limits.too_large,
                            error_class=ErrorClass.REJECTED,
                            where=f"{method} {path}",
                            limit=self._limits.max_response,
                        )
                    body.extend(piece)
            return _read_response(response, bytes(body))


async def pieces(response: httpx2.Response) -> AsyncGenerator[bytes]:
    """Yield a body as it decodes, giving the loop a turn after each piece.

    A small compressed answer decodes to many pieces with no read between them, so a deadline
    and everything else on the loop would otherwise wait for all of them.
    """
    async with contextlib.aclosing(response.aiter_bytes()) as decoded:
        async for piece in decoded:
            yield piece
            await asyncio.sleep(0)


async def read_error(response: httpx2.Response) -> httpx2.Response:
    """Read the opening of an error answer, as much as a refusal has any use for."""
    body = bytearray()
    async with contextlib.aclosing(pieces(response)) as decoded:
        async for piece in decoded:
            body.extend(piece[: ERROR_BODY - len(body)])
            if len(body) >= ERROR_BODY:
                break
    return _read_response(response, bytes(body))


def _read_response(response: httpx2.Response, body: bytes) -> httpx2.Response:
    """The same response holding its decoded body, so nothing about it needs the connection again."""
    headers = response.headers.copy()
    headers.pop("content-encoding", None)
    headers.pop("content-length", None)
    return httpx2.Response(
        response.status_code,
        headers=headers,
        content=body,
        request=response.request,
        extensions=response.extensions,
    )
