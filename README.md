# dirigent-fhir

The FHIR pack for [dirigent](https://github.com/winterop-com/dirigent). It contributes the `fhir`
connection kind and the REST blocks for one FHIR R4 server, and FHIRPath on dirigent's transform,
map and filter verbs:

| Block | Effect | What it does |
| --- | --- | --- |
| `fhir.read` | reads | Reads one resource by type and id. |
| `fhir.search` | reads | Searches one type, following `Bundle.link[next]` on the connection's origin under a page cap, and hands on one Bundle, its pages, or its resources for a fan-out. |
| `fhir.create` | writes | Creates a resource, conditionally with `ifNoneExist`. |
| `fhir.update` | writes | Updates a resource with a conditional update on its identifier, never on a source server's id. |
| `fhir.transaction` | writes | Writes many resources in one transaction or batch Bundle, references between them rewritten to `urn:uuid` entries. |
| `fhir.validate` | computes | Validates resources and Bundles structurally: FHIR's JSON rules, and the engine's R4 models where they reach. |
| `fhir.cql` | computes | Evaluates a CQL library over the resources a step produced, per patient or once. |
| `fhir.measure` | computes | Evaluates a measure's populations over those resources, scores them, and answers a MeasureReport. |
| `transform.fhirpath` | computes | Reshapes a value with a FHIRPath expression. |
| `map.fhirpath` | computes | Replaces every element of a list with what a FHIRPath expression makes of it. |
| `filter.fhirpath` | computes | Keeps the elements of a list a FHIRPath expression answers true for. |

The three FHIRPath verbs read any JSON value, not only FHIR, and need no server: see
[On JSON that is not FHIR](docs/blocks.md#on-json-that-is-not-fhir).

It is FHIR as a system of its own, not a DHIS2 pack: FHIR to FHIR is as much its purpose as
feeding DHIS2. Between steps a resource or a Bundle is plain FHIR JSON. Inside a block the R4
models of `dhis2w-fhir-engine` type what they cover, and a resource they do not model passes
through as JSON. A server's `OperationOutcome` fails the step classified by its status and its
issue types, so a `transient` or `throttled` issue is retried and an `invalid` one is not.

Documentation: the [`docs/`](docs/) directory, built with `uv run mkdocs build --strict`.

## Install

Add it to a dirigent project, or start one with it:

```bash
uv add dirigent-fhir
dg init --pack dirigent-fhir
```

It moves in step with dirigent: install the version that matches the runtime.

An instance discovers the pack by installing it: the `dirigent.plugins.v1` entry point in
`pyproject.toml` is the whole registration.

## Develop

```bash
uv sync
uv run ruff format --check . && uv run ruff check .
uv run mypy && uv run pyright
uv run pytest -m "not live"
uv run mkdocs build --strict
```

The tests are offline: every block runs against a fake FHIR server in `tests/fhirserver.py`, and
the response bounds against a hostile server on loopback. `tests/test_fhir_live.py` reads a real
server with `DIRIGENT_LIVE=1 FHIR_LIVE_URL=http://localhost:8090/fhir`.

The pack follows dirigent's version: each release is built against the dirigent release of the
same number.
