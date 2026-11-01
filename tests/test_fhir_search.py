"""``fhir.search``: pages followed by next link under a cap, and never off the connection's origin."""

from typing import Any

import httpx2
import pytest

from dirigent_fhir.search import FhirSearchOperator, FhirSearchOutput
from dirigent_plugin import BlockFailure, ErrorClass
from dirigent_testing import FakeContext, call_block
from fhirserver import CONNECTION, FhirServer

MRN = "http://example.org/mrn"


def _patients(server: FhirServer, count: int) -> None:
    for number in range(count):
        server.seed({"resourceType": "Patient", "identifier": [{"system": MRN, "value": f"p{number}"}]})


def _config(**extra: Any) -> dict[str, Any]:
    return {"connection": CONNECTION, "resource_type": "Patient", "params": {"_count": "2"}, **extra}


async def test_every_page_is_followed_and_folded_into_one_searchset(ctx: FakeContext, server: FhirServer) -> None:
    _patients(server, 5)
    out = await call_block(FhirSearchOperator(), _config(), ctx)
    assert isinstance(out, FhirSearchOutput)
    assert (out.count, out.total, out.pages, out.complete) == (5, 5, 3, True)
    assert out.bundle["resourceType"] == "Bundle"
    assert out.bundle["type"] == "searchset"
    assert "link" not in out.bundle
    entries = out.bundle["entry"]
    assert isinstance(entries, list)
    values = [entry["resource"]["identifier"][0]["value"] for entry in entries]  # type: ignore[call-overload, index]
    assert values == ["p0", "p1", "p2", "p3", "p4"]
    assert entries[0] == {  # the score the engine's model does not name is not carried
        "fullUrl": "http://fhir.test/fhir/Patient/1",
        "resource": server.store["Patient"]["1"],
        "search": {"mode": "match"},
    }
    assert [request.url.params.get("_offset") for request in server.requests] == [None, "2", "4"]


async def test_a_search_past_max_pages_fails_by_default(ctx: FakeContext, server: FhirServer) -> None:
    _patients(server, 5)
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirSearchOperator(), _config(max_pages=2), ctx)
    assert raised.value.code == "fhir.search.more_pages"
    assert raised.value.error_class is ErrorClass.REJECTED
    assert len(server.requests) == 2


async def test_a_search_past_max_pages_stops_when_asked(ctx: FakeContext, server: FhirServer) -> None:
    _patients(server, 5)
    out = await call_block(FhirSearchOperator(), _config(max_pages=2, on_more="stop"), ctx)
    assert isinstance(out, FhirSearchOutput)
    assert (out.count, out.pages, out.complete, out.total) == (4, 2, False, 5)


async def test_a_list_parameter_is_sent_once_per_value(ctx: FakeContext, server: FhirServer) -> None:
    await call_block(FhirSearchOperator(), _config(params={"_tag": ["a", "b"], "identifier": f"{MRN}|p1"}), ctx)
    assert server.requests[0].url.params.get_list("_tag") == ["a", "b"]
    assert server.requests[0].url.params["identifier"] == f"{MRN}|p1"


@pytest.mark.parametrize(
    "elsewhere",
    [
        "http://attacker.test/fhir/Patient?_offset=2",
        "https://fhir.test/fhir/Patient?_offset=2",
        "http://fhir.test:8080/fhir/Patient?_offset=2",
    ],
)
async def test_a_next_link_off_the_origin_is_never_followed(
    ctx: FakeContext, server: FhirServer, elsewhere: str
) -> None:
    _patients(server, 3)
    server.next_link_override = elsewhere
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirSearchOperator(), _config(), ctx)
    assert raised.value.code == "fhir.off_origin"
    assert raised.value.error_class is ErrorClass.REJECTED
    assert [request.url.host for request in server.requests] == ["fhir.test"]


async def test_an_outcome_entry_is_answered_as_issues_not_as_a_result(ctx: FakeContext, server: FhirServer) -> None:
    def warn(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "resourceType": "Bundle",
                "type": "searchset",
                "total": 0,
                "entry": [
                    {
                        "resource": {
                            "resourceType": "OperationOutcome",
                            "issue": [{"severity": "warning", "code": "not-supported", "diagnostics": "unknown: foo"}],
                        },
                        "search": {"mode": "outcome"},
                    }
                ],
            },
        )

    server.script(warn)
    out = await call_block(FhirSearchOperator(), _config(params={"foo": "1"}), ctx)
    assert isinstance(out, FhirSearchOutput)
    assert out.count == 0
    assert "entry" not in out.bundle
    assert [issue.describe() for issue in out.issues] == ["warning not-supported: unknown: foo"]
