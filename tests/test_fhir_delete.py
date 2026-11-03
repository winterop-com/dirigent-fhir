"""``fhir.delete``: by id, or what a search matches, never more than ``max``."""

from typing import Any

import httpx2
import pytest
from pydantic import ValidationError

from dirigent_fhir.delete import FhirDeleteConfig, FhirDeleteOperator, FhirDeleteOutput
from dirigent_plugin import BlockFailure, ErrorClass
from dirigent_testing import FakeContext, call_block
from fhirserver import CONNECTION, FhirServer

MRN = "http://example.org/mrn"


def _seed(server: FhirServer, count: int) -> list[str]:
    return [
        server.seed({"resourceType": "Patient", "identifier": [{"system": MRN, "value": f"m{i}"}]})["id"]
        for i in range(count)
    ]


async def _delete(ctx: FakeContext, **config: Any) -> FhirDeleteOutput:
    out = await call_block(FhirDeleteOperator(), {"connection": CONNECTION, "resource_type": "Patient", **config}, ctx)
    assert isinstance(out, FhirDeleteOutput)
    return out


def _deletes(server: FhirServer) -> list[str]:
    return [request.url.path for request in server.requests if request.method == "DELETE"]


async def test_a_delete_by_id_deletes_it_and_a_second_finds_it_absent(ctx: FakeContext, server: FhirServer) -> None:
    [held] = _seed(server, 1)
    first = await _delete(ctx, id=held)
    second = await _delete(ctx, id=held)
    assert (first.outcome, first.deleted, first.ids) == ("deleted", 1, [held])
    assert (second.outcome, second.deleted, second.ids) == ("absent", 0, [])
    assert server.count("Patient") == 0
    assert _deletes(server) == [f"/fhir/Patient/{held}"]


async def test_a_delete_of_an_id_never_held_is_absent_and_sends_no_delete(ctx: FakeContext, server: FhirServer) -> None:
    out = await _delete(ctx, id="never-held")
    assert out.outcome == "absent"
    assert _deletes(server) == []


async def test_a_conditional_delete_deletes_each_match_by_its_id(ctx: FakeContext, server: FhirServer) -> None:
    ids = _seed(server, 3)
    server.seed({"resourceType": "Patient", "identifier": [{"system": "http://other.org", "value": "o"}]})
    out = await _delete(ctx, params={"identifier": f"{MRN}|"}, max=3)
    assert (out.outcome, out.deleted, sorted(out.ids)) == ("deleted", 3, sorted(ids))
    assert server.count("Patient") == 1
    assert sorted(_deletes(server)) == sorted(f"/fhir/Patient/{one}" for one in ids)
    search = server.requests[0]
    assert search.method == "GET"
    assert search.url.params["_count"] == "4"


async def test_a_search_matching_more_than_max_deletes_nothing(ctx: FakeContext, server: FhirServer) -> None:
    _seed(server, 3)
    with pytest.raises(BlockFailure) as raised:
        await _delete(ctx, params={"identifier": f"{MRN}|"}, max=2)
    assert raised.value.code == "fhir.delete.too_many"
    assert raised.value.error_class is ErrorClass.REJECTED
    assert raised.value.params["matched"] == "3"
    assert server.count("Patient") == 3
    assert _deletes(server) == []


async def test_a_server_that_states_no_total_is_held_to_max_by_what_it_answered(
    ctx: FakeContext, server: FhirServer
) -> None:
    _seed(server, 3)

    def without_total(request: httpx2.Request) -> httpx2.Response | None:
        if request.method != "GET":
            return None
        server.scripted.remove(without_total)
        answer = server(request)
        server.scripted.append(without_total)
        bundle = answer.json()
        bundle.pop("total")
        return httpx2.Response(200, json=bundle)

    server.script(without_total)
    with pytest.raises(BlockFailure) as raised:
        await _delete(ctx, params={"identifier": f"{MRN}|"}, max=1)
    assert raised.value.params["matched"] == "more than 1"
    assert server.count("Patient") == 3


async def test_a_search_matching_nothing_is_absent(ctx: FakeContext, server: FhirServer) -> None:
    out = await _delete(ctx, params={"identifier": f"{MRN}|none"})
    assert (out.outcome, out.deleted) == ("absent", 0)
    assert _deletes(server) == []


@pytest.mark.parametrize(
    "params",
    [{}, {"_count": "10"}, {"_sort": "name", "_elements": "id"}, {"identifier": ""}, {"identifier": []}],
)
def test_a_search_that_matches_everything_is_refused(params: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="matches every Patient"):
        FhirDeleteConfig.model_validate({"connection": CONNECTION, "resource_type": "Patient", "params": params})


@pytest.mark.parametrize("target", [{}, {"id": "1", "params": {"identifier": f"{MRN}|1"}}])
def test_a_delete_names_its_resource_by_id_or_by_params(target: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="by id or by params, exactly one"):
        FhirDeleteConfig.model_validate({"connection": CONNECTION, "resource_type": "Patient", **target})


def test_a_search_parameter_carrying_a_credential_is_refused() -> None:
    with pytest.raises(ValidationError, match="carries a credential"):
        FhirDeleteConfig.model_validate(
            {"connection": CONNECTION, "resource_type": "Patient", "params": {"access_token": "x"}}
        )
