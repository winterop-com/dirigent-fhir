"""``fhir.operation``: a named operation at the system, a type or an instance, and what it answers."""

import json
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError

from dirigent_fhir.operation import FhirOperationConfig, FhirOperationOperator, FhirOperationOutput
from dirigent_plugin import BlockFailure, ErrorClass
from dirigent_testing import FakeContext, call_block
from fhirserver import BASE_URL, CONNECTION, FhirServer, outcome


async def _operation(ctx: FakeContext, **config: Any) -> FhirOperationOutput:
    out = await call_block(FhirOperationOperator(), {"connection": CONNECTION, **config}, ctx)
    assert isinstance(out, FhirOperationOutput)
    return out


@pytest.mark.parametrize(
    ("target", "level", "path"),
    [
        ({}, "system", "/fhir/$meta"),
        ({"resource_type": "Patient"}, "type", "/fhir/Patient/$meta"),
        ({"resource_type": "Patient", "id": "p1"}, "instance", "/fhir/Patient/p1/$meta"),
    ],
)
async def test_an_operation_is_invoked_at_its_level(
    ctx: FakeContext, server: FhirServer, target: dict[str, str], level: str, path: str
) -> None:
    out = await _operation(ctx, operation="$meta", **target)
    assert out.level == level
    assert out.resource_type == "Parameters"
    assert (out.pages, out.complete) == (1, True)
    assert (server.requests[0].method, server.requests[0].url.path) == ("GET", path)


async def test_simple_parameters_go_in_the_query(ctx: FakeContext, server: FhirServer) -> None:
    url = "http://hl7.org/fhir/ValueSet/administrative-gender"
    await _operation(ctx, operation="$expand", resource_type="ValueSet", params={"url": url, "filter": ["a", "b"]})
    assert server.requests[0].url.params.get_list("filter") == ["a", "b"]
    assert server.requests[0].url.params["url"] == url


async def test_an_input_is_posted_as_fhir_json(ctx: FakeContext, server: FhirServer) -> None:
    resource = {"resourceType": "Patient", "active": True}
    out = await _operation(ctx, operation="$validate", resource_type="Patient", input=resource)
    sent = server.requests[0]
    assert sent.method == "POST"
    assert sent.headers["Content-Type"] == "application/fhir+json"
    assert json.loads(sent.content) == resource
    assert out.resource is not None
    assert {"name": "body", "resource": resource} in out.resource["parameter"]  # type: ignore[operator]


async def test_an_operation_outcome_answer_is_handed_on_with_its_issues(ctx: FakeContext, server: FhirServer) -> None:
    server.script(lambda request: outcome(200, "required", "Observation.status: minimum required = 1"))
    out = await _operation(
        ctx, operation="$validate", resource_type="Observation", input={"resourceType": "Observation"}
    )
    assert out.resource_type == "OperationOutcome"
    assert [issue.code for issue in out.issues] == ["required"]


async def test_a_paged_bundle_answer_is_followed_and_folded(ctx: FakeContext, server: FhirServer) -> None:
    def everything(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params.get("page", "1"))
        bundle: dict[str, Any] = {
            "resourceType": "Bundle",
            "type": "searchset",
            "entry": [{"resource": {"resourceType": "Observation", "id": f"o{page}", "status": "final"}}],
        }
        if page < 3:
            bundle["link"] = [{"relation": "next", "url": f"{BASE_URL}/Patient/p1/$everything?page={page + 1}"}]
        return httpx2.Response(200, json=bundle)

    server.script(everything)
    out = await _operation(ctx, operation="$everything", resource_type="Patient", id="p1", max_pages=2)
    assert (out.pages, out.complete) == (2, False)
    folded = json.loads(json.dumps(out.resource))
    assert [entry["resource"]["id"] for entry in folded["entry"]] == ["o1", "o2"]


async def test_a_next_link_off_the_origin_is_not_followed(ctx: FakeContext, server: FhirServer) -> None:
    bundle = {
        "resourceType": "Bundle",
        "type": "searchset",
        "link": [{"relation": "next", "url": "http://evil.test/x"}],
    }
    server.script(lambda request: httpx2.Response(200, json=bundle))
    with pytest.raises(BlockFailure) as raised:
        await _operation(ctx, operation="$everything", resource_type="Patient", id="p1", max_pages=2)
    assert raised.value.code == "fhir.off_origin"
    assert len(server.requests) == 1


async def test_an_answer_with_no_body_is_no_page(ctx: FakeContext, server: FhirServer) -> None:
    server.script(lambda request: httpx2.Response(204))
    out = await _operation(ctx, operation="$reindex", method="POST")
    assert (out.resource, out.pages, out.complete) == (None, 0, True)


async def test_a_refusal_carries_the_servers_words(ctx: FakeContext, server: FhirServer) -> None:
    server.script(lambda request: outcome(404, "not-supported", "Unknown operation $nope"))
    with pytest.raises(BlockFailure) as raised:
        await _operation(ctx, operation="$nope")
    assert raised.value.error_class is ErrorClass.REJECTED
    assert "Unknown operation $nope" in raised.value.message


@pytest.mark.parametrize(
    ("config", "said"),
    [
        ({"operation": "everything"}, "should match pattern"),
        ({"operation": "$every/../thing"}, "should match pattern"),
        ({"operation": "$meta", "id": "p1"}, "names resource_type beside its id"),
        ({"operation": "$validate", "method": "GET", "input": {"resourceType": "Patient"}}, "a GET carries no body"),
        ({"operation": "$meta", "params": {"api_key": "x"}}, "carries a credential"),
    ],
)
def test_an_inconsistent_invocation_is_refused(config: dict[str, Any], said: str) -> None:
    with pytest.raises(ValidationError, match=said):
        FhirOperationConfig.model_validate({"connection": CONNECTION, **config})
