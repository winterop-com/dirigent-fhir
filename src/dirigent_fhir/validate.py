"""``fhir.validate``: structural validation of resources and Bundles, on the engine's R4 models.

Every resource is held to the rules of FHIR's JSON format, whatever its type: a ``resourceType``,
a well-formed ``id``, no empty or null element, and references in a form a server can resolve,
a contained one pointing at something the resource contains. A resource of a type the engine
models is then validated through that model, projected onto the elements the model names: the
engine's models name a subset of each resource's elements, so an element they do not name is
left unjudged rather than refused.
"""

import asyncio
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar, Final, Literal, cast

from dhis2w_fhir_engine.evaluation import EvaluationContext
from dhis2w_fhir_engine.evaluation.fhirpath import FHIRPathEvaluator
from dhis2w_fhir_engine.r4 import resources as r4
from dhis2w_fhir_engine.r4.resources import OperationOutcome, OperationOutcomeIssue
from pydantic import BaseModel, Field, JsonValue, ValidationError

from dirigent_common import BlockModel
from dirigent_fhir.client import said
from dirigent_fhir.messages import (
    VALIDATE_CONTAINED_MISSING,
    VALIDATE_DUPLICATE_FULL_URL,
    VALIDATE_EMPTY,
    VALIDATE_INVALID,
    VALIDATE_MODEL,
    VALIDATE_NOT_A_RESOURCE,
    VALIDATE_NULL,
    VALIDATE_PROFILE,
    VALIDATE_RESOURCE_ID,
    VALIDATE_UNRESOLVABLE_REFERENCE,
)
from dirigent_fhir.r4 import RESOURCE_ID, RESOURCE_TYPE, FhirIssue, JsonObject, as_object, project, resource_type
from dirigent_plugin import BlockFailure, ErrorClass, Operator, OperatorSpec, StepContext

#: A reference a server can resolve: relative, versioned, conditional, absolute, a urn, or contained.
REFERENCE: Final = re.compile(
    r"^(?:[A-Z][A-Za-z]{1,63}/[A-Za-z0-9\-.]{1,64}(?:/_history/[A-Za-z0-9\-.]{1,64})?"
    r"|[A-Z][A-Za-z]{1,63}\?\S*"
    r"|https?://\S+"
    r"|urn:(?:uuid|oid):\S+"
    r"|#\S*)$"
)


def _models() -> dict[str, type[BaseModel]]:
    """The engine's closed R4 model for each resource type it models, by ``resourceType``."""
    found: dict[str, type[BaseModel]] = {}
    for value in vars(r4).values():
        if isinstance(value, type) and issubclass(value, r4.Resource) and value.__module__ == r4.__name__:
            declared = value.model_fields.get("resourceType")
            kind = declared.default if declared is not None else None
            if isinstance(kind, str):
                found[kind] = value
    return found


#: The resource types the engine models, and the model each is validated through.
MODELS: Final = _models()


class FhirValidateConfig(BlockModel):
    """What to validate, and what an invalid resource does."""

    input: JsonValue
    """A resource, a Bundle whose entries are validated too, or a list of resources, such as a `fhir.search` step's
    `bundle` or `resources`."""

    profile: str | None = Field(default=None, min_length=1)
    """A profile's canonical URL every resource handed in must conform to, by the engine's `conformsTo()`: the
    resource claims it under `meta.profile`, or it is the base StructureDefinition of the resource's type."""

    on_invalid: Literal["fail", "report"] = "fail"
    """What an error does: `fail` fails the step listing the issues, `report` answers with `valid` false."""

    max_issues: int = Field(default=100, ge=1, le=10_000)
    """The most issues collected before validation stops."""


class FhirValidateOutput(BlockModel):
    """Whether everything handed in is structurally valid, and what was found."""

    valid: bool
    """Whether no issue is an error."""

    checked: int
    """How many resources were checked, contained and Bundle entries counted."""

    issues: list[FhirIssue] = Field(default_factory=list[FhirIssue])
    """Every issue found, each with the FHIRPath of the element it is about."""

    truncated: bool = False
    """Whether validation stopped at `max_issues` with more left to check."""

    outcome: dict[str, JsonValue]
    """The issues as an `OperationOutcome`, built through the engine's model, as plain FHIR JSON."""


class _Full(Exception):
    """Raised when ``max_issues`` issues have been collected."""


