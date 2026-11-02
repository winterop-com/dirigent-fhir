"""What the write blocks share: the resource a step names, made ready for another server, and what a write answers."""

from typing import Literal

from pydantic import Field, JsonValue

from dirigent_common import BlockModel
from dirigent_fhir.client import Written, portable, writable_type
from dirigent_fhir.r4 import FhirIssue, JsonObject


def outgoing(resource: JsonObject) -> JsonObject:
    """The resource a write sends: one that names a FHIR type, without its source id or version metadata."""
    writable_type(resource)
    return portable(resource)


class FhirWriteOutput(BlockModel):
    """What the server did with the resource, and the resource as it stored it."""

    outcome: Literal["created", "updated", "existing"]
    """`created` when the server made a new resource, `updated` when it wrote over the one that matched, and
    `existing` when a conditional create found one already there and wrote nothing."""

    status: int
    """The HTTP status the server answered with."""

    id: str | None = None
    """The id the server holds the resource under."""

    version_id: str | None = None
    """The version the server holds."""

    location: str | None = None
    """Where the server says the resource lives."""

    resource: dict[str, JsonValue] | None = None
    """The resource as the server stored it, when it answered with it."""

    issues: list[FhirIssue] = Field(default_factory=list[FhirIssue])
    """What the server said about the write in an `OperationOutcome`, such as a warning."""


def output_of(written: Written, outcome: Literal["created", "updated", "existing"]) -> FhirWriteOutput:
    """Carry what a write answered as the block's output."""
    return FhirWriteOutput(
        outcome=outcome,
        status=written.status,
        id=written.id,
        version_id=written.version_id,
        location=written.location,
        resource=written.resource,
        issues=written.issues,
    )
