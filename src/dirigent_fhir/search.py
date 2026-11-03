"""``fhir.search``: search one resource type, following the server's next links under a page cap."""

import asyncio
import re
from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from dirigent_common import BlockModel, Size
from dirigent_fhir.client import SearchPages
from dirigent_fhir.connection import client_for
from dirigent_fhir.messages import SEARCH_CREDENTIAL_REFUSED, SEARCH_MORE_PAGES
from dirigent_fhir.r4 import RESOURCE_TYPE, FhirIssue, JsonObject, as_object, searchset
from dirigent_plugin import BlockFailure, ConnectionRef, ErrorClass, Operator, OperatorSpec, StepContext

#: A compound that makes a search parameter's name, read without its separators, that of a credential.
CREDENTIAL_COMPOUNDS: Final = (
    "accesskey",
    "accesstoken",
    "apikey",
    "authtoken",
    "clientsecret",
    "idtoken",
    "privatekey",
    "refreshtoken",
    "secretkey",
    "sessionid",
    "sessiontoken",
)

#: A whole word of a search parameter's name that does the same, as a final `token` does.
CREDENTIAL_WORDS: Final = frozenset(
    {
        "auth",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "jwt",
        "passwd",
        "password",
        "pwd",
        "secret",
        "secrets",
    }
)


def _carries_credential(name: str) -> bool:
    """Whether a search parameter's name is that of a credential, in any case and with any separator."""
    words = [word for word in re.split(r"[^a-z0-9]+", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).lower()) if word]
    compact = "".join(words)
    return (
        any(word in compact for word in CREDENTIAL_COMPOUNDS)
        or bool(CREDENTIAL_WORDS.intersection(words))
        or words[-1:] == ["token"]
    )


class FhirSearchConfig(BlockModel):
    """What to search for, and how many pages to follow."""

    model_config = ConfigDict(hide_input_in_errors=True)

    connection: ConnectionRef
    """The code of the fhir connection naming the server."""

    resource_type: str = Field(pattern=RESOURCE_TYPE)
    """The resource type searched, such as `Patient`."""

    params: dict[str, str | list[str]] = Field(
        default_factory=dict[str, str | list[str]], json_schema_extra={"x-dirigent-plain-query": True}
    )
    """The search parameters as FHIR spells them, such as `{identifier: "http://example.org/mrn|", _count: "50"}`;
    a list sends the parameter once per value, which FHIR reads as AND. One whose name carries a credential is
    refused."""

    max_pages: int = Field(default=10, ge=1, le=10_000)
    """The most pages followed, the first included."""

    max_total: Size = Field(default=64 * 1024 * 1024, ge=1)
    """The most every page read may decode to together; a search past it fails, and the rest is not read."""

    on_more: Literal["fail", "stop"] = "fail"
    """What a next link past `max_pages` does: `fail` fails the step, `stop` answers with the pages read and
    `complete` false."""

    emit: Literal["bundle", "pages", "resources"] = "bundle"
    """What the step hands on: `bundle` folds every page into one Bundle under `bundle`, `pages` lists each page as
    its own searchset Bundle under `bundles`, and `resources` lists every resource under `resources`, so a
    following step's `for_each` maps over either list."""

    @field_validator("params")
    @classmethod
    def _params_hold_no_credential(cls, params: dict[str, str | list[str]]) -> dict[str, str | list[str]]:
        """Refuse a parameter whose name carries a credential."""
        return refuse_credentials(params)


def refuse_credentials(params: dict[str, str | list[str]]) -> dict[str, str | list[str]]:
    """Refuse a parameter whose name carries a credential, repeating the name and never the value."""
    for name in params:
        if _carries_credential(name):
            raise ValueError(SEARCH_CREDENTIAL_REFUSED.render(parameter=name))
    return params


class FhirSearchOutput(BlockModel):
    """Every page's results, folded into one Bundle, or listed for a fan-out."""

    bundle: dict[str, JsonValue] | None = None
    """With `emit: bundle`, a `searchset` Bundle holding every entry of every page read, as plain FHIR JSON."""

    bundles: list[dict[str, JsonValue]] | None = None
    """With `emit: pages`, one `searchset` Bundle per page read that holds a result, in the order the server
    answered them."""

    resources: list[dict[str, JsonValue]] | None = None
    """With `emit: resources`, every resource of every page read, included resources counted, in order."""

    count: int
    """How many entries the pages hold, included resources counted."""

    total: int | None = None
    """How many matches the server says there are, when it says."""

    pages: int
    """How many pages were read."""

    complete: bool
    """Whether the last page read had no next link."""

    issues: list[FhirIssue] = Field(default_factory=list[FhirIssue])
    """What the server said about the search in an `OperationOutcome` entry, such as an unknown parameter."""


def query_pairs(params: dict[str, str | list[str]]) -> list[tuple[str, str]]:
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
            result = await fhir.search(
                config.resource_type, query_pairs(config.params), config.max_pages, config.max_total
            )
        if result.more and config.on_more == "fail":
            raise BlockFailure(SEARCH_MORE_PAGES, error_class=ErrorClass.REJECTED, max_pages=config.max_pages)
        output = await asyncio.to_thread(_fold, result, config.emit)
        for issue in output.issues:
            ctx.log.warning("the server said something about the search", issue=issue.describe())
        return output


def _fold(result: SearchPages, emit: Literal["bundle", "pages", "resources"]) -> FhirSearchOutput:
    """Fold a search's pages into the step's output."""
    bundle, issues = searchset(result.pages)
    entries = _entries(bundle)
    total = bundle.get("total")
    output = FhirSearchOutput(
        count=len(entries),
        total=total if isinstance(total, int) else None,
        pages=len(result.pages),
        complete=not result.more,
        issues=issues,
    )
    if emit == "pages":
        folded = (searchset([page])[0] for page in result.pages)
        output.bundles = [page for page in folded if _entries(page)]
    elif emit == "resources":
        output.resources = [resource for entry in entries if (resource := as_object(entry.get("resource")))]
    else:
        output.bundle = bundle
    return output


def _entries(bundle: JsonObject) -> list[JsonObject]:
    """A Bundle's entries, each as an object."""
    raw = bundle.get("entry")
    return [entry for item in raw if (entry := as_object(item)) is not None] if isinstance(raw, list) else []
