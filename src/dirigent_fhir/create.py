"""``fhir.create``: create one resource, conditionally on nothing matching ``if_none_exist``."""

from typing import ClassVar

from pydantic import BaseModel, JsonValue

from dirigent_common import BlockModel
from dirigent_fhir.connection import client_for
from dirigent_fhir.writes import FhirWriteOutput, outgoing, output_of
from dirigent_plugin import ConnectionRef, Operator, OperatorSpec, StepContext


class FhirCreateConfig(BlockModel):
    """The resource to create, and the search that says it is already there."""

    connection: ConnectionRef
    """The code of the fhir connection naming the server."""

    resource: dict[str, JsonValue]
    """The resource as plain FHIR JSON; its `id` and version metadata are not sent, since the server assigns its own."""

    if_none_exist: str | None = None
    """A search, such as `identifier=http://example.org/mrn|12345`, that makes the create conditional: when one
    resource matches, nothing is written and the step answers `existing`. Unset, every run creates another."""


class FhirCreateOperator(Operator[FhirCreateConfig, FhirWriteOutput]):
    """Creates one resource. It writes."""

    spec = OperatorSpec(id="fhir.create", summary="Create a FHIR resource, conditionally with ifNoneExist.")
    config_model: ClassVar[type[BaseModel]] = FhirCreateConfig
    output_model: ClassVar[type[BaseModel]] = FhirWriteOutput

    async def execute(self, config: FhirCreateConfig, ctx: StepContext) -> FhirWriteOutput:
        """Post the resource and say whether the server made it or found it."""
        async with client_for(ctx, config.connection) as fhir:
            written = await fhir.create(outgoing(config.resource), config.if_none_exist)
        return output_of(written, "created" if written.status == 201 else "existing")