@dataclass
class _Findings:
    """The issues found so far, and how many resources were checked."""

    limit: int
    issues: list[FhirIssue] = field(default_factory=list[FhirIssue])
    checked: int = 0
    truncated: bool = False

    def add(self, code: str, said: str, where: str) -> None:
        """Record one error at one element, stopping once the limit is reached."""
        if len(self.issues) >= self.limit:
            self.truncated = True
            raise _Full
        self.issues.append(FhirIssue(severity="error", code=code, diagnostics=said, expression=[where]))


def _kind_of(error: dict[str, Any]) -> str:
    """FHIR's issue type for one model error."""
    if error["type"] == "literal_error":
        return "code-invalid"
    if error["type"].endswith("_type") or error["type"] in ("missing", "extra_forbidden"):
        return "structure"
    return "value"


def _path(base: str, location: tuple[int | str, ...]) -> str:
    """A model error's location as a FHIRPath from the resource's own path."""
    path = base
    for part in location:
        path = f"{path}[{part}]" if isinstance(part, int) else f"{path}.{part}"
    return path


class _Validator:
    """Walks what was handed in, resource by resource and element by element."""

    def __init__(self, findings: _Findings) -> None:
        """Collect into the findings given."""
        self.findings = findings

    def resource(self, value: object, path: str) -> None:
        """Check one value that should be a resource, and every resource inside it."""
        found = as_object(value)
        kind = resource_type(found)
        if found is None or kind is None or not re.match(RESOURCE_TYPE, kind):
            self.findings.add("structure", VALIDATE_NOT_A_RESOURCE.render(), path)
            return
        self.findings.checked += 1
        here = path if path != "$" else kind
        identifier = found.get("id")
        if identifier is not None and (not isinstance(identifier, str) or not re.match(RESOURCE_ID, identifier)):
            self.findings.add("value", VALIDATE_RESOURCE_ID.render(), f"{here}.id")
        model = MODELS.get(kind)
        if model is not None:
            try:
                model.model_validate(project(model, found), strict=True)
            except ValidationError as error:
                for one in error.errors(include_url=False):
                    if one.get("input") is None:
                        continue
                    said = VALIDATE_MODEL.render(detail=str(one["msg"]))
                    self.findings.add(_kind_of(cast("dict[str, Any]", one)), said, _path(here, one["loc"]))
        self.elements(found, here, _contained_ids(found), top=True)
        if kind == "Bundle":
            self.bundle(found, here)

    def bundle(self, bundle: JsonObject, path: str) -> None:
        """Hold a Bundle's entries to one fullUrl each."""
        entries = bundle.get("entry")
        seen: set[str] = set()
        for index, item in enumerate(cast("list[object]", entries) if isinstance(entries, list) else []):
            full_url = (as_object(item) or {}).get("fullUrl")
            if isinstance(full_url, str):
                if full_url in seen:
                    where = f"{path}.entry[{index}].fullUrl"
                    self.findings.add("duplicate", VALIDATE_DUPLICATE_FULL_URL.render(full_url=full_url), where)
                seen.add(full_url)

    def elements(self, value: JsonObject, path: str, contained: set[str], *, top: bool = False) -> None:
        """Hold every element under an object to FHIR's JSON rules, checking a nested resource as one."""
        for key, item in value.items():
            where = f"{path}.{key}"
            if top and key == "resourceType":
                continue
            if isinstance(item, dict) and "resourceType" in item:
                self.resource(item, where)
            elif isinstance(item, list):
                self.listed(key, item, value, where, contained)
            else:
                self.element(key, item, where, contained)

    def listed(self, key: str, items: list[JsonValue], owner: JsonObject, path: str, contained: set[str]) -> None:
        """Check a repeating element: never empty, and a null only where a primitive's extensions sit beside it."""
        if not items:
            self.findings.add("structure", VALIDATE_EMPTY.render(), path)
            return
        sibling = owner.get(key[1:] if key.startswith("_") else f"_{key}")
        for index, item in enumerate(items):
            where = f"{path}[{index}]"
            if item is None:
                if not isinstance(sibling, list):
                    self.findings.add("structure", VALIDATE_NULL.render(), where)
            elif isinstance(item, dict) and "resourceType" in item:
                self.resource(item, where)
            else:
                self.element(key, item, where, contained)

    def element(self, key: str, item: JsonValue, path: str, contained: set[str]) -> None:
        """Check one element's value."""
        if item is None:
            self.findings.add("structure", VALIDATE_NULL.render(), path)
        elif isinstance(item, str) and not item.strip():
            self.findings.add("value", VALIDATE_EMPTY.render(), path)
        elif isinstance(item, dict):
            if not item:
                self.findings.add("structure", VALIDATE_EMPTY.render(), path)
                return
            reference = item.get("reference")
            if isinstance(reference, str):
                self.reference(reference, f"{path}.reference", contained)
            self.elements(item, path, contained)

    def reference(self, reference: str, path: str, contained: set[str]) -> None:
        """Hold a reference to a form a server resolves, and a contained one to what is contained."""
        if not REFERENCE.match(reference):
            self.findings.add("value", VALIDATE_UNRESOLVABLE_REFERENCE.render(reference=reference), path)
        elif reference.startswith("#") and len(reference) > 1 and reference[1:] not in contained:
            self.findings.add("not-found", VALIDATE_CONTAINED_MISSING.render(reference=reference), path)


