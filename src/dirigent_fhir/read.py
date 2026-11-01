"""``fhir.read``: read one resource by its type and id."""

from typing import ClassVar, Final

from pydantic import BaseModel, Field, JsonValue

from dirigent_common import BlockModel
from dirigent_fhir.connection import client_for
from dirigent_plugin import ConnectionRef, Operator, OperatorSpec, StepContext

#: A FHIR resource type as it appears in a path: an upper-case letter, then letters.
RESOURCE_TYPE: Final = r"^[A-Z][A-Za-z]{1,63}$"

#: A FHIR logical id: up to 64 letters, digits, hyphens and dots.
RESOURCE_ID: Final = r"^[A-Za-z0-9\-.]{1,64}$"


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
