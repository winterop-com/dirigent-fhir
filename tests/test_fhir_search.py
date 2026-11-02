"""``fhir.search``: pages followed by next link under a cap, and never off the connection's origin."""

import threading
from typing import Any, cast

import httpx2
import pytest

from dirigent_fhir import client, r4, search
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
    assert out.bundle is not None
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
    assert out.bundle is not None
    assert "entry" not in out.bundle
    assert [issue.describe() for issue in out.issues] == ["warning not-supported: unknown: foo"]


async def test_pages_are_handed_on_one_bundle_each_for_a_fan_out(ctx: FakeContext, server: FhirServer) -> None:
    _patients(server, 5)
    out = await call_block(FhirSearchOperator(), _config(emit="pages"), ctx)
    assert isinstance(out, FhirSearchOutput)
    assert out.bundle is None and out.resources is None
    assert out.bundles is not None
    assert [bundle["type"] for bundle in out.bundles] == ["searchset"] * 3
    values: list[list[Any]] = [
        [entry["resource"]["identifier"][0]["value"] for entry in cast("list[Any]", bundle["entry"])]
        for bundle in out.bundles
    ]
    assert values == [["p0", "p1"], ["p2", "p3"], ["p4"]]
    assert (out.count, out.pages, out.complete) == (5, 3, True)


async def test_resources_are_handed_on_one_each_for_a_fan_out(ctx: FakeContext, server: FhirServer) -> None:
    _patients(server, 5)
    out = await call_block(FhirSearchOperator(), _config(emit="resources"), ctx)
    assert isinstance(out, FhirSearchOutput)
    assert out.bundle is None and out.bundles is None
    assert out.resources == server.all("Patient")
    assert out.count == 5


async def test_an_empty_search_hands_on_an_empty_list_which_skips_the_fan_out(
    ctx: FakeContext, server: FhirServer
) -> None:
    for emit in ("pages", "resources"):
        out = await call_block(FhirSearchOperator(), _config(emit=emit), ctx)
        assert isinstance(out, FhirSearchOutput)
        assert (out.bundles if emit == "pages" else out.resources) == []
        assert (out.count, out.pages) == (0, 1)


def _large_patients(server: FhirServer, count: int, size: int) -> None:
    for number in range(count):
        server.seed(
            {
                "resourceType": "Patient",
                "identifier": [{"system": MRN, "value": f"p{number}"}],
                "text": {"status": "generated", "div": "x" * size},
            }
        )


async def test_pages_each_in_limit_are_refused_past_max_total_together(ctx: FakeContext, server: FhirServer) -> None:
    _large_patients(server, 6, 300_000)
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirSearchOperator(), _config(params={"_count": "1"}, max_total="1MB"), ctx)
    assert raised.value.code == "fhir.search.too_large"
    assert raised.value.error_class is ErrorClass.REJECTED
    assert raised.value.params["limit"] == 1024 * 1024
    assert len(server.requests) == 4


async def test_pages_within_max_total_together_are_read(ctx: FakeContext, server: FhirServer) -> None:
    _large_patients(server, 6, 300_000)
    out = await call_block(FhirSearchOperator(), _config(params={"_count": "1"}, max_total="2MB"), ctx)
    assert isinstance(out, FhirSearchOutput)
    assert (out.count, out.pages, out.complete) == (6, 6, True)


@pytest.mark.parametrize("emit", ["bundle", "pages", "resources"])
async def test_every_page_is_parsed_and_folded_off_the_event_loop(
    ctx: FakeContext, server: FhirServer, monkeypatch: pytest.MonkeyPatch, emit: str
) -> None:
    loop_thread = threading.get_ident()
    threads: list[tuple[str, int]] = []
    parse, fold = client.resource_of, r4.searchset

    def parsing(*args: Any) -> Any:
        threads.append(("parse", threading.get_ident()))
        return parse(*args)

    def folding(*args: Any) -> Any:
        threads.append(("fold", threading.get_ident()))
        return fold(*args)

    monkeypatch.setattr(client, "resource_of", parsing)
    monkeypatch.setattr(search, "searchset", folding)
    _patients(server, 5)
    await call_block(FhirSearchOperator(), _config(emit=emit), ctx)
    assert {name for name, _ in threads} == {"parse", "fold"}
    assert [name for name, thread in threads if thread == loop_thread] == []
