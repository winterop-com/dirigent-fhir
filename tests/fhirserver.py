"""A fake FHIR R4 server in memory, answering the pack's client through ``httpx2.MockTransport``.

It serves ``/metadata``, reads, searches paged by ``_count`` with next links, conditional create
and update on ``identifier``, update by id, delete, a JSON Patch of top-level elements, operations
answered with a Parameters naming what they were sent, and transaction and batch Bundles that
resolve ``urn:uuid`` references. It refuses what a real server refuses: an id on a create, a reference it cannot
resolve on itself, more than one match for a conditional write. A test scripts a failure, a redirect, or a
next link elsewhere, and reads back every request the server took.
"""

import json
from collections.abc import Callable
from typing import Any, Final, cast
from urllib.parse import parse_qsl, unquote

import httpx2

#: The connection code the conftest installs on every context.
CONNECTION: Final = "fhir-test"

#: The base the conftest's connection points at.
BASE_URL: Final = "http://fhir.test/fhir"

Resource = dict[str, Any]


def outcome(status: int, code: str, diagnostics: str, severity: str = "error") -> httpx2.Response:
    """An error answer carrying an OperationOutcome, narrative included, the way HAPI writes one."""
    return httpx2.Response(
        status,
        json={
            "resourceType": "OperationOutcome",
            "text": {"status": "generated", "div": "<div>refused</div>"},
            "issue": [{"severity": severity, "code": code, "diagnostics": diagnostics}],
        },
        headers={"Content-Type": "application/fhir+json"},
    )


def _token_match(resource: Resource, token: str) -> bool:
    """Whether a resource carries the identifier a token search names."""
    system, _, value = token.partition("|")
    system, value = system.replace("\\|", "|"), value.replace("\\|", "|")
    for identifier in resource.get("identifier", []):
        if identifier.get("system") == system and (not value or identifier.get("value") == value):
            return True
    return False


