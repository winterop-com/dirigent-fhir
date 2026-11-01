"""An ``OperationOutcome`` classified into a step failure, read through the engine's R4 model where it fits."""

from typing import Any

import httpx2
import pytest
from dhis2w_fhir_engine.r4.resources import OperationOutcome

from dirigent_fhir.client import error_class, refusal
from dirigent_fhir.r4 import FhirIssue, fhir_version, issues_of, project
from dirigent_plugin import ErrorClass


def _outcome(*issues: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"resourceType": "OperationOutcome", "issue": list(issues), **extra}


def _answer(status: int, body: Any) -> httpx2.Response:
    return httpx2.Response(status, json=body, request=httpx2.Request("PUT", "http://fhir.test/fhir/Patient"))


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (400, "invalid", ErrorClass.REJECTED),
        (404, "not-found", ErrorClass.REJECTED),
        (412, "multiple-matches", ErrorClass.REJECTED),
        (422, "business-rule", ErrorClass.REJECTED),
        (409, "lock-error", ErrorClass.TRANSIENT),
        (400, "throttled", ErrorClass.TRANSIENT),
        (429, "throttled", ErrorClass.TRANSIENT),
        (500, "exception", ErrorClass.TRANSIENT),
        (503, "transient", ErrorClass.TRANSIENT),
    ],
)
def test_an_outcome_is_classified_by_its_status_and_its_issues(status: int, code: str, expected: ErrorClass) -> None:
    failure = refusal(_answer(status, _outcome({"severity": "error", "code": code})), "PUT Patient")
    assert failure.error_class is expected
    assert failure.code == "fhir.answered"


def test_a_warning_never_makes_a_refusal_transient() -> None:
    issues = [FhirIssue(severity="warning", code="transient")]
    assert error_class(400, issues) is ErrorClass.REJECTED


def test_a_refusal_names_the_first_issues_and_counts_the_rest() -> None:
    issues = [
        {"severity": "error", "code": "required", "diagnostics": f"missing {n}", "expression": [f"Patient.x{n}"]}
        for n in range(5)
    ]
    failure = refusal(_answer(422, _outcome(*issues)), "PUT Patient")
    assert failure.message == (
        "PUT Patient answered 422: error required: missing 0 (at Patient.x0); "
        "error required: missing 1 (at Patient.x1); error required: missing 2 (at Patient.x2) (and 2 more)"
    )
    assert failure.params["issues"] == [
        {"expression": [f"Patient.x{n}"], "severity": "error", "code": "required", "diagnostics": f"missing {n}"}
        for n in range(5)
    ]


def test_an_outcome_with_narrative_and_unmodelled_keys_is_read_through_the_engine_model() -> None:
    document = _outcome(
        {"severity": "error", "code": "processing", "details": {"text": "said in details"}, "location": ["x"]},
        text={"status": "generated", "div": "<div/>"},
        meta={"lastUpdated": "2026-01-01"},
    )
    projected = project(OperationOutcome, document)
    assert OperationOutcome.model_validate(projected).issue is not None
    assert issues_of(document) == [FhirIssue(severity="error", code="processing", diagnostics="said in details")]


def test_an_issue_the_engine_model_refuses_is_read_loosely() -> None:
    document = _outcome({"severity": "error", "code": "made-up-code", "diagnostics": "odd"})
    assert issues_of(document) == [FhirIssue(severity="error", code="made-up-code", diagnostics="odd")]


def test_a_body_that_is_not_an_outcome_has_no_issues() -> None:
    assert issues_of({"resourceType": "Patient"}) == []
    assert issues_of("text") == []


def test_the_fhir_version_is_read_from_a_capability_statement() -> None:
    statement = {
        "resourceType": "CapabilityStatement",
        "fhirVersion": "4.0.1",
        "publisher": "x",
        "software": {"name": "s", "releaseDate": "2026"},
    }
    assert fhir_version(statement) == "4.0.1"
    assert fhir_version({"resourceType": "CapabilityStatement", "status": "bogus", "fhirVersion": "4.3.0"}) == "4.3.0"
