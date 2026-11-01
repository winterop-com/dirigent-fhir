"""``fhir.read``: one resource by type and id, and a refusal in the server's own words."""

from typing import Any

import pytest

from dirigent_fhir.read import FhirReadOperator, FhirReadOutput
from dirigent_plugin import BlockFailure, ErrorClass
from dirigent_testing import FakeContext, call_block
from fhirserver import CONNECTION, FhirServer

PATIENT: dict[str, Any] = {"resourceType": "Patient", "name": [{"family": "Nordmann"}]}


async def test_a_resource_is_read_as_plain_fhir_json(ctx: FakeContext, server: FhirServer) -> None:
    stored = server.seed(PATIENT)
    out = await call_block(
        FhirReadOperator(), {"connection": CONNECTION, "resource_type": "Patient", "id": stored["id"]}, ctx
    )
    assert isinstance(out, FhirReadOutput)
    assert out.resource == stored
    assert out.version_id == "1"
    request = server.requests[-1]
    assert str(request.url) == f"http://fhir.test/fhir/Patient/{stored['id']}"
    assert request.headers["Accept"] == "application/fhir+json"
    assert "Authorization" not in request.headers


async def test_a_missing_resource_is_refused_with_the_operation_outcome(ctx: FakeContext) -> None:
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirReadOperator(), {"connection": CONNECTION, "resource_type": "Patient", "id": "nope"}, ctx)
    assert raised.value.code == "fhir.answered"
    assert raised.value.error_class is ErrorClass.REJECTED
    assert raised.value.params["status"] == 404
    assert raised.value.params["remote"] == "error not-found: Resource Patient/nope is not known"


async def test_an_answer_of_another_type_is_refused(ctx: FakeContext, server: FhirServer) -> None:
    stored = server.seed(PATIENT)
    server.script(lambda request: server._json(200, {"resourceType": "Bundle"}) if request.method == "GET" else None)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(BlockFailure) as raised:
        await call_block(
            FhirReadOperator(), {"connection": CONNECTION, "resource_type": "Patient", "id": stored["id"]}, ctx
        )
    assert raised.value.code == "fhir.not_a_resource"


@pytest.mark.parametrize("bad", ["patient", "Patient/1", ""])
def test_a_resource_type_must_be_one(bad: str) -> None:
    with pytest.raises(ValueError):
        FhirReadOperator.config_model.model_validate({"connection": CONNECTION, "resource_type": bad, "id": "1"})


def test_an_id_must_be_a_fhir_id() -> None:
    with pytest.raises(ValueError):
        FhirReadOperator.config_model.model_validate(
            {"connection": CONNECTION, "resource_type": "Patient", "id": "../metadata"}
        )
