"""``fhir.search``: search one resource type, following the server's next links under a page cap."""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field, JsonValue

from dirigent_common import BlockModel
from dirigent_fhir.connection import client_for
from dirigent_fhir.messages import SEARCH_MORE_PAGES
from dirigent_fhir.r4 import FhirIssue, searchset
from dirigent_fhir.read import RESOURCE_TYPE
from dirigent_plugin import BlockFailure, ConnectionRef, ErrorClass, Operator, OperatorSpec, StepContext


class FhirSearchConfig(BlockModel):
    """What to search for, and how many pages to follow."""

    connection: ConnectionRef
    """The code of the fhir connection naming the server."""

    resource_type: str = Field(pattern=RESOURCE_TYPE)
    """The resource type searched, such as `Patient`."""

    params: dict[str, str | list[str]] = Field(default_factory=dict[str, str | list[str]])
    """The search parameters as FHIR spells them, such as `{identifier: "http://example.org/mrn|", _count: "50"}`;
    a list sends the parameter once per value, which FHIR reads as AND."""

    max_pages: int = Field(default=10, ge=1, le=10_000)
    """The most pages followed, the first included."""

    on_more: Literal["fail", "stop"] = "fail"
    """What a next link past `max_pages` does: `fail` fails the step, `stop` answers with the pages read and
    `complete` false."""


class FhirSearchOutput(BlockModel):
    """Every page's results, folded into one Bundle."""

    bundle: dict[str, JsonValue]
    """A `searchset` Bundle holding every entry of every page read, as plain FHIR JSON."""

    count: int
    """How many entries the Bundle holds, included resources counted."""

    total: int | None = None
    """How many matches the server says there are, when it says."""

    pages: int
    """How many pages were read."""

    complete: bool
    """Whether the last page read had no next link."""

    issues: list[FhirIssue] = Field(default_factory=list[FhirIssue])
    """What the server said about the search in an `OperationOutcome` entry, such as an unknown parameter."""


def _params(params: dict[str, str | list[str]]) -> list[tuple[str, str]]:
    """The search parameters as query pairs, a list sending one pair per value."""
    pairs: list[tuple[str, str]] = []
    for name, value in params.items():
        for one in [value] if isinstance(value, str) else value:
            pairs.append((name, one))
    return pairs


class FhirSearchOperator(Operator[FhirSearchConfig, FhirSearchOutput]):
    """Searches one type and follows `Bundle.link[next]` on the connection's origin only. It only reads."""

    spec = OperatorSpec(id="fhir.search", summary="Search FHIR resources, following next links.", idempotent=True)
    config_model: ClassVar[type[BaseModel]] = FhirSearchConfig
    output_model: ClassVar[type[BaseModel]] = FhirSearchOutput

    async def execute(self, config: FhirSearchConfig, ctx: StepContext) -> FhirSearchOutput:
        """Read the pages, then fold them into one searchset Bundle."""
        async with client_for(ctx, config.connection) as fhir:
            result = await fhir.search(config.resource_type, _params(config.params), config.max_pages)
        if result.more and config.on_more == "fail":
            raise BlockFailure(SEARCH_MORE_PAGES, error_class=ErrorClass.REJECTED, max_pages=config.max_pages)
        bundle, issues = searchset(result.pages)
        for issue in issues:
            ctx.log.warning("the server said something about the search", issue=issue.describe())
        entries = bundle.get("entry")
        total = bundle.get("total")
        return FhirSearchOutput(
            bundle=bundle,
            count=len(entries) if isinstance(entries, list) else 0,
            total=total if isinstance(total, int) else None,
            pages=len(result.pages),
            complete=not result.more,
            issues=issues,
        )
