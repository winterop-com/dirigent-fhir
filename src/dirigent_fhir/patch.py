"""``fhir.patch``: change part of one resource with a JSON Patch or a FHIRPath Patch."""

from typing import ClassVar, Final, Self

from pydantic import BaseModel, JsonValue, field_validator, model_validator

from dirigent_fhir.connection import client_for
from dirigent_fhir.messages import PATCH_BAD_OPERATION, PATCH_NOT_PARAMETERS, PATCH_ONE_BODY
from dirigent_fhir.r4 import as_object, resource_type
from dirigent_fhir.writes import FhirWriteOutput, Targeted, output_of
from dirigent_plugin import Operator, OperatorSpec, StepContext

#: The media type a JSON Patch document is sent as.
JSON_PATCH: Final = "application/json-patch+json"

#: The media type a FHIRPath Patch is sent as: it is a Parameters resource.
FHIRPATH_PATCH: Final = "application/fhir+json"

#: What each JSON Patch op carries beside `op` and `path`.
JSON_PATCH_OPS: Final = {
    "add": "value",
    "remove": None,
    "replace": "value",
    "move": "from",
    "copy": "from",
    "test": "value",
}


class FhirPatchConfig(Targeted):
    """Which resource to patch, and the patch."""

    block: ClassVar[str] = "fhir.patch"

    json_patch: list[dict[str, JsonValue]] | None = None
    """A JSON Patch, RFC 6902: a list of operations such as `{op: replace, path: /active, value: false}`. Set
    this or `fhirpath_patch`."""

    fhirpath_patch: dict[str, JsonValue] | None = None
    """A FHIRPath Patch: a `Parameters` resource whose parameters are each named `operation`. Set this or
    `json_patch`."""

    @field_validator("json_patch")
    @classmethod
    def _json_patch_operations(cls, operations: list[dict[str, JsonValue]] | None) -> list[dict[str, JsonValue]] | None:
        """Refuse an entry that is not a JSON Patch operation."""
        for index, operation in enumerate(operations or []):
            op = operation.get("op")
            needs = JSON_PATCH_OPS.get(op) if isinstance(op, str) else "op"
            if (
                not isinstance(op, str)
                or op not in JSON_PATCH_OPS
                or not isinstance(operation.get("path"), str)
                or (needs is not None and needs not in operation)
                or (needs == "from" and not isinstance(operation.get("from"), str))
            ):
                raise ValueError(PATCH_BAD_OPERATION.render(index=index))
        return operations

    @field_validator("fhirpath_patch")
    @classmethod
    def _fhirpath_patch_is_parameters(cls, patch: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        """Refuse a FHIRPath Patch that is not a Parameters resource of operations."""
        if patch is None:
            return None
        parameters = patch.get("parameter")
        named = [as_object(item) or {} for item in parameters] if isinstance(parameters, list) else []
        if resource_type(patch) != "Parameters" or not named or any(one.get("name") != "operation" for one in named):
            raise ValueError(PATCH_NOT_PARAMETERS.render())
        return patch

    @model_validator(mode="after")
    def _one_body(self) -> Self:
        """Carry a JSON Patch or a FHIRPath Patch, exactly one of them."""
        if (self.json_patch is None) == (self.fhirpath_patch is None):
            raise ValueError(PATCH_ONE_BODY.render())
        return self


class FhirPatchOperator(Operator[FhirPatchConfig, FhirWriteOutput]):
    """Patches one resource, by its id or the one a search matches. It writes."""

    spec = OperatorSpec(id="fhir.patch", summary="Patch a FHIR resource with JSON Patch or FHIRPath Patch.")
    config_model: ClassVar[type[BaseModel]] = FhirPatchConfig
    output_model: ClassVar[type[BaseModel]] = FhirWriteOutput

    async def execute(self, config: FhirPatchConfig, ctx: StepContext) -> FhirWriteOutput:
        """Send the patch, and answer the resource as the server stored it."""
        body: JsonValue
        if config.json_patch is not None:
            body, content_type = list[JsonValue](config.json_patch), JSON_PATCH
        else:
            body, content_type = config.fhirpath_patch, FHIRPATH_PATCH
        async with client_for(ctx, config.connection) as fhir:
            written = await fhir.patch(config.path, body, content_type, config.query)
        return output_of(written, "updated")
