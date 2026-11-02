"""A small FHIR REST client: reads, paged searches, conditional writes, transactions, and ``/metadata``.

Every call goes through a bounded client held to one origin. A refusal is raised as a classified
failure carrying the server's ``OperationOutcome`` in its own words.
"""

import base64
import binascii
import contextlib
import json
import re
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Final, Self

import httpx2
from pydantic import JsonValue

from dirigent_common import scrub, scrub_json, spellings
from dirigent_fhir.limits import BoundedClient
from dirigent_fhir.messages import (
    ANSWERED,
    BAD_RESOURCE_TYPE,
    ISSUES_AND_MORE,
    NO_RESOURCE_TYPE,
    NOT_A_CAPABILITY_STATEMENT,
    NOT_A_RESOURCE,
    NOT_JSON,
    OFF_ORIGIN,
)
from dirigent_fhir.r4 import (
    RESOURCE_TYPE,
    FhirIssue,
    JsonObject,
    as_object,
    fhir_version,
    issues_of,
    next_link,
    resource_type,
)
from dirigent_plugin import BlockFailure, ErrorClass

#: The header a credential travels in.
AUTHORIZATION: Final = "Authorization"

#: The media type every request asks for and every body is sent as.
FHIR_JSON: Final = "application/fhir+json"

#: The port a URL without one is served on, by scheme.
DEFAULT_PORTS: Final = {"http": 80, "https": 443}

#: The issue types that say the same request may succeed later.
TRANSIENT_CODES: Final = frozenset({"transient", "timeout", "throttled", "lock-error"})

#: How many issues a refusal names before counting the rest.
NAMED_ISSUES: Final = 3

#: How much of a body that is not an OperationOutcome a refusal quotes.
QUOTED: Final = 500

#: What a write asks the server to answer with: the resource as stored.
RETURN_REPRESENTATION: Final = {"Prefer": "return=representation", "Content-Type": FHIR_JSON}


@dataclass(frozen=True, slots=True)
class Origin:
    """A URL's scheme, host and port: the only place a connection sends requests."""

    scheme: str
    host: str
    port: int

    def __str__(self) -> str:
        """Write the origin as a URL prefix."""
        return f"{self.scheme}://{self.host}:{self.port}"


def origin_of(url: httpx2.URL | str) -> Origin:
    """The origin a URL is served from, its port filled in from the scheme when it names none."""
    parsed = httpx2.URL(url) if isinstance(url, str) else url
    scheme = parsed.scheme.lower()
    return Origin(scheme, parsed.host.lower().rstrip("."), parsed.port or DEFAULT_PORTS.get(scheme, 0))


def ensure_on_origin(url: httpx2.URL | str, origin: Origin) -> None:
    """Refuse a URL off the connection's origin."""
    target = origin_of(url)
    if target != origin:
        raise BlockFailure(OFF_ORIGIN, error_class=ErrorClass.REJECTED, target=str(target), origin=str(origin))


def error_class(status: int, issues: list[FhirIssue]) -> ErrorClass:
    """Classify a refusal by its status, and by its issues where they say the request may succeed later."""
    if status >= 500 or status == 429:
        return ErrorClass.TRANSIENT
    if any(issue.code in TRANSIENT_CODES for issue in issues if issue.severity in ("error", "fatal")):
        return ErrorClass.TRANSIENT
    if 400 <= status < 500:
        return ErrorClass.REJECTED
    return ErrorClass.UNKNOWN


def said(issues: list[FhirIssue]) -> str:
    """Name the first issues on one line, counting the rest."""
    named = "; ".join(issue.describe() for issue in issues[:NAMED_ISSUES])
    more = len(issues) - NAMED_ISSUES
    return ISSUES_AND_MORE.render(named=named, more=more) if more > 0 else named


def credential_spellings(authorization: str | None) -> set[str]:
    """Every spelling of an ``Authorization`` value a server may quote: whole, its credential, a basic pair decoded."""
    if not authorization:
        return set()
    written = {authorization}
    scheme, _, credential = authorization.partition(" ")
    if credential:
        written.add(credential)
        if scheme.lower() == "basic":
            with contextlib.suppress(binascii.Error, UnicodeDecodeError):
                written.add(base64.b64decode(credential, validate=True).decode())
    found: set[str] = set()
    for one in written:
        found |= spellings(one)
    return found


def sent_credential(response: httpx2.Response) -> set[str]:
    """The spellings of the credential the request behind a response carried."""
    try:
        request = response.request
    except RuntimeError:
        return set()
    return credential_spellings(request.headers.get(AUTHORIZATION))