def _contained_ids(resource: JsonObject) -> set[str]:
    """The ids of the resources a resource contains, which a ``#id`` reference in it may name."""
    contained = resource.get("contained")
    ids: set[str] = set()
    for item in cast("list[object]", contained) if isinstance(contained, list) else []:
        identifier = (as_object(item) or {}).get("id")
        if isinstance(identifier, str):
            ids.add(identifier)
    return ids


def _handed(value: JsonValue) -> Iterator[tuple[object, str]]:
    """The resources handed in, each with the path it is named by."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield item, f"$[{index}]"
    else:
        yield value, "$"


def _profiled(value: JsonValue) -> Iterator[tuple[JsonObject, str]]:
    """The resources a profile is checked on: a Bundle's entries, a list's elements, or the one resource."""
    found = as_object(value)
    if found is not None and resource_type(found) == "Bundle":
        entries = found.get("entry")
        for index, item in enumerate(cast("list[object]", entries) if isinstance(entries, list) else []):
            resource = as_object((as_object(item) or {}).get("resource"))
            if resource is not None and resource_type(resource) is not None:
                yield resource, f"Bundle.entry[{index}].resource"
        return
    for item, path in _handed(value):
        resource = as_object(item)
        if resource is not None and resource_type(resource) is not None:
            yield resource, path if path != "$" else str(resource_type(resource))


def validate(config: FhirValidateConfig) -> FhirValidateOutput:
    """Validate everything handed in, and build the outcome."""
    findings = _Findings(limit=config.max_issues)
    validator = _Validator(findings)
    try:
        for item, path in _handed(config.input):
            validator.resource(item, path)
        if config.profile is not None:
            evaluator = FHIRPathEvaluator()
            for resource, path in _profiled(config.input):
                context = EvaluationContext(resource=resource)
                context.set_constant("profile", config.profile)
                if evaluator.evaluate("conformsTo(%profile)", resource, context) != [True]:
                    findings.add("processing", VALIDATE_PROFILE.render(profile=config.profile), path)
    except _Full:
        pass
    outcome = OperationOutcome(
        issue=[
            OperationOutcomeIssue(
                severity=cast("Any", issue.severity),
                code=cast("Any", issue.code),
                diagnostics=issue.diagnostics,
                expression=issue.expression or None,
            )
            for issue in findings.issues
        ]
        or [OperationOutcomeIssue(severity="information", code="informational", diagnostics="no issues were found")]
    )
    return FhirValidateOutput(
        valid=not findings.issues,
        checked=findings.checked,
        issues=findings.issues,
        truncated=findings.truncated,
        outcome=cast("dict[str, JsonValue]", json.loads(outcome.model_dump_json(exclude_none=True, by_alias=True))),
    )


class FhirValidateOperator(Operator[FhirValidateConfig, FhirValidateOutput]):
    """Validates resources and Bundles structurally, on the engine's R4 models. It touches no server."""

    spec = OperatorSpec(
        id="fhir.validate", summary="Validate FHIR resources and Bundles structurally.", idempotent=True
    )
    config_model: ClassVar[type[BaseModel]] = FhirValidateConfig
    output_model: ClassVar[type[BaseModel]] = FhirValidateOutput

    async def execute(self, config: FhirValidateConfig, ctx: StepContext) -> FhirValidateOutput:
        """Validate off the event loop, and fail the step on an error unless asked to report it."""
        output = await asyncio.to_thread(validate, config)
        for issue in output.issues:
            ctx.log.info("validation found an issue", issue=issue.describe())
        if not output.valid and config.on_invalid == "fail":
            raise BlockFailure(
                VALIDATE_INVALID,
                error_class=ErrorClass.REJECTED,
                count=len(output.issues),
                checked=output.checked,
                detail=said(output.issues),
                issues=[issue.model_dump(mode="json") for issue in output.issues],
            )
        return output