class FhirServer:
    """An in-memory FHIR server: resources by type and id, and every request it took."""

    def __init__(self, base_url: str = BASE_URL) -> None:
        """Start empty."""
        self.base_url = base_url
        self.store: dict[str, dict[str, Resource]] = {}
        self.requests: list[httpx2.Request] = []
        self.scripted: list[Callable[[httpx2.Request], httpx2.Response | None]] = []
        self.next_link_override: str | None = None
        self._promised: set[str] = set()
        self._deleted: set[str] = set()
        self._ids = 0

    def seed(self, resource: Resource) -> Resource:
        """Store a resource under a new id, as if it had been created."""
        stored = self._store(dict(resource))
        return stored

    def count(self, kind: str) -> int:
        """How many resources of a type the server holds."""
        return len(self.store.get(kind, {}))

    def all(self, kind: str) -> list[Resource]:
        """Every resource of a type the server holds."""
        return list(self.store.get(kind, {}).values())

    def script(self, answer: Callable[[httpx2.Request], httpx2.Response | None]) -> None:
        """Answer requests from a function first, which returns None to let the server answer."""
        self.scripted.append(answer)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Answer one request."""
        self.requests.append(request)
        for answer in self.scripted:
            scripted = answer(request)
            if scripted is not None:
                return scripted
        path = request.url.path.removeprefix(httpx2.URL(self.base_url).path).strip("/")
        method = request.method
        if method == "GET" and path == "metadata":
            return self._json(200, self._capabilities())
        if method == "POST" and path == "":
            return self._bundle(json.loads(request.content))
        parts = path.split("/")
        if parts[-1].startswith("$"):
            return self._operation(request, parts)
        if method == "GET" and len(parts) == 2:
            found = self.store.get(parts[0], {}).get(parts[1])
            if found is None:
                return self._missing(path)
            return self._json(200, found)
        if method == "PUT" and len(parts) == 2:
            body = json.loads(request.content)
            if body.get("id") != parts[1]:
                return outcome(400, "processing", "the id in the body must match the id in the URL")
            created = parts[1] not in self.store.get(parts[0], {})
            stored = self._store(body, parts[1])
            self._deleted.discard(path)
            return self._json(201 if created else 200, stored, location=self._location(stored))
        if method == "DELETE" and len(parts) == 2:
            if self.store.get(parts[0], {}).pop(parts[1], None) is None:
                return self._missing(path)
            self._deleted.add(path)
            return outcome(200, "informational", "Successfully deleted 1 resource(s).", severity="information")
        if method == "PATCH":
            return self._patch(request, parts)
        if method == "GET" and len(parts) == 1:
            return self._search(parts[0], dict(parse_qsl(request.url.query.decode())))
        if method == "POST" and len(parts) == 1:
            status, body, _ = self._create(json.loads(request.content), request.headers.get("If-None-Exist"))
            return self._json(status, body, location=self._location(body))
        if method == "PUT" and len(parts) == 1:
            search = dict(parse_qsl(request.url.query.decode()))
            status, body, _ = self._update(parts[0], json.loads(request.content), search)
            return self._json(status, body, location=self._location(body))
        return outcome(400, "not-supported", f"{method} {path} is not served")

    def _missing(self, path: str) -> httpx2.Response:
        """A 410 for a resource deleted here, a 404 for one never held."""
        if path in self._deleted:
            return outcome(410, "deleted", f"Resource {path} was deleted")
        return outcome(404, "not-found", f"Resource {path} is not known")

    def _patch(self, request: httpx2.Request, parts: list[str]) -> httpx2.Response:
        """Apply a JSON Patch's top-level add, replace and remove, or a FHIRPath Patch as a new version only."""
        kind = parts[0]
        if len(parts) == 2:
            found = [self.store.get(kind, {})[parts[1]]] if parts[1] in self.store.get(kind, {}) else []
        else:
            found = self._matches(kind, dict(parse_qsl(request.url.query.decode())))
        if not found:
            return outcome(404, "not-found", f"nothing matches {kind}")
        if len(found) > 1:
            return outcome(412, "multiple-matches", "more than one resource matches")
        resource = {key: value for key, value in found[0].items() if key != "meta"}
        if request.headers["Content-Type"] == "application/json-patch+json":
            for operation in json.loads(request.content):
                key = operation["path"].strip("/")
                if operation["op"] == "remove":
                    resource.pop(key, None)
                else:
                    resource[key] = operation["value"]
        stored = self._store(resource, resource["id"])
        return self._json(200, stored, location=self._location(stored))

    def _operation(self, request: httpx2.Request, parts: list[str]) -> httpx2.Response:
        """Answer an operation with a Parameters naming its path, method, query and body."""
        named: list[Resource] = [
            {"name": "path", "valueString": "/".join(parts)},
            {"name": "method", "valueString": request.method},
        ]
        named.extend({"name": name, "valueString": value} for name, value in parse_qsl(request.url.query.decode()))
        if request.content:
            named.append({"name": "body", "resource": json.loads(request.content)})
        return self._json(200, {"resourceType": "Parameters", "parameter": named})

    def _json(self, status: int, body: Any, location: str | None = None) -> httpx2.Response:
        """A FHIR JSON answer."""
        headers = {"Content-Type": "application/fhir+json"}
        if location:
            headers["Location"] = location
        if isinstance(body, httpx2.Response):
            return body
        return httpx2.Response(status, content=json.dumps(body).encode(), headers=headers)

    def _location(self, body: Any) -> str | None:
        """Where a resource lives, versioned."""
        if not isinstance(body, dict) or "id" not in body:
            return None
        resource = cast("Resource", body)
        return f"{self.base_url}/{resource['resourceType']}/{resource['id']}/_history/{resource['meta']['versionId']}"

    def _capabilities(self) -> Resource:
        """A CapabilityStatement with more in it than the engine's model names."""
        return {
            "resourceType": "CapabilityStatement",
            "status": "active",
            "kind": "instance",
            "publisher": "Not provided",
            "date": "2026-11-01",
            "software": {"name": "Fake FHIR", "version": "1.0", "releaseDate": "2026-10-01"},
            "fhirVersion": "4.0.1",
            "format": ["application/fhir+json"],
            "rest": [{"mode": "server"}],
        }

    def _store(self, resource: Resource, existing: str | None = None) -> Resource:
        """Store a resource under its existing id or a new one, its version advanced."""
        kind = resource["resourceType"]
        self._ids += 1
        resource_id = existing or str(self._ids)
        previous = self.store.get(kind, {}).get(resource_id)
        version = int(previous["meta"]["versionId"]) + 1 if previous else 1
        stored = {**resource, "id": resource_id, "meta": {**resource.get("meta", {}), "versionId": str(version)}}
        self.store.setdefault(kind, {})[resource_id] = stored
        return stored

    def _matches(self, kind: str, search: dict[str, str]) -> list[Resource]:
        """The resources of a type a search matches, by identifier only."""
        found = self.all(kind)
        if "identifier" in search:
            found = [resource for resource in found if _token_match(resource, search["identifier"])]
        return found

    def _references_resolve(self, resource: Resource) -> str | None:
        """The first literal reference the server holds nothing under, or None."""

        def walk(value: Any) -> str | None:
            if isinstance(value, list):
                items: list[Any] = cast("list[Any]", value)  # type: ignore[redundant-cast]
                for item in items:
                    found = walk(item)
                    if found:
                        return found
            elif isinstance(value, dict):
                mapping = cast("dict[str, Any]", value)
                reference = mapping.get("reference")
                if isinstance(reference, str) and not reference.startswith("#") and "://" not in reference:
                    kind, _, rest = reference.partition("/")
                    held = rest.split("/")[0] in self.store.get(kind, {}) or reference in self._promised
                    if reference.startswith("urn:") or not held:
                        return reference
                for item in mapping.values():
                    found = walk(item)
                    if found:
                        return found
            return None

        return walk(resource)

    def _create(self, resource: Resource, if_none_exist: str | None) -> tuple[int, Any, Resource | None]:
        """Create, or find the one resource ``if_none_exist`` matches."""
        if "id" in resource:
            return 400, outcome(400, "processing", "Resource has an ID - ID must not be populated for a create"), None
        kind = resource["resourceType"]
        if if_none_exist:
            found = self._matches(kind, dict(parse_qsl(if_none_exist)))
            if len(found) > 1:
                return 412, outcome(412, "multiple-matches", "more than one resource matches"), None
            if found:
                return 200, found[0], found[0]
        dangling = self._references_resolve(resource)
        if dangling:
            return 400, outcome(400, "processing", f"Resource {dangling} not found"), None
        stored = self._store(resource)
        return 201, stored, stored

    def _update(self, kind: str, resource: Resource, search: dict[str, str]) -> tuple[int, Any, Resource | None]:
        """Write over the one resource a search matches, or create it."""
        if "id" in resource:
            return 400, outcome(400, "processing", "an id in the body must match the conditional URL"), None
        found = self._matches(kind, search)
        if len(found) > 1:
            return 412, outcome(412, "multiple-matches", "more than one resource matches"), None
        dangling = self._references_resolve(resource)
        if dangling:
            return 400, outcome(400, "processing", f"Resource {dangling} not found"), None
        stored = self._store(resource, found[0]["id"] if found else None)
        return (200 if found else 201), stored, stored

    def _search(self, kind: str, params: dict[str, str]) -> httpx2.Response:
        """A searchset page, with a next link when more remain."""
        found = self._matches(kind, params)
        count = int(params.get("_count", "2"))
        offset = int(params.get("_offset", "0"))
        page = found[offset : offset + count]
        bundle: Resource = {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": len(found),
            "link": [{"relation": "self", "url": f"{self.base_url}/{kind}"}],
            "entry": [
                {
                    "fullUrl": f"{self.base_url}/{kind}/{resource['id']}",
                    "resource": resource,
                    "search": {"mode": "match", "score": 1},
                }
                for resource in page
            ],
        }
        if offset + count < len(found):
            following = dict(params, _offset=str(offset + count), _count=str(count))
            url = self.next_link_override or f"{self.base_url}/{kind}?{httpx2.QueryParams(following)}"
            bundle["link"].append({"relation": "next", "url": url})
        return self._json(200, bundle)

    def _bundle(self, bundle: Resource) -> httpx2.Response:
        """Process a transaction or batch: ids assigned first, then urn:uuid references resolved."""
        mode = bundle["type"]
        entries = cast("list[Resource]", bundle.get("entry", []))
        urns: dict[str, str] = {}
        for entry in entries:
            request = entry["request"]
            kind = entry["resource"]["resourceType"]
            search = dict(parse_qsl(unquote(request["url"].partition("?")[2]) or request.get("ifNoneExist", "")))
            found = self._matches(kind, search) if search else []
            if len(found) == 1:
                urns[entry["fullUrl"]] = f"{kind}/{found[0]['id']}"
            else:
                self._ids += 1
                urns[entry["fullUrl"]] = f"{kind}/new-{self._ids}"
        snapshot = json.dumps(self.store)
        self._promised = set(urns.values())
        answers: list[Resource] = []
        for entry in entries:
            resource = json.loads(json.dumps(entry["resource"]))
            text = json.dumps(resource)
            for urn, target in urns.items():
                text = text.replace(f'"{urn}"', f'"{target}"')
            resource = json.loads(text)
            request = entry["request"]
            kind = resource["resourceType"]
            if request["method"] == "PUT":
                query = dict(parse_qsl(request["url"].partition("?")[2]))
                status, body, stored = self._update(kind, resource, query)
            else:
                status, body, stored = self._create(resource, request.get("ifNoneExist"))
            if stored is None and mode == "transaction":
                self.store = json.loads(snapshot)
                self._promised = set()
                return cast("httpx2.Response", body)
            if stored is not None and urns[entry["fullUrl"]].startswith(f"{kind}/new-"):
                self._rename(kind, stored["id"], urns[entry["fullUrl"]].split("/", 1)[1])
                stored = self.store[kind][urns[entry["fullUrl"]].split("/", 1)[1]]
            response: Resource = {"status": f"{status} {'Created' if status == 201 else 'OK'}"}
            if stored is not None:
                response["location"] = f"{kind}/{stored['id']}/_history/{stored['meta']['versionId']}"
            else:
                response["outcome"] = json.loads(cast("httpx2.Response", body).content)
            answers.append({"response": response})
        self._promised = set()
        return self._json(200, {"resourceType": "Bundle", "type": f"{mode}-response", "entry": answers})

    def _rename(self, kind: str, old: str, new: str) -> None:
        """Move a stored resource to the id a transaction promised its references."""
        resource = self.store[kind].pop(old)
        self.store[kind][new] = {**resource, "id": new}


def redirect(location: str) -> httpx2.Response:
    """A redirect to somewhere else."""
    return httpx2.Response(302, headers={"Location": location})
