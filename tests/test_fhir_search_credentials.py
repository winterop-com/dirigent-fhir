"""``fhir.search`` refuses a credential in its parameters, and keeps what a search really sends."""

import pytest
from pydantic import ValidationError

from dirigent_fhir.search import FhirSearchConfig


@pytest.mark.parametrize("name", ["access_token", "api_key", "apiKey", "password", "client_secret", "token", "auth"])
def test_a_parameter_named_for_a_credential_is_refused_without_repeating_it(name: str) -> None:
    with pytest.raises(ValidationError) as refused:
        FhirSearchConfig.model_validate(
            {"connection": "fhir", "resource_type": "Patient", "params": {name: "DUMMYSECRET"}}
        )
    assert f"the {name} search parameter carries a credential" in str(refused.value)
    assert "DUMMYSECRET" not in str(refused.value)


@pytest.mark.parametrize(
    "name",
    ["identifier", "_count", "_sort", "_id", "_security", "_tag", "patient", "code", "date", "_lastUpdated", "key"],
)
def test_a_parameter_a_search_really_sends_is_kept(name: str) -> None:
    config = FhirSearchConfig(connection="fhir", resource_type="Patient", params={name: "v"})
    assert config.params == {name: "v"}
