"""``fhir.operation``: invoke a named FHIR operation at the system, a type, or one instance."""

import asyncio
from typing import ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from dirigent_common import BlockModel, Size
from dirigent_fhir.client import SearchPages
from dirigent_fhir.connection import client_for
from dirigent_fhir.messages import OPERATION_ID_WITHOUT_TYPE, OPERATION_INPUT_ON_GET
from dirigent_fhir.r4 import RESOURCE_ID, RESOURCE_TYPE, FhirIssue, issues_of, resource_type, searchset
from dirigent_fhir.search import query_pairs, refuse_credentials
from dirigent_plugin import ConnectionRef, Operator, OperatorSpec, StepContext

#: An operation's name as a URL spells it: a dollar sign, a letter, then letters, digits and hyphens.
OPERATION_NAME: Final = r"^\$[A-Za-z][A-Za-z0-9\-]{0,63}$"


class FhirOperationConfig(BlockModel):
    """The operation, where it is invoked, and what it is handed."""

    model_config = ConfigDict(hide_input_in_errors=True)

    connection: ConnectionRef
    """The code of the fhir connection naming the server."""

    operation: str = Field(pattern=OPERATION_NAME)
    """The operation's name with its dollar sign, such as `$everything`, `$validate` or `$expand`."""

    resource_type: str | None = Field(default=None, pattern=RESOURCE_TYPE)
    """The type the operation is invoked on, such as `Patient`; unset, it is invoked on the whole system."""

    id: str | None = Field(default=None, pattern=RESOURCE_ID)
    """The id of the one resource the operation is invoked on, beside `resource_type`; unset, it is invoked on the
    type."""

    params: dict[str, str | list[str]] = Field(
        default_factory=dict[str, str | list[str]], json_schema_extra={"x-dirigent-plain-query": True}
    )
    """The operation's simple parameters, sent in the query, such as `{_count: "50"}` for `$everything` or
    `{url: "http://hl7.org/fhir/ValueSet/administrative-gender"}` for `$expand`; a list sends the parameter once
    per value. One whose name carries a credential is refused."""

    input: dict[str, JsonValue] | None = None
    """The body the operation is posted, as plain FHIR JSON: a `Parameters` resource, or the one resource an
    operation such as `$validate` takes."""

    method: Literal["GET", "POST"] | None = None
    """How the operation is invoked; `POST` when `input` is set and `GET` otherwise when unset. An operation that
    changes state is invoked with `POST`."""

    max_pages: int = Field(default=1, ge=1, le=10_000)
    """The most pages of a Bundle answer followed by its next links, the first included."""

    max_total: Size = Field(default=64 * 1024 * 1024, ge=1)
    """The most every page read may decode to together; an answer past it fails, and the rest is not read."""

    @field_validator("params")
    @classmethod
    def _params_hold_no_credential(cls, params: dict[str, str | list[str]]) -> dict[str, str | list[str]]:
        """Refuse a parameter whose name carries a credential."""
        return refuse_credentials(params)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        """An instance names its type, and a GET carries no body."""
        if self.id is not None and self.resource_type is None:
            raise ValueError(OPERATION_ID_WITHOUT_TYPE.render())
        if self.method == "GET" and self.input is not None:
            raise ValueError(OPERATION_INPUT_ON_GET.render())
        return self

    @property
    def level(self) -> Literal["system", "type", "instance"]:
        """Where the operation is invoked."""
        if self.resource_type is None:
            return "system"
        return "type" if self.id is None else "instance"

    @property
    def path(self) -> str:
        """The operation's path under the base."""
        prefix = [part for part in (self.resource_type, self.id) if part is not None]
        return "/".join([*prefix, self.operation])


class FhirOperationOutput(BlockModel):
    """What the operation answered."""

    level: Literal["system", "type", "instance"]
    """Where the operation was invoked: on the whole system, a type, or one instance."""

    resource: dict[str, JsonValue] | None = None
    """What the server answered, as plain FHIR JSON, such as a `Parameters`, a `Bundle`, a `ValueSet` or an
    `OperationOutcome`; the pages of a Bundle followed past the first are folded into one `searchset` Bundle. None
    when the server answered no body."""

    resource_type: str | None = None
    """The type of what the server answered."""

    pages: int
    """How many pages were read: 1 for an answer that is not a Bundle, 0 when there was no body."""

    complete: bool
    """Whether the last page read had no next link."""

    issues: list[FhirIssue] = Field(default_factory=list[FhirIssue])
    """What the server said in an `OperationOutcome`: the answer itself, as `$validate` gives, or entries of a
    Bundle answer."""


class FhirOperationOperator(Operator[FhirOperationConfig, FhirOperationOutput]):
    """Invokes a named operation on the system, a type, or one instance. It reads or writes, as the operation does."""

    spec = OperatorSpec(id="fhir.operation", summary="Invoke a FHIR operation such as $everything or $validate.")
    config_model: ClassVar[type[BaseModel]] = FhirOperationConfig
    output_model: ClassVar[type[BaseModel]] = FhirOperationOutput

    async def execute(self, config: FhirOperationConfig, ctx: StepContext) -> FhirOperationOutput:
        """Invoke the operation and hand on what the server answered."""
        method = config.method or ("POST" if config.input is not None else "GET")
        async with client_for(ctx, config.connection) as fhir:
            result = await fhir.operation(
                method, config.path, query_pairs(config.params), config.input, config.max_pages, config.max_total
            )
        return await asyncio.to_thread(_answer, result, config.level)


def _answer(result: SearchPages, level: Literal["system", "type", "instance"]) -> FhirOperationOutput:
    """Carry an operation's answer as the step's output, a paged Bundle folded into one."""
    if not result.pages:
        return FhirOperationOutput(level=level, pages=0, complete=True)
    if len(result.pages) > 1:
        resource, issues = searchset(result.pages)
    else:
        resource = result.pages[0]
        issues = issues_of(resource) if resource_type(resource) == "OperationOutcome" else _entry_issues(resource)
    return FhirOperationOutput(
        level=level,
        resource=resource,
        resource_type=resource_type(resource),
        pages=len(result.pages),
        complete=not result.more,
        issues=issues,
    )


def _entry_issues(resource: dict[str, JsonValue]) -> list[FhirIssue]:
    """The issues a Bundle answer carries in its outcome entries."""
    if resource_type(resource) != "Bundle":
        return []
    return searchset([resource])[1]
