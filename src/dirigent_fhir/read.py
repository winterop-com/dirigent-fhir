"""``fhir.read``: read one resource by its type and id."""

from typing import ClassVar

from pydantic import BaseModel, Field, JsonValue

from dirigent_common import BlockModel
from dirigent_fhir.connection import client_for
from dirigent_fhir.r4 import RESOURCE_ID, RESOURCE_TYPE
from dirigent_plugin import ConnectionRef, Operator, OperatorSpec, StepContext


class FhirReadConfig(BlockModel):
    """Which resource to read."""

    connection: ConnectionRef
    """The code of the fhir connection naming the server."""

    resource_type: str = Field(pattern=RESOURCE_TYPE)
    """The resource's type, such as `Patient` or `Observation`."""

    id: str = Field(pattern=RESOURCE_ID)
    """The resource's id on that server."""


class FhirReadOutput(BlockModel):
    """The resource, as the server holds it."""

    resource: dict[str, JsonValue]
    """The resource as plain FHIR JSON."""

    version_id: str | None = None
    """The version the server holds, from `meta.versionId`."""


class FhirReadOperator(Operator[FhirReadConfig, FhirReadOutput]):
    """Reads one resource. It only reads."""

    spec = OperatorSpec(id="fhir.read", summary="Read one FHIR resource by type and id.", idempotent=True)
    config_model: ClassVar[type[BaseModel]] = FhirReadConfig
    output_model: ClassVar[type[BaseModel]] = FhirReadOutput

    async def execute(self, config: FhirReadConfig, ctx: StepContext) -> FhirReadOutput:
        """Read the resource and hand it on."""
        async with client_for(ctx, config.connection) as fhir:
            resource = await fhir.read(config.resource_type, config.id)
        meta = resource.get("meta")
        version = meta.get("versionId") if isinstance(meta, dict) else None
        return FhirReadOutput(resource=resource, version_id=version if isinstance(version, str) else None)