def _body(response: httpx2.Response) -> object:
    """The JSON a response carries, with the credential its request sent scrubbed, or None when it carries none."""
    try:
        parsed: JsonValue = response.json()
    except ValueError:
        return None
    secrets = sent_credential(response)
    return scrub_json(parsed, secrets) if secrets else parsed


def _text(response: httpx2.Response) -> str:
    """The opening of a response's text, with the credential its request sent scrubbed."""
    return scrub(response.text, sent_credential(response))[:QUOTED]


def refusal(response: httpx2.Response, where: str) -> BlockFailure:
    """Turn an error answer into a classified failure, in the words of its OperationOutcome when it has one."""
    issues = issues_of(_body(response))
    remote = said(issues) if issues else (_text(response) or response.reason_phrase)
    status = response.status_code
    return BlockFailure(
        ANSWERED,
        error_class=error_class(status, issues),
        where=where,
        status=status,
        remote=remote,
        issues=[issue.model_dump(mode="json") for issue in issues],
    )


def writable_type(resource: JsonObject) -> str:
    """The type a resource is written under, refusing one that is missing or is no FHIR type name."""
    kind = resource_type(resource)
    if kind is None:
        raise BlockFailure(NO_RESOURCE_TYPE, error_class=ErrorClass.REJECTED)
    if re.fullmatch(RESOURCE_TYPE, kind) is None:
        raise BlockFailure(BAD_RESOURCE_TYPE, error_class=ErrorClass.REJECTED)
    return kind


def resource_of(response: httpx2.Response, where: str, expected: str | None = None) -> JsonObject:
    """Read an answer as a FHIR resource, of the expected type when one is named."""
    body = _body(response)
    found = as_object(body)
    if found is None or resource_type(found) is None:
        raise BlockFailure(NOT_JSON, error_class=ErrorClass.UNKNOWN, where=where)
    kind = resource_type(found)
    if expected is not None and kind != expected:
        raise BlockFailure(NOT_A_RESOURCE, error_class=ErrorClass.UNKNOWN, where=where, found=kind, expected=expected)
    return found


@dataclass(frozen=True, slots=True)
class Written:
    """What a write answered: its status, where the resource lives, and the resource as stored."""

    status: int
    location: str | None
    resource: JsonObject | None
    issues: list[FhirIssue] = field(default_factory=list[FhirIssue])

    @property
    def id(self) -> str | None:
        """The id the server stored the resource under."""
        if self.resource is not None and isinstance(self.resource.get("id"), str):
            return str(self.resource["id"])
        return _location_part(self.location, 1)

    @property
    def version_id(self) -> str | None:
        """The version the server stored, from the resource or its location."""
        meta = as_object(self.resource.get("meta")) if self.resource is not None else None
        version = meta.get("versionId") if meta is not None else None
        if isinstance(version, str):
            return version
        return _location_part(self.location, 3)


def _location_part(location: str | None, index: int) -> str | None:
    """One part of a ``Type/id/_history/version`` location, counted from its type."""
    if not location:
        return None
    parts = location.split("?", 1)[0].rstrip("/").split("/")
    for at, part in enumerate(parts):
        if part and part[0].isupper() and at + index < len(parts):
            if index == 3 and parts[at + 2] != "_history":
                return None
            return parts[at + index]
    return None


@dataclass(frozen=True, slots=True)
class SearchPages:
    """The pages a search answered with, and whether a next link was left unfollowed."""

    pages: list[JsonObject]
    more: bool


