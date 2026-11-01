"""``fhir.transaction``: carried resources as ``urn:uuid`` entries, their references rewritten between them."""

import json
from typing import Any

import pytest

from dirigent_fhir.search import FhirSearchOperator, FhirSearchOutput
from dirigent_fhir.transaction import FhirTransactionOperator, FhirTransactionOutput
from dirigent_plugin import BlockFailure, ErrorClass
from dirigent_testing import FakeContext, call_block
from fhirserver import CONNECTION, FhirServer

MRN = "http://example.org/mrn"
OBS = "http://example.org/obs"

PATIENT: dict[str, Any] = {
    "resourceType": "Patient",
    "id": "17",
    "meta": {"versionId": "3"},
    "identifier": [{"system": MRN, "value": "12345"}],
}

OBSERVATION: dict[str, Any] = {
    "resourceType": "Observation",
    "id": "90",
    "identifier": [{"system": OBS, "value": "o-1"}],
    "status": "final",
    "code": {"text": "weight"},
    "subject": {"reference": "Patient/17/_history/3"},
    "performer": [{"reference": "http://source.test/fhir/Patient/17"}],
    "extension": [{"url": "http://example.org/x", "valueReference": {"reference": "Patient/17"}}],
}


async def _transaction(ctx: FakeContext, **config: Any) -> FhirTransactionOutput:
    out = await call_block(FhirTransactionOperator(), {"connection": CONNECTION, **config}, ctx)
    assert isinstance(out, FhirTransactionOutput)
    return out


def _sent(server: FhirServer) -> dict[str, Any]:
    posts = [request for request in server.requests if request.method == "POST"]
    sent: dict[str, Any] = json.loads(posts[-1].content)
    return sent


async def test_references_between_carried_resources_become_their_urns(ctx: FakeContext, server: FhirServer) -> None:
    bundle = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            {"fullUrl": "http://source.test/fhir/Patient/17", "resource": PATIENT},
            {"fullUrl": "http://source.test/fhir/Observation/90", "resource": OBSERVATION},
        ],
    }
    out = await _transaction(ctx, bundle=bundle)
    sent = _sent(server)
    assert sent["type"] == "transaction"
    patient, observation = sent["entry"]
    assert patient["fullUrl"].startswith("urn:uuid:")
    assert patient["request"] == {"method": "PUT", "url": "Patient?identifier=http%3A%2F%2Fexample.org%2Fmrn%7C12345"}
    assert "id" not in patient["resource"] and "meta" not in patient["resource"]
    rewritten = observation["resource"]
    assert rewritten["subject"]["reference"] == patient["fullUrl"]
    assert rewritten["performer"][0]["reference"] == patient["fullUrl"]
    assert rewritten["extension"][0]["valueReference"]["reference"] == patient["fullUrl"]
    assert (out.created, out.updated, out.failed) == (2, 0, 0)
    assert [(entry.resource_type, entry.source, entry.status) for entry in out.entries] == [
        ("Patient", "Patient/17", 201),
        ("Observation", "Observation/90", 201),
    ]
    stored_patient = server.all("Patient")[0]
    assert server.all("Observation")[0]["subject"] == {"reference": f"Patient/{stored_patient['id']}"}
    assert out.response["type"] == "transaction-response"


async def test_a_second_run_creates_nothing_new(ctx: FakeContext, server: FhirServer) -> None:
    await _transaction(ctx, resources=[PATIENT, OBSERVATION])
    again = await _transaction(ctx, resources=[PATIENT, OBSERVATION])
    assert (again.created, again.updated) == (0, 2)
    assert (server.count("Patient"), server.count("Observation")) == (1, 1)


async def test_a_search_feeds_a_transaction_on_another_server(ctx: FakeContext, server: FhirServer) -> None:
    server.seed({"resourceType": "Patient", "identifier": [{"system": MRN, "value": "a"}]})
    found = await call_block(
        FhirSearchOperator(), {"connection": CONNECTION, "resource_type": "Patient", "params": {"_count": "5"}}, ctx
    )
    assert isinstance(found, FhirSearchOutput)
    target = FhirServer()
    ctx.handler = target
    out = await _transaction(ctx, bundle=found.bundle)
    assert out.created == 1
    assert target.all("Patient")[0]["identifier"] == [{"system": MRN, "value": "a"}]


async def test_a_reference_to_a_resource_not_carried_fails_by_default(ctx: FakeContext, server: FhirServer) -> None:
    with pytest.raises(BlockFailure) as raised:
        await _transaction(ctx, resources=[OBSERVATION])
    assert raised.value.code == "fhir.transaction.unresolved_reference"
    assert raised.value.params["reference"] == "Patient/17/_history/3"
    assert server.requests == []


