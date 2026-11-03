"""What the write blocks share: the resource a step names, made ready for another server, and what a write answers."""

from typing import ClassVar, Final, Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from dirigent_common import BlockModel
from dirigent_fhir.client import Written, portable, writable_type
from dirigent_fhir.messages import TARGET_EMPTY_SEARCH, TARGET_ONE
from dirigent_fhir.r4 import RESOURCE_ID, RESOURCE_TYPE, FhirIssue, JsonObject
from dirigent_fhir.search import query_pairs, refuse_credentials
from dirigent_plugin import ConnectionRef


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


#: The search parameters that shape an answer and match nothing, so a search of only these matches every resource.
RESULT_PARAMETERS: Final = frozenset(
    {
        "_contained",
        "_containedType",
        "_count",
        "_elements",
        "_format",
        "_include",
        "_offset",
        "_pretty",
        "_revinclude",
        "_sort",
        "_summary",
        "_total",
    }
)


def _narrows(params: dict[str, str | list[str]]) -> bool:
    """Whether a search names at least one parameter that matches on something, with a value."""
    return any(name not in RESULT_PARAMETERS and value for name, value in query_pairs(params))


class Targeted(BlockModel):
    """One resource named by its id, or the resources a search matches, on one server."""

    block: ClassVar[str] = ""

    connection: ConnectionRef
    """The code of the fhir connection naming the server."""

    resource_type: str = Field(pattern=RESOURCE_TYPE)
    """The resource's type, such as `Patient` or `Observation`."""

    id: str | None = Field(default=None, pattern=RESOURCE_ID)
    """The resource's id on that server. Set this or `params`."""

    params: dict[str, str | list[str]] | None = Field(default=None, json_schema_extra={"x-dirigent-plain-query": True})
    """A search, such as `{identifier: "http://example.org/mrn|12345"}`, that makes the interaction conditional on
    what it matches; one that names no parameter matching on a value, such as only `_count`, is refused, since it
    matches every resource of the type. Set this or `id`."""

    @field_validator("params")
    @classmethod
    def _params_hold_no_credential(cls, params: dict[str, str | list[str]] | None) -> dict[str, str | list[str]] | None:
        """Refuse a parameter whose name carries a credential."""
        return None if params is None else refuse_credentials(params)

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        """Take an id or a search that names at least one parameter, exactly one of them."""
        if (self.id is None) == (self.params is None):
            raise ValueError(TARGET_ONE.render(block=self.block))
        if self.params is not None and not _narrows(self.params):
            raise ValueError(TARGET_EMPTY_SEARCH.render(resource_type=self.resource_type))
        return self

    @property
    def path(self) -> str:
        """The path the interaction is sent to: the instance, or the type its search runs over."""
        return self.resource_type if self.id is None else f"{self.resource_type}/{self.id}"

    @property
    def query(self) -> list[tuple[str, str]] | None:
        """The search as query pairs, or None for an instance."""
        return None if self.params is None else query_pairs(self.params)