class FhirClient:
    """The FHIR interactions the pack's blocks make, through one connection's bounded client."""

    def __init__(self, client: BoundedClient, origin: Origin) -> None:
        """Wrap a bounded client already held to the origin."""
        self._client = client
        self._origin = origin

    async def __aenter__(self) -> Self:
        """Open the client."""
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the client."""
        await self._client.__aexit__(kind, error, traceback)

    async def _call(self, method: str, path: str, **kwargs: Any) -> httpx2.Response:
        """Make one interaction, raising the classified refusal when the server answers with an error."""
        response = await self._client.request(method, path, **kwargs)
        if response.is_error:
            raise refusal(response, f"{method} {path.split('?', 1)[0]}")
        return response

    async def capabilities(self) -> "Capabilities":
        """Read the server's CapabilityStatement from ``/metadata``."""
        where = "GET metadata"
        response = await self._call("GET", "metadata")
        body = _body(response)
        if resource_type(body) != "CapabilityStatement":
            found = resource_type(body) or "something other than a resource"
            raise BlockFailure(NOT_A_CAPABILITY_STATEMENT, error_class=ErrorClass.REJECTED, where=where, found=found)
        statement = as_object(body) or {}
        return Capabilities(statement=statement, fhir_version=fhir_version(statement))

    async def read(self, kind: str, resource_id: str) -> JsonObject:
        """Read one resource by its type and id."""
        path = f"{kind}/{resource_id}"
        return resource_of(await self._call("GET", path), f"GET {path}", kind)

    async def search(self, kind: str, params: list[tuple[str, str]], max_pages: int) -> SearchPages:
        """Search one type, following ``Bundle.link[next]`` on the origin for at most ``max_pages`` pages."""
        response = await self._call("GET", kind, params=params)
        page = resource_of(response, f"GET {kind}", "Bundle")
        pages = [page]
        link = next_link(page)
        while link is not None and len(pages) < max_pages:
            ensure_on_origin(link, self._origin)
            page = resource_of(await self._call("GET", link), f"GET {kind} (page {len(pages) + 1})", "Bundle")
            pages.append(page)
            link = next_link(page)
        return SearchPages(pages=pages, more=link is not None)

    async def create(self, resource: JsonObject, if_none_exist: str | None) -> Written:
        """Create a resource, only when nothing matches ``if_none_exist`` when it is given."""
        kind = writable_type(resource)
        headers = dict(RETURN_REPRESENTATION)
        if if_none_exist:
            headers["If-None-Exist"] = if_none_exist
        response = await self._call("POST", kind, content=json.dumps(resource), headers=headers)
        return _written(response)

    async def update_where(self, resource: JsonObject, search: str) -> Written:
        """Update the one resource a search matches, or create it when none does."""
        kind = writable_type(resource)
        response = await self._call(
            "PUT", f"{kind}?{search}", content=json.dumps(resource), headers=RETURN_REPRESENTATION
        )
        return _written(response)

    async def transaction(self, bundle: JsonObject) -> JsonObject:
        """Send a transaction or batch Bundle to the base, answering its response Bundle."""
        base = str(self._client.base_url).rstrip("/")
        response = await self._client.request("POST", base, content=json.dumps(bundle), headers=RETURN_REPRESENTATION)
        if response.is_error:
            raise refusal(response, "POST (transaction)")
        return resource_of(response, "POST (transaction)", "Bundle")


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What ``/metadata`` said."""

    statement: JsonObject
    fhir_version: str | None


def _written(response: httpx2.Response) -> Written:
    """Read what a write answered, a body that is an OperationOutcome giving issues instead of a resource."""
    body = _body(response)
    found = as_object(body)
    issues = issues_of(found)
    resource = found if found is not None and resource_type(found) not in (None, "OperationOutcome") else None
    location = response.headers.get("Content-Location") or response.headers.get("Location")
    if location is not None:
        location = scrub(location, sent_credential(response))
    return Written(status=response.status_code, location=location, resource=resource, issues=issues)


def token(system: str, value: str) -> str:
    """Write a token search value, escaping what FHIR's search syntax reserves."""
    return f"{_escape(system)}|{_escape(value)}"


def _escape(part: str) -> str:
    """Escape the characters a search parameter value reserves."""
    for reserved in ("\\", "|", ",", "$"):
        part = part.replace(reserved, f"\\{reserved}")
    return part


def identifier_search(system: str, value: str) -> str:
    """The query that matches a resource on one identifier, encoded for a URL."""
    return str(httpx2.QueryParams({"identifier": token(system, value)}))


def first_identifier(resource: JsonObject, system: str | None) -> tuple[str, str] | None:
    """The first identifier carrying both a system and a value, under ``system`` when one is named."""
    identifiers = resource.get("identifier")
    for item in identifiers if isinstance(identifiers, list) else []:
        found = as_object(item) or {}
        found_system, found_value = found.get("system"), found.get("value")
        if (
            isinstance(found_system, str)
            and isinstance(found_value, str)
            and found_system
            and found_value
            and (system is None or found_system == system)
        ):
            return found_system, found_value
    return None


def portable(resource: JsonObject) -> JsonObject:
    """A copy of a resource without what only its source server means: its id and its version metadata."""
    copied: dict[str, JsonValue] = {key: value for key, value in resource.items() if key != "id"}
    meta = as_object(copied.get("meta"))
    if meta is not None:
        kept = {key: value for key, value in meta.items() if key not in ("versionId", "lastUpdated", "source")}
        if kept:
            copied["meta"] = kept
        else:
            copied.pop("meta")
    return copied
