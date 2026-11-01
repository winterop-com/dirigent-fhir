"""``fhir.validate``: FHIR's JSON rules for every resource, and the engine's R4 model where it has one."""

from typing import Any

import pytest

from dirigent_fhir import FhirValidateOperator, FhirValidateOutput
from dirigent_plugin import BlockFailure, ErrorClass
from dirigent_testing import FakeContext, call_block

WEIGHT: dict[str, Any] = {
    "resourceType": "Observation",
    "id": "w1",
    "status": "final",
    "code": {"coding": [{"system": "http://loinc.org", "code": "29463-7"}]},
    "subject": {"reference": "Patient/kari"},
    "valueQuantity": {"value": 82.5, "unit": "kg", "system": "http://unitsofmeasure.org", "code": "kg"},
}

KARI: dict[str, Any] = {
    "resourceType": "Patient",
    "id": "kari",
    "meta": {"profile": ["http://example.org/StructureDefinition/my-patient"]},
    "active": True,
    "gender": "female",
    "birthDate": "1980-04-01",
    "deceasedBoolean": False,
    "name": [{"family": "Nordmann", "given": ["Kari"]}],
    "contained": [{"resourceType": "Organization", "id": "clinic", "name": "Clinic"}],
    "managingOrganization": {"reference": "#clinic"},
    "_birthDate": {"extension": [{"url": "http://example.org/note", "valueString": "stated"}]},
}


async def _validate(ctx: FakeContext, **config: Any) -> FhirValidateOutput:
    output = await call_block(FhirValidateOperator(), {"on_invalid": "report", **config}, ctx)
    assert isinstance(output, FhirValidateOutput)
    return output


def _found(output: FhirValidateOutput) -> list[tuple[str, str]]:
    return [(issue.code, issue.expression[0]) for issue in output.issues]


async def test_a_valid_resource_passes_with_elements_the_engine_does_not_model(ctx: FakeContext) -> None:
    for resource in (WEIGHT, KARI, {"resourceType": "Encounter", "id": "e1", "status": "finished"}):
        output = await _validate(ctx, input=resource)
        assert output.valid, output.issues
        assert output.outcome["issue"] == [
            {"severity": "information", "code": "informational", "diagnostics": "no issues were found"}
        ]


async def test_the_engine_model_judges_the_elements_it_names(ctx: FakeContext) -> None:
    patient = {**KARI, "gender": "mal", "active": "yes", "name": {"family": "Nordmann"}}
    output = await _validate(ctx, input=patient)
    assert not output.valid
    assert sorted(_found(output)) == [
        ("code-invalid", "Patient.gender"),
        ("structure", "Patient.active"),
        ("structure", "Patient.name"),
    ]


async def test_fhir_json_rules_hold_for_a_type_the_engine_does_not_model(ctx: FakeContext) -> None:
    encounter = {
        "resourceType": "Encounter",
        "id": "not an id",
        "status": "",
        "type": [],
        "period": {},
        "subject": {"reference": "patients/1"},
        "serviceProvider": {"reference": "#nowhere"},
        "participant": [None],
        "note": None,
    }
    output = await _validate(ctx, input=encounter)
    assert sorted(_found(output)) == [
        ("not-found", "Encounter.serviceProvider.reference"),
        ("structure", "Encounter.note"),
        ("structure", "Encounter.participant[0]"),
        ("structure", "Encounter.period"),
        ("structure", "Encounter.type"),
        ("value", "Encounter.id"),
        ("value", "Encounter.status"),
        ("value", "Encounter.subject.reference"),
    ]


async def test_a_null_beside_a_primitive_extension_is_allowed(ctx: FakeContext) -> None:
    patient = {
        "resourceType": "Patient",
        "name": [{"given": ["Kari", None], "_given": [None, {"extension": [{"url": "u", "valueString": "x"}]}]}],
    }
    assert (await _validate(ctx, input=patient)).valid


@pytest.mark.parametrize(
    "reference",
    [
        "Patient/1",
        "Patient/1/_history/2",
        "Patient?identifier=http://example.org/mrn|1",
        "https://fhir.example.org/Patient/1",
        "urn:uuid:4b3c3a2e-1d2f-4b0a-9a5f-6e2b1c0d9e8f",
        "#",
    ],
)
async def test_a_reference_a_server_resolves_passes(ctx: FakeContext, reference: str) -> None:
    observation = {**WEIGHT, "subject": {"reference": reference}}
    assert (await _validate(ctx, input=observation)).valid


async def test_every_entry_of_a_bundle_is_validated_and_a_full_url_is_used_once(ctx: FakeContext) -> None:
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"fullUrl": "urn:uuid:1", "resource": WEIGHT},
            {"fullUrl": "urn:uuid:1", "resource": {**KARI, "gender": "x"}},
            {"fullUrl": "urn:uuid:2", "resource": {"id": "no-type"}},
        ],
    }
    output = await _validate(ctx, input=bundle)
    assert output.checked == 4  # the Bundle, two entries, and the organization the patient contains
    assert sorted(_found(output)) == [
        ("code-invalid", "Bundle.entry[1].resource.gender"),
        ("duplicate", "Bundle.entry[1].fullUrl"),
        ("structure", "Bundle.entry[2].resource.resourceType"),
    ]


async def test_a_list_of_resources_is_validated_one_by_one(ctx: FakeContext) -> None:
    output = await _validate(ctx, input=[WEIGHT, "Patient/1", {**WEIGHT, "status": "done"}])
    assert output.checked == 2
    assert _found(output) == [("structure", "$[1]"), ("code-invalid", "$[2].status")]


async def test_a_profile_is_held_to_what_the_engine_conforms_to(ctx: FakeContext) -> None:
    claimed = "http://example.org/StructureDefinition/my-patient"
    assert (await _validate(ctx, input=KARI, profile=claimed)).valid
    assert (await _validate(ctx, input=KARI, profile="http://hl7.org/fhir/StructureDefinition/Patient")).valid
    other = await _validate(ctx, input=[KARI, WEIGHT], profile=claimed)
    assert _found(other) == [("processing", "$[1]")]


async def test_an_invalid_resource_fails_the_step_listing_the_issues(ctx: FakeContext) -> None:
    patient: dict[str, Any] = {**KARI, "gender": "mal", "active": "yes", "name": [], "birthDate": " "}
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirValidateOperator(), {"input": patient}, ctx)
    failure = raised.value
    assert failure.code == "fhir.validate.invalid"
    assert failure.error_class is ErrorClass.REJECTED
    assert failure.params["count"] == 4
    assert len(failure.params["issues"]) == 4
    assert "(and 1 more)" in str(failure)
    assert "Patient.active" in str(failure)


async def test_validation_stops_at_max_issues(ctx: FakeContext) -> None:
    many = [{**WEIGHT, "status": "done"} for _ in range(10)]
    output = await _validate(ctx, input=many, max_issues=3)
    assert len(output.issues) == 3
    assert output.truncated
