"""``fhir.delete``: delete one resource by its id, or the resources a search matches, never more than ``max``."""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from dirigent_common import BlockModel
from dirigent_fhir.connection import client_for
from dirigent_fhir.messages import DELETE_TOO_MANY
from dirigent_fhir.r4 import FhirIssue
from dirigent_fhir.writes import Targeted
from dirigent_plugin import BlockFailure, ErrorClass, Operator, OperatorSpec, StepContext


class FhirDeleteConfig(Targeted):
    """Which resource to delete, or which search to delete what it matches, and how many it may match."""

    block: ClassVar[str] = "fhir.delete"

    max: int = Field(default=1, ge=1, le=10_000)
    """The most resources a conditional delete may match: a search matching more fails the step before anything is
    deleted. Read for `params` only."""


class FhirDeleteOutput(BlockModel):
    """What the server deleted."""

    outcome: Literal["deleted", "absent"]
    """`deleted` when the server deleted something, `absent` when there was nothing to delete: the id is not held or
    is already deleted, or the search matched nothing."""

    deleted: int
    """How many resources were deleted."""

    ids: list[str] = Field(default_factory=list[str])
    """The ids of the resources deleted."""

    issues: list[FhirIssue] = Field(default_factory=list[FhirIssue])
    """What the server said about each delete in an `OperationOutcome`, such as how long it took."""


class FhirDeleteOperator(Operator[FhirDeleteConfig, FhirDeleteOutput]):
    """Deletes one resource by its id, or what a search matches up to `max`. It writes."""

    spec = OperatorSpec(
        id="fhir.delete", summary="Delete a FHIR resource by id, or what a search matches.", idempotent=True
    )
    config_model: ClassVar[type[BaseModel]] = FhirDeleteConfig
    output_model: ClassVar[type[BaseModel]] = FhirDeleteOutput

    async def execute(self, config: FhirDeleteConfig, ctx: StepContext) -> FhirDeleteOutput:
        """Find what is held, refuse a search matching more than `max`, then delete each resource found by its id."""
        async with client_for(ctx, config.connection) as fhir:
            if config.id is not None:
                found = [config.id] if await fhir.holds(config.resource_type, config.id) else []
            else:
                matches = await fhir.matches(config.resource_type, config.query or [], config.max)
                if len(matches.ids) > config.max or (matches.total is not None and matches.total > config.max):
                    shown = str(matches.total) if matches.total is not None else f"more than {config.max}"
                    raise BlockFailure(
                        DELETE_TOO_MANY,
                        error_class=ErrorClass.REJECTED,
                        matched=shown,
                        resource_type=config.resource_type,
                        max=config.max,
                    )
                found = matches.ids
            ids: list[str] = []
            issues: list[FhirIssue] = []
            for resource_id in found:
                done = await fhir.delete(config.resource_type, resource_id)
                issues.extend(done.issues)
                if not done.absent:
                    ids.append(resource_id)
        return FhirDeleteOutput(outcome="deleted" if ids else "absent", deleted=len(ids), ids=ids, issues=issues)
