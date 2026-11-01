"""``fhir.update``: write one resource over the one matching its identifier, or create it."""

from typing import ClassVar

from pydantic import BaseModel, JsonValue

from dirigent_common import BlockModel
from dirigent_fhir.client import first_identifier, identifier_search
from dirigent_fhir.connection import client_for
from dirigent_fhir.messages import IDENTIFIER_SYSTEM, NO_IDENTIFIER
from dirigent_fhir.r4 import resource_type
from dirigent_fhir.writes import FhirWriteOutput, outgoing, output_of
from dirigent_plugin import BlockFailure, ConnectionRef, ErrorClass, Operator, OperatorSpec, StepContext


class FhirUpdateConfig(BlockModel):
    """The resource to write, and which of its identifiers it is matched on."""

    connection: ConnectionRef
    """The code of the fhir connection naming the server."""

    resource: dict[str, JsonValue]
    """The resource as plain FHIR JSON; its `id` and version metadata are not sent, so the source server's id
    never decides what is overwritten."""

    identifier_system: str | None = None
    """The system of the identifier the resource is matched on; the first identifier with both a system and a
    value when unset."""


class FhirUpdateOperator(Operator[FhirUpdateConfig, FhirWriteOutput]):
    """Writes one resource with a conditional update on its identifier. It writes."""

    spec = OperatorSpec(id="fhir.update", summary="Update a FHIR resource matched on its identifier.", idempotent=True)
    config_model: ClassVar[type[BaseModel]] = FhirUpdateConfig
    output_model: ClassVar[type[BaseModel]] = FhirWriteOutput

    async def execute(self, config: FhirUpdateConfig, ctx: StepContext) -> FhirWriteOutput:
        """Put the resource where its identifier matches, and say whether the server created or updated it."""
        resource = outgoing(config.resource)
        identifier = first_identifier(resource, config.identifier_system)
        if identifier is None:
            system = IDENTIFIER_SYSTEM.render(system=config.identifier_system) if config.identifier_system else ""
            raise BlockFailure(
                NO_IDENTIFIER, error_class=ErrorClass.REJECTED, resource=resource_type(resource), system=system
            )
        async with client_for(ctx, config.connection) as fhir:
            written = await fhir.update_where(resource, identifier_search(*identifier))
        return output_of(written, "created" if written.status == 201 else "updated")
