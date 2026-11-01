"""What one answer may cost through a fhir connection, against a hostile server on loopback."""

import asyncio
import tracemalloc
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
import pytest_asyncio

from dirigent_fhir.connection import FhirConnectionConfig, FhirConnectionKind
from dirigent_fhir.read import FhirReadOperator, FhirReadOutput
from dirigent_fhir.search import FhirSearchOperator
from dirigent_plugin import BlockFailure, ErrorClass
from dirigent_testing import FakeContext, call_block
from fhirserver import CONNECTION
from hostile import MIB, Hostile, answer, double_gzip, drip, json_answer

TOO_LARGE = "fhir.response_too_large"
PAST_DEADLINE = "fhir.past_deadline"
READ = "GET /fhir/Patient/1"
SEARCH = "GET /fhir/Patient"

#: How long a test waits on a drip before calling it unbounded.
GUARD = 10


@pytest_asyncio.fixture
async def hostile() -> AsyncIterator[Hostile]:
    server = Hostile()
    await server.start()
    try:
        yield server
    finally:
        server.stop()


def _connect(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, hostile: Hostile, **limits: Any) -> None:
    """Point the context's connection at the hostile server, over a real client with no transport."""
    config = FhirConnectionConfig.model_validate({"base_url": f"{hostile.url}/fhir", **limits})

    def http(ref: str) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(base_url=config.base_url)

    ctx.connections[CONNECTION] = config
    monkeypatch.setattr(ctx, "http", http)


READ_CONFIG = {"connection": CONNECTION, "resource_type": "Patient", "id": "1"}


async def test_an_answer_past_the_default_limit_is_refused(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, hostile: Hostile
) -> None:
    hostile.on(READ, double_gzip(65 * MIB))
    _connect(ctx, monkeypatch, hostile)
    with pytest.raises(BlockFailure) as refused:
        await call_block(FhirReadOperator(), READ_CONFIG, ctx)
    assert refused.value.code == TOO_LARGE
    assert refused.value.error_class is ErrorClass.REJECTED


async def test_an_answer_past_the_limit_costs_little(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, hostile: Hostile
) -> None:
    hostile.on(READ, double_gzip(200 * MIB))
    _connect(ctx, monkeypatch, hostile, max_response="1MB")
    tracemalloc.start()
    try:
        with pytest.raises(BlockFailure) as refused:
            await call_block(FhirReadOperator(), READ_CONFIG, ctx)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert refused.value.code == TOO_LARGE
    assert peak < 16 * MIB


async def test_an_answer_within_the_limit_is_read(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, hostile: Hostile
) -> None:
    hostile.on(READ, json_answer({"resourceType": "Patient", "id": "1"}))
    _connect(ctx, monkeypatch, hostile, max_response="1KB")
    out = await call_block(FhirReadOperator(), READ_CONFIG, ctx)
    assert isinstance(out, FhirReadOutput)
    assert out.resource == {"resourceType": "Patient", "id": "1"}


async def test_every_page_of_a_search_is_held_to_the_limit(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, hostile: Hostile
) -> None:
    hostile.on(SEARCH, double_gzip(2 * MIB))
    _connect(ctx, monkeypatch, hostile, max_response="1MB")
    with pytest.raises(BlockFailure) as refused:
        await call_block(FhirSearchOperator(), {"connection": CONNECTION, "resource_type": "Patient"}, ctx)
    assert refused.value.code == TOO_LARGE


async def test_an_error_answer_past_the_limit_is_still_classified_by_its_status(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, hostile: Hostile
) -> None:
    hostile.on(READ, double_gzip(65 * MIB, status="503 Service Unavailable"))
    _connect(ctx, monkeypatch, hostile)
    with pytest.raises(BlockFailure) as refused:
        await call_block(FhirReadOperator(), READ_CONFIG, ctx)
    assert refused.value.code == "fhir.answered"
    assert refused.value.error_class is ErrorClass.TRANSIENT


async def test_a_body_that_never_ends_is_cut_at_the_deadline(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, hostile: Hostile
) -> None:
    hostile.on(READ, drip)
    _connect(ctx, monkeypatch, hostile, request_deadline="1s")
    async with asyncio.timeout(GUARD):
        with pytest.raises(BlockFailure) as refused:
            await call_block(FhirReadOperator(), READ_CONFIG, ctx)
    assert refused.value.code == PAST_DEADLINE
    assert refused.value.error_class is ErrorClass.TRANSIENT


async def test_the_health_check_is_held_to_the_limits(hostile: Hostile) -> None:
    hostile.on("GET /fhir/metadata", drip)
    config = FhirConnectionConfig.model_validate({"base_url": f"{hostile.url}/fhir", "request_deadline": "1s"})
    async with asyncio.timeout(GUARD):
        report = await FhirConnectionKind().check(config)
    assert report.healthy is False
    assert report.detail is not None and "took longer than the 1.0 seconds" in report.detail


async def test_a_plain_error_page_is_quoted(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, hostile: Hostile
) -> None:
    hostile.on(READ, answer("502 Bad Gateway", b"<html>upstream down</html>"))
    _connect(ctx, monkeypatch, hostile)
    with pytest.raises(BlockFailure) as refused:
        await call_block(FhirReadOperator(), READ_CONFIG, ctx)
    assert refused.value.params["remote"] == "<html>upstream down</html>"
    assert refused.value.error_class is ErrorClass.TRANSIENT
