"""A transaction built from carried resources: each one a ``urn:uuid`` entry, the references between them rewritten.

A reference to a resource the transaction carries, by ``Type/id`` or by the ``fullUrl`` it was
read from, becomes that resource's ``urn:uuid``, which the target server resolves to wherever it
stores the resource. A reference to a contained resource, a ``urn:``, an absolute URL elsewhere,
or a conditional reference is left as written.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Final, Literal, cast

from pydantic import JsonValue

from dirigent_fhir.client import first_identifier, identifier_search, portable, writable_type
from dirigent_fhir.messages import (
    IDENTIFIER_SYSTEM,
    NO_IDENTIFIER,
    TRANSACTION_DUPLICATE,
    TRANSACTION_UNRESOLVED,
)
from dirigent_fhir.r4 import JsonObject, as_object
from dirigent_plugin import BlockFailure, ErrorClass

#: A relative literal reference: a type and an id, with an optional version after it.
RELATIVE: Final = re.compile(r"^([A-Z][A-Za-z]+)/([A-Za-z0-9\-.]{1,64})(?:/_history/[A-Za-z0-9\-.]{1,64})?$")

#: The version suffix a reference may carry.
HISTORY: Final = re.compile(r"/_history/[^/]+$")

Write = Literal["update", "create"]
Unresolved = Literal["fail", "keep"]


@dataclass(frozen=True, slots=True)
class Carried:
    """One resource a transaction carries, and the names it had on the server it was read from."""

    resource: JsonObject
    full_url: str | None = None

    @property
    def kind(self) -> str:
        """The resource's type, refused when it is missing or is no FHIR type name."""
        return writable_type(self.resource)

    @property
    def source(self) -> str | None:
        """The ``Type/id`` it had where it was read, when it carries an id."""
        found = self.resource.get("id")
        return f"{self.kind}/{found}" if isinstance(found, str) and found else None

    def names(self) -> list[str]:
        """Every name a reference to it may use."""
        return [name for name in (self.source, self.full_url) if name]


def carried_from_bundle(bundle: JsonObject) -> list[Carried]:
    """The resources a Bundle's entries hold, each with the ``fullUrl`` it was read from."""
    entries = bundle.get("entry")
    carried: list[Carried] = []
    for item in cast("list[object]", entries) if isinstance(entries, list) else []:
        entry = as_object(item) or {}
        resource = as_object(entry.get("resource"))
        if resource is None:
            continue
        full_url = entry.get("fullUrl")
        carried.append(Carried(resource, full_url if isinstance(full_url, str) else None))
    return carried


def _rewrite(value: JsonValue, urns: dict[str, str], unresolved: Unresolved, owner: str) -> JsonValue:
    """Rewrite every reference inside a value to a carried resource's ``urn:uuid``."""
    if isinstance(value, list):
        return [_rewrite(item, urns, unresolved, owner) for item in value]
    if not isinstance(value, dict):
        return value
    rewritten: dict[str, JsonValue] = {key: _rewrite(item, urns, unresolved, owner) for key, item in value.items()}
    reference = rewritten.get("reference")
    if isinstance(reference, str):
        rewritten["reference"] = _resolve(reference, urns, unresolved, owner)
    return rewritten


def _resolve(reference: str, urns: dict[str, str], unresolved: Unresolved, owner: str) -> str:
    """The ``urn:uuid`` a reference resolves to among the carried resources, or the reference as written."""
    bare = HISTORY.sub("", reference)
    if bare in urns:
        return urns[bare]
    if reference in urns:
        return urns[reference]
    if unresolved == "fail" and RELATIVE.fullmatch(reference):
        raise BlockFailure(TRANSACTION_UNRESOLVED, error_class=ErrorClass.REJECTED, resource=owner, reference=reference)
    return reference


def transaction_bundle(
    carried: list[Carried],
    *,
    mode: Literal["transaction", "batch"],
    write: Write,
    identifier_system: str | None,
    unresolved: Unresolved,
) -> JsonObject:
    """Build the Bundle that writes every carried resource conditionally on its identifier.

    ``update`` writes each as a conditional update, ``PUT Type?identifier=``, and ``create`` as a
    conditional create, ``POST Type`` with ``ifNoneExist``. The source server's id is never sent.
    """
    urns: dict[str, str] = {}
    entries_urns: list[str] = []
    for one in carried:
        urn = f"urn:uuid:{uuid.uuid4()}"
        entries_urns.append(urn)
        for name in one.names():
            if name in urns:
                raise BlockFailure(TRANSACTION_DUPLICATE, error_class=ErrorClass.REJECTED, resource=name)
            urns[name] = urn
    entries: list[JsonValue] = []
    for one, urn in zip(carried, entries_urns, strict=True):
        owner = one.source or one.kind
        identifier = first_identifier(one.resource, identifier_system)
        if identifier is None:
            system = IDENTIFIER_SYSTEM.render(system=identifier_system) if identifier_system else ""
            raise BlockFailure(NO_IDENTIFIER, error_class=ErrorClass.REJECTED, resource=owner, system=system)
        search = identifier_search(*identifier)
        request: JsonObject = (
            {"method": "PUT", "url": f"{one.kind}?{search}"}
            if write == "update"
            else {"method": "POST", "url": one.kind, "ifNoneExist": search}
        )
        resource = _rewrite(portable(one.resource), urns, unresolved, owner)
        entries.append({"fullUrl": urn, "resource": resource, "request": request})
    return {"resourceType": "Bundle", "type": mode, "entry": entries}
