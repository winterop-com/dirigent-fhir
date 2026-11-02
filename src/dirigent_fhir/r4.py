"""The engine's R4 models, read from a server's answer and written into the documents the pack builds.

The engine's models are closed: a key they do not name is an error. A server's answer carries
elements they do not model, so an answer is projected onto the fields a model names before it
is validated, and a document the projection still does not fit is read as plain JSON instead.
"""

import json
import types
from typing import Any, Final, Union, cast, get_args, get_origin

from dhis2w_fhir_engine.r4.resources import (
    Bundle,
    BundleEntry,
    BundleEntrySearch,
    BundleLink,
    CapabilityStatement,
    JsonResource,
    OperationOutcome,
    OperationOutcomeIssue,
)
from pydantic import BaseModel, Field, JsonValue, ValidationError

from dirigent_common import BlockModel

JsonObject = dict[str, JsonValue]

#: A FHIR resource type as it appears in a path: an upper-case letter, then letters.
RESOURCE_TYPE: Final = r"^[A-Z][A-Za-z]{1,63}$"

#: A FHIR logical id: up to 64 letters, digits, hyphens and dots.
RESOURCE_ID: Final = r"^[A-Za-z0-9\-.]{1,64}$"


class FhirIssue(BlockModel):
    """One issue of an `OperationOutcome`, in FHIR's own words."""

    severity: str
    """How bad it is: `fatal`, `error`, `warning` or `information`."""

    code: str
    """FHIR's issue type, such as `invalid`, `not-found`, `duplicate` or `transient`."""

    diagnostics: str | None = None
    """What the server said about it."""

    expression: list[str] = Field(default_factory=list[str])
    """The FHIRPath of each element it is about."""

    def describe(self) -> str:
        """Write the issue on one line."""
        said = f"{self.severity} {self.code}"
        if self.diagnostics:
            said = f"{said}: {self.diagnostics}"
        if self.expression:
            said = f"{said} (at {', '.join(self.expression)})"
        return said


def as_object(value: object) -> JsonObject | None:
    """Read a JSON value as an object, or None when it is anything else."""
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in cast("dict[object, JsonValue]", value).items()}


def resource_type(value: object) -> str | None:
    """The ``resourceType`` a JSON value names, or None when it is not a resource."""
    found = as_object(value)
    kind = found.get("resourceType") if found is not None else None
    return kind if isinstance(kind, str) else None


def _model_in(annotation: Any) -> type[BaseModel] | None:
    """The model a field's annotation holds, through ``list`` and ``| None``."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (list, Union, types.UnionType):
        for argument in get_args(annotation):
            found = _model_in(argument)
            if found is not None:
                return found
    return None


def project(model: type[BaseModel], value: object) -> object:
    """Keep only the keys a model names, at every depth the model types.

    An open model such as ``JsonResource`` keeps everything.
    """
    if isinstance(value, list):
        return [project(model, item) for item in cast("list[object]", value)]
    found = as_object(value)
    if found is None or model.model_config.get("extra") == "allow":
        return value
    kept: dict[str, object] = {}
    for name, field in model.model_fields.items():
        key = field.alias or name
        if key not in found:
            continue
        inner = _model_in(field.annotation)
        kept[key] = found[key] if inner is None else project(inner, found[key])
    return kept


def typed[M: BaseModel](model: type[M], value: object) -> M | None:
    """Read a document as an engine model, or None when it does not fit one even projected."""
    try:
        return model.model_validate(project(model, value))
    except ValidationError:
        return None


def issues_of(value: object) -> list[FhirIssue]:
    """The issues an `OperationOutcome` carries, typed by the engine where they fit its model."""
    if resource_type(value) != "OperationOutcome":
        return []
    outcome = typed(OperationOutcome, value)
    if outcome is not None:
        return [_issue(issue) for issue in outcome.issue or []]
    found = as_object(value) or {}
    raw = found.get("issue")
    return [_loose(item) for item in cast("list[object]", raw)] if isinstance(raw, list) else []


def _issue(issue: OperationOutcomeIssue) -> FhirIssue:
    """Carry one engine-typed issue as the pack's output shape."""
    diagnostics = issue.diagnostics
    if diagnostics is None and issue.details is not None:
        diagnostics = issue.details.text
    return FhirIssue(
        severity=issue.severity or "error",
        code=issue.code or "unknown",
        diagnostics=diagnostics,
        expression=list(issue.expression or []),
    )


def _loose(item: object) -> FhirIssue:
    """Read one issue the engine's model does not fit, taking what it can."""
    found = as_object(item) or {}
    severity, code, diagnostics = found.get("severity"), found.get("code"), found.get("diagnostics")
    expression = found.get("expression")
    return FhirIssue(
        severity=severity if isinstance(severity, str) else "error",
        code=code if isinstance(code, str) else "unknown",
        diagnostics=diagnostics if isinstance(diagnostics, str) else None,
        expression=[part for part in cast("list[object]", expression) if isinstance(part, str)]
        if isinstance(expression, list)
        else [],
    )


def fhir_version(value: object) -> str | None:
    """The FHIR version a CapabilityStatement states, typed by the engine where it fits."""
    statement = typed(CapabilityStatement, value)
    if statement is not None:
        return statement.fhirVersion
    found = as_object(value) or {}
    version = found.get("fhirVersion")
    return version if isinstance(version, str) else None


def next_link(bundle: JsonObject) -> str | None:
    """The URL a search Bundle's ``next`` link names, or None on the last page."""
    links = bundle.get("link")
    if not isinstance(links, list):
        return None
    for link in links:
        typed_link = typed(BundleLink, link)
        if typed_link is not None and typed_link.relation == "next" and typed_link.url:
            return typed_link.url
    return None


def _search(mode: object) -> BundleEntrySearch | None:
    """Type why an entry is in a result set, when it says so in a way the engine names."""
    if mode == "match":
        return BundleEntrySearch(mode="match")
    if mode == "include":
        return BundleEntrySearch(mode="include")
    return None


def searchset(pages: list[JsonObject]) -> tuple[JsonObject, list[FhirIssue]]:
    """Fold a search's pages into one searchset Bundle, built through the engine's model.

    An entry the server marks as an ``outcome`` is not a result: its issues are answered beside
    the Bundle rather than inside it.
    """
    entries: list[BundleEntry] = []
    issues: list[FhirIssue] = []
    total: int | None = None
    for page in pages:
        if total is None and isinstance(page.get("total"), int):
            total = cast("int", page["total"])
        raw = page.get("entry")
        for item in cast("list[object]", raw) if isinstance(raw, list) else []:
            entry = as_object(item) or {}
            resource = as_object(entry.get("resource"))
            search = as_object(entry.get("search")) or {}
            mode = search.get("mode")
            if mode == "outcome" or (resource_type(resource) == "OperationOutcome" and mode != "match"):
                issues.extend(issues_of(resource))
                continue
            if resource is None or resource_type(resource) is None:
                continue
            full_url = entry.get("fullUrl")
            entries.append(
                BundleEntry(
                    fullUrl=full_url if isinstance(full_url, str) else None,
                    resource=JsonResource.model_validate(resource),
                    search=_search(mode),
                )
            )
    bundle = Bundle(type="searchset", total=total, entry=entries or None)
    folded = as_object(json.loads(bundle.model_dump_json(exclude_none=True, by_alias=True)))
    return folded or {}, issues