async def test_a_reference_not_carried_is_sent_as_written_when_kept(ctx: FakeContext, server: FhirServer) -> None:
    server.store["Patient"] = {"17": {"resourceType": "Patient", "id": "17", "meta": {"versionId": "1"}}}
    await _transaction(ctx, resources=[OBSERVATION], unresolved_references="keep")
    sent = _sent(server)["entry"][0]["resource"]
    assert sent["subject"] == {"reference": "Patient/17/_history/3"}


async def test_contained_conditional_and_urn_references_are_left_alone(ctx: FakeContext, server: FhirServer) -> None:
    resource = {
        **OBSERVATION,
        "subject": {"reference": "Patient?identifier=http://example.org/mrn|12345"},
        "performer": [{"reference": "#p1"}],
        "extension": [{"url": "http://example.org/x", "valueReference": {"reference": "urn:oid:1.2.3"}}],
    }
    with pytest.raises(BlockFailure):
        await _transaction(ctx, resources=[resource])
    sent = _sent(server)["entry"][0]["resource"]
    assert sent["subject"] == resource["subject"]
    assert sent["performer"] == resource["performer"]
    assert sent["extension"] == resource["extension"]


async def test_a_conditional_create_transaction_leaves_a_match_as_it_is(ctx: FakeContext, server: FhirServer) -> None:
    await _transaction(ctx, resources=[PATIENT], write="create")
    changed = {**PATIENT, "gender": "female"}
    again = await _transaction(ctx, resources=[changed], write="create")
    sent = _sent(server)["entry"][0]
    assert sent["request"] == {
        "method": "POST",
        "url": "Patient",
        "ifNoneExist": "identifier=http%3A%2F%2Fexample.org%2Fmrn%7C12345",
    }
    assert (again.created, again.updated) == (0, 1)
    assert "gender" not in server.all("Patient")[0]


async def test_a_resource_without_an_identifier_is_refused(ctx: FakeContext, server: FhirServer) -> None:
    with pytest.raises(BlockFailure) as raised:
        await _transaction(ctx, resources=[{"resourceType": "Patient", "id": "1"}])
    assert raised.value.code == "fhir.resource.no_identifier"
    assert raised.value.params["resource"] == "Patient/1"


async def test_the_same_resource_carried_twice_is_refused(ctx: FakeContext) -> None:
    with pytest.raises(BlockFailure) as raised:
        await _transaction(ctx, resources=[PATIENT, PATIENT])
    assert raised.value.code == "fhir.transaction.duplicate"


async def test_a_refused_transaction_writes_nothing(ctx: FakeContext, server: FhirServer) -> None:
    server.seed({"resourceType": "Observation", "identifier": [{"system": OBS, "value": "o-1"}]})
    server.seed({"resourceType": "Observation", "identifier": [{"system": OBS, "value": "o-1"}]})
    with pytest.raises(BlockFailure) as raised:
        await _transaction(ctx, resources=[PATIENT, OBSERVATION])
    assert raised.value.code == "fhir.answered"
    assert raised.value.error_class is ErrorClass.REJECTED
    assert raised.value.params["status"] == 412
    assert server.count("Patient") == 0


async def test_a_batch_entry_that_fails_fails_the_step(ctx: FakeContext, server: FhirServer) -> None:
    server.seed({"resourceType": "Observation", "identifier": [{"system": OBS, "value": "o-1"}]})
    server.seed({"resourceType": "Observation", "identifier": [{"system": OBS, "value": "o-1"}]})
    with pytest.raises(BlockFailure) as raised:
        await _transaction(ctx, resources=[PATIENT, OBSERVATION], mode="batch")
    assert raised.value.code == "fhir.transaction.entries_failed"
    assert raised.value.params["failed"] == 1
    assert "Observation/90 412 error multiple-matches" in raised.value.message
    assert server.count("Patient") == 1


async def test_a_batch_entry_that_fails_is_counted_when_reported(ctx: FakeContext, server: FhirServer) -> None:
    server.seed({"resourceType": "Observation", "identifier": [{"system": OBS, "value": "o-1"}]})
    server.seed({"resourceType": "Observation", "identifier": [{"system": OBS, "value": "o-1"}]})
    out = await _transaction(ctx, resources=[PATIENT, OBSERVATION], mode="batch", on_failed="report")
    assert (out.created, out.failed) == (1, 1)
    assert out.entries[1].issues[0].code == "multiple-matches"


def test_resources_or_a_bundle_exactly() -> None:
    configs: list[dict[str, Any]] = [{}, {"resources": [], "bundle": {}}]
    for config in configs:
        with pytest.raises(ValueError):
            FhirTransactionOperator.config_model.model_validate({"connection": CONNECTION, **config})
