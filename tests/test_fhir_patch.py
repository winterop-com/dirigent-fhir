"""``fhir.patch``: a JSON Patch or a FHIRPath Patch, by id or on the one resource a search matches."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from dirigent_fhir.patch import FhirPatchConfig, FhirPatchOperator
from dirigent_fhir.writes import FhirWriteOutput
from dirigent_plugin import BlockFailure, ErrorClass
from dirigent_testing import FakeContext, call_block
from fhirserver import CONNECTION, FhirServer

MRN = "http://example.org/mrn"

FHIRPATH_PATCH: dict[str, Any] = {
    "resourceType": "Parameters",
    "parameter": [
        {
            "name": "operation",
            "part": [
                {"name": "type", "valueCode": "replace"},
                {"name": "path", "valueString": "Patient.active"},
                {"name": "value", "valueBoolean": False},
            ],
        }
    ],
}


def _held(server: FhirServer, value: str = "1") -> str:
    patient = {"resourceType": "Patient", "active": True, "identifier": [{"system": MRN, "value": value}]}
    return str(server.seed(patient)["id"])


async def _patch(ctx: FakeContext, **config: Any) -> FhirWriteOutput:
    out = await call_block(FhirPatchOperator(), {"connection": CONNECTION, "resource_type": "Patient", **config}, ctx)
    assert isinstance(out, FhirWriteOutput)
    return out


async def test_a_json_patch_is_sent_as_one_and_answers_the_patched_resource(
    ctx: FakeContext, server: FhirServer
) -> None:
    held = _held(server)
    operations = [{"op": "replace", "path": "/active", "value": False}, {"op": "add", "path": "/gender", "value": "x"}]
    out = await _patch(ctx, id=held, json_patch=operations)
    assert (out.outcome, out.status, out.id, out.version_id) == ("updated", 200, held, "2")
    assert out.resource is not None and out.resource["active"] is False
    sent = server.requests[0]
    assert (sent.method, sent.url.path) == ("PATCH", f"/fhir/Patient/{held}")
    assert sent.headers["Content-Type"] == "application/json-patch+json"
    assert json.loads(sent.content) == operations


async def test_a_fhirpath_patch_is_sent_as_a_parameters_resource(ctx: FakeContext, server: FhirServer) -> None:
    held = _held(server)
    await _patch(ctx, id=held, fhirpath_patch=FHIRPATH_PATCH)
    sent = server.requests[0]
    assert sent.headers["Content-Type"] == "application/fhir+json"
    assert json.loads(sent.content) == FHIRPATH_PATCH


async def test_a_conditional_patch_patches_the_one_resource_the_search_matches(
    ctx: FakeContext, server: FhirServer
) -> None:
    held = _held(server)
    out = await _patch(ctx, params={"identifier": f"{MRN}|1"}, json_patch=[{"op": "remove", "path": "/active"}])
    assert out.id == held
    assert server.requests[0].url.path == "/fhir/Patient"
    assert server.requests[0].url.params["identifier"] == f"{MRN}|1"
    assert "active" not in server.all("Patient")[0]


async def test_a_conditional_patch_matching_many_is_refused_by_the_server(ctx: FakeContext, server: FhirServer) -> None:
    _held(server, "1")
    _held(server, "2")
    with pytest.raises(BlockFailure) as raised:
        await _patch(ctx, params={"identifier": f"{MRN}|"}, json_patch=[{"op": "remove", "path": "/active"}])
    assert raised.value.error_class is ErrorClass.REJECTED
    assert raised.value.params["status"] == 412


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "replace", "path": "/active"},
        {"op": "move", "path": "/a"},
        {"op": "launch", "path": "/a", "value": 1},
        {"path": "/a", "value": 1},
        {"op": "add", "value": 1},
    ],
)
def test_an_entry_that_is_no_json_patch_operation_is_refused(operation: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="json_patch entry 0 is not a JSON Patch operation"):
        FhirPatchConfig.model_validate(
            {"connection": CONNECTION, "resource_type": "Patient", "id": "1", "json_patch": [operation]}
        )


#: FHIRPath Patches that are not a Parameters resource of operations.
NOT_A_FHIRPATH_PATCH: list[dict[str, Any]] = [
    {"resourceType": "Patient"},
    {"resourceType": "Parameters"},
    {"resourceType": "Parameters", "parameter": [{}]},
]


@pytest.mark.parametrize("patch", NOT_A_FHIRPATH_PATCH)
def test_a_fhirpath_patch_that_is_no_parameters_of_operations_is_refused(patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="fhirpath_patch is a Parameters resource"):
        FhirPatchConfig.model_validate(
            {"connection": CONNECTION, "resource_type": "Patient", "id": "1", "fhirpath_patch": patch}
        )


@pytest.mark.parametrize("bodies", [{}, {"json_patch": [], "fhirpath_patch": FHIRPATH_PATCH}])
def test_a_patch_carries_exactly_one_body(bodies: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="json_patch or fhirpath_patch, exactly one"):
        FhirPatchConfig.model_validate({"connection": CONNECTION, "resource_type": "Patient", "id": "1", **bodies})


def test_a_conditional_patch_with_a_search_that_matches_everything_is_refused() -> None:
    with pytest.raises(ValidationError, match="matches every Patient"):
        FhirPatchConfig.model_validate(
            {"connection": CONNECTION, "resource_type": "Patient", "params": {}, "json_patch": []}
        )
