"""``fhir.transaction``: write many resources in one transaction or batch Bundle, each matched on its identifier."""

from typing import ClassVar, Final, Literal, cast

from pydantic import BaseModel, Field, JsonValue, model_validator

from dirigent_common import BlockModel
from dirigent_fhir.client import error_class, said
from dirigent_fhir.connection import client_for
from dirigent_fhir.messages import BATCH_ENTRIES_FAILED, TRANSACTION_NO_RESPONSE, TRANSACTION_NOTHING
from dirigent_fhir.r4 import FhirIssue, JsonObject, as_object, issues_of
from dirigent_fhir.references import Carried, Unresolved, Write, carried_from_bundle, transaction_bundle
from dirigent_plugin import BlockFailure, ConnectionRef, ErrorClass, Operator, OperatorSpec, StepContext

#: How many failed entries a refusal names before counting the rest.
NAMED_FAILURES: Final = 3


class FhirTransactionConfig(BlockModel):
    """What to write, and how the server is asked to write it."""

    connection: ConnectionRef
    """The code of the fhir connection naming the server written to."""

    resources: list[dict[str, JsonValue]] | None = None
    """The resources to write, as plain FHIR JSON. Set this or `bundle`."""

    bundle: dict[str, JsonValue] | None = None
    """A Bundle whose entries hold the resources to write, such as a `fhir.search` step's `bundle`. Set this or
    `resources`."""

    mode: Literal["transaction", "batch"] = "transaction"
    """`transaction` writes everything or nothing; `batch` writes each entry on its own."""

    write: Write = "update"
    """`update` writes each resource with a conditional update on its identifier, creating it when nothing
    matches; `create` writes each with a conditional create, leaving one that matches as it is."""

    identifier_system: str | None = None
    """The system of the identifier each resource is matched on; each resource's first identifier with both a
    system and a value when unset. A resource without one fails the step: the source server's id is never used."""

    unresolved_references: Unresolved = "fail"
    """What a relative reference such as `Patient/123` to a resource the transaction does not carry does: `fail`
    fails the step, since on the target it would name whatever holds that id; `keep` sends it as written."""

    on_failed: Literal["fail", "report"] = "fail"
    """What a failed batch entry does: `fail` fails the step, `report` counts it under `failed` and logs it. A
    transaction is written whole or not at all, so this only reaches a batch."""

    @model_validator(mode="after")
    def _one_source(self) -> "FhirTransactionConfig":
        """Take resources or a bundle, exactly one of them."""
        if (self.resources is None) == (self.bundle is None):
            raise ValueError(TRANSACTION_NOTHING.render())
        return self


class FhirEntryResult(BlockModel):
    """What the server did with one resource."""

    resource_type: str
    """The resource's type."""

    source: str | None = None
    """The `Type/id` it had where it was read, when it carried an id."""

    status: int
    """The status the server gave the entry, such as 201 for created and 200 for updated."""

    location: str | None = None
    """Where the server holds it now."""

    issues: list[FhirIssue] = Field(default_factory=list[FhirIssue])
    """What the server said about the entry."""


class FhirTransactionOutput(BlockModel):
    """What the server did with each resource, and its response Bundle."""

    created: int
    """How many resources the server created."""

    updated: int
    """How many the server wrote over, or found already there."""

    failed: int
    """How many entries of a batch failed, under `on_failed: report`."""

    entries: list[FhirEntryResult]
    """One result per resource written, in the order they were sent."""

    response: dict[str, JsonValue]
    """The server's `transaction-response` or `batch-response` Bundle, as plain FHIR JSON."""


def _status(response: JsonObject) -> int:
    """The status code an entry's response states, such as 201 from `201 Created`."""
    status = response.get("status")
    head = status.split(" ", 1)[0] if isinstance(status, str) else ""
    return int(head) if head.isdigit() else 0


def _results(carried: list[Carried], answer: JsonObject) -> list[FhirEntryResult]:
    """Pair each carried resource with the response entry the server answered for it."""
    raw = answer.get("entry")
    entries = cast("list[object]", raw) if isinstance(raw, list) else []
    if len(entries) != len(carried):
        raise BlockFailure(
            TRANSACTION_NO_RESPONSE,
            error_class=ErrorClass.UNKNOWN,
            where="POST (transaction)",
            found=len(entries),
            sent=len(carried),
        )
    results: list[FhirEntryResult] = []
    for one, item in zip(carried, entries, strict=True):
        response = as_object((as_object(item) or {}).get("response")) or {}
        location = response.get("location")
        results.append(
            FhirEntryResult(
                resource_type=one.kind,
                source=one.source,
                status=_status(response),
                location=location if isinstance(location, str) else None,
                issues=issues_of(response.get("outcome")),
            )
        )
    return results


class FhirTransactionOperator(Operator[FhirTransactionConfig, FhirTransactionOutput]):
    """Writes many resources in one Bundle, the references between them carried as `urn:uuid` entries. It writes."""

    spec = OperatorSpec(
        id="fhir.transaction",
        summary="Write FHIR resources in one transaction or batch.",
        idempotent=True,
    )
    config_model: ClassVar[type[BaseModel]] = FhirTransactionConfig
    output_model: ClassVar[type[BaseModel]] = FhirTransactionOutput

    async def execute(self, config: FhirTransactionConfig, ctx: StepContext) -> FhirTransactionOutput:
        """Build the Bundle, send it, and read what the server did with each entry."""
        carried = (
            [Carried(resource) for resource in config.resources]
            if config.resources is not None
            else carried_from_bundle(config.bundle or {})
        )
        bundle = transaction_bundle(
            carried,
            mode=config.mode,
            write=config.write,
            identifier_system=config.identifier_system,
            unresolved=config.unresolved_references,
        )
        ctx.log.info("transaction built", mode=config.mode, entries=len(carried))
        async with client_for(ctx, config.connection) as fhir:
            answer = await fhir.transaction(bundle)
        results = _results(carried, answer) if carried else []
        failed = [result for result in results if result.status >= 400 or result.status == 0]
        for result in failed:
            ctx.log.warning(
                "an entry was not written",
                resource=result.source or result.resource_type,
                status=result.status,
                issues=said(result.issues),
            )
        if failed and config.on_failed == "fail":
            classes = {error_class(result.status, result.issues) for result in failed}
            detail = "; ".join(
                f"{result.source or result.resource_type} {result.status}"
                + (f" {said(result.issues)}" if result.issues else "")
                for result in failed[:NAMED_FAILURES]
            )
            raise BlockFailure(
                BATCH_ENTRIES_FAILED,
                error_class=ErrorClass.TRANSIENT if classes == {ErrorClass.TRANSIENT} else ErrorClass.REJECTED,
                failed=len(failed),
                total=len(results),
                detail=detail,
            )
        return FhirTransactionOutput(
            created=sum(1 for result in results if result.status == 201),
            updated=sum(1 for result in results if 200 <= result.status < 300 and result.status != 201),
            failed=len(failed),
            entries=results,
            response=answer,
        )
