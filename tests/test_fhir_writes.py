"""``fhir.create`` and ``fhir.update``: conditional writes, never on the source server's id."""

import json
from typing import Any

import pytest

from dirigent_fhir.create import FhirCreateOperator
from dirigent_fhir.update import FhirUpdateOperator
from dirigent_fhir.writes import FhirWriteOutput
from dirigent_plugin import BlockFailure, ErrorClass
from dirigent_testing import FakeContext, call_block
from fhirserver import CONNECTION, FhirServer

MRN = "http://example.org/mrn"

#: A patient as read from another server: its id and version there come with it.
SOURCE: dict[str, Any] = {
    "resourceType": "Patient",
    "id": "source-17",
    "meta": {"versionId": "4", "lastUpdated": "2026-10-01T00:00:00Z", "profile": ["http://example.org/p"]},
    "identifier": [{"system": "http://other.org/id", "value": "o-1"}, {"system": MRN, "value": "12345"}],
    "name": [{"family": "Nordmann"}],
}


async def _create(ctx: FakeContext, **extra: Any) -> FhirWriteOutput:
    out = await call_block(FhirCreateOperator(), {"connection": CONNECTION, "resource": SOURCE, **extra}, ctx)
    assert isinstance(out, FhirWriteOutput)
    return out


async def _update(ctx: FakeContext, resource: dict[str, Any] = SOURCE, **extra: Any) -> FhirWriteOutput:
    out = await call_block(FhirUpdateOperator(), {"connection": CONNECTION, "resource": resource, **extra}, ctx)
    assert isinstance(out, FhirWriteOutput)
    return out


async def test_a_conditional_create_creates_once_and_then_finds_it(ctx: FakeContext, server: FhirServer) -> None:
    first = await _create(ctx, if_none_exist=f"identifier={MRN}|12345")
    second = await _create(ctx, if_none_exist=f"identifier={MRN}|12345")
    assert (first.outcome, first.status) == ("created", 201)
    assert (second.outcome, second.status, second.id) == ("existing", 200, first.id)
    assert server.count("Patient") == 1
    assert server.requests[0].headers["If-None-Exist"] == f"identifier={MRN}|12345"
    assert server.requests[0].headers["Content-Type"] == "application/fhir+json"


async def test_a_create_sends_no_source_id_and_no_source_version(ctx: FakeContext, server: FhirServer) -> None:
    out = await _create(ctx)
    sent = json.loads(server.requests[0].content)
    assert "id" not in sent
    assert sent["meta"] == {"profile": ["http://example.org/p"]}
    assert out.id != "source-17"
    assert out.version_id == "1"
    assert out.location == f"http://fhir.test/fhir/Patient/{out.id}/_history/1"


async def test_an_unconditional_create_creates_every_time(ctx: FakeContext, server: FhirServer) -> None:
    await _create(ctx)
    await _create(ctx)
    assert server.count("Patient") == 2


async def test_a_create_matching_many_is_refused(ctx: FakeContext, server: FhirServer) -> None:
    await _create(ctx)
    await _create(ctx)
    with pytest.raises(BlockFailure) as raised:
        await _create(ctx, if_none_exist=f"identifier={MRN}|12345")
    assert raised.value.error_class is ErrorClass.REJECTED
    assert raised.value.params["status"] == 412
    assert "multiple-matches" in raised.value.message


async def test_a_resource_without_a_type_is_refused_before_anything_is_sent(
    ctx: FakeContext, server: FhirServer
) -> None:
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirCreateOperator(), {"connection": CONNECTION, "resource": {"id": "x"}}, ctx)
    assert raised.value.code == "fhir.resource.no_resource_type"
    assert server.requests == []


async def test_a_conditional_update_creates_then_updates_the_same_resource(
    ctx: FakeContext, server: FhirServer
) -> None:
    first = await _update(ctx, identifier_system=MRN)
    changed = {**SOURCE, "name": [{"family": "Hansen"}]}
    second = await _update(ctx, changed, identifier_system=MRN)
    assert (first.outcome, second.outcome) == ("created", "updated")
    assert first.id == second.id
    assert second.version_id == "2"
    assert server.count("Patient") == 1
    assert server.all("Patient")[0]["name"] == [{"family": "Hansen"}]
    put = server.requests[0]
    assert put.method == "PUT"
    assert put.url.path == "/fhir/Patient"
    assert put.url.params["identifier"] == f"{MRN}|12345"
    assert "id" not in json.loads(put.content)


async def test_an_update_matches_the_first_identifier_when_no_system_is_named(
    ctx: FakeContext, server: FhirServer
) -> None:
    await _update(ctx)
    assert server.requests[0].url.params["identifier"] == "http://other.org/id|o-1"


async def test_an_identifier_value_is_escaped_for_the_search(ctx: FakeContext, server: FhirServer) -> None:
    resource = {**SOURCE, "identifier": [{"system": MRN, "value": "a|b,c$d"}]}
    await _update(ctx, resource)
    assert server.requests[0].url.params["identifier"] == f"{MRN}|a\\|b\\,c\\$d"


@pytest.mark.parametrize("system", [None, "http://nowhere.org"])
async def test_an_update_without_a_matching_identifier_is_refused(
    ctx: FakeContext, server: FhirServer, system: str | None
) -> None:
    resource = {"resourceType": "Patient", "id": "source-17"} if system is None else SOURCE
    with pytest.raises(BlockFailure) as raised:
        await _update(ctx, resource, identifier_system=system)
    assert raised.value.code == "fhir.resource.no_identifier"
    assert server.requests == []
