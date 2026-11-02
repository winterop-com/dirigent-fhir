# dirigent-fhir

**The FHIR pack for dirigent.**

The pack contributes one connection kind and five blocks that speak FHIR R4's REST interface as
first-class steps: a read, a search that follows the server's pages, a conditional create, a
conditional update, and a transaction that carries many resources with the references between
them rewritten. A pipeline that names `fhir.transaction` says what it wants written; it does not
assemble the Bundle, invent `urn:uuid`s, or parse the `OperationOutcome` itself. Beside them it
validates resources, evaluates CQL and scores measures, and puts FHIRPath on dirigent's transform
verbs, where it reads any JSON and not only FHIR.

It is FHIR as a system of its own, not a DHIS2 pack: a pipeline that reads from one FHIR server
and writes to another, neither of them DHIS2, is as much its purpose as feeding DHIS2. Between
steps a resource or a Bundle is plain FHIR JSON, so `transform.jq`, references and storage work
on it unchanged.

## What is in it

- The [`fhir` connection kind](connection.md): one server's base, its credential, the limits one
  answer is held to, and a health check that reads `/metadata`.
- The [blocks](blocks.md): two that read and three that write, every write conditional on FHIR's
  own means; `fhir.validate`, `fhir.cql` and `fhir.measure`, which compute on what they are given;
  and `transform.fhirpath`, `map.fhirpath` and `filter.fhirpath`, which
  [work on any JSON](blocks.md#on-json-that-is-not-fhir).
- One [example](examples.md) document per operation, and a FHIR to FHIR copy on a schedule.

## What it is built on

Inside a block, the engine's R4 models type what they cover: an `OperationOutcome` read into a
step failure, a search's pages folded into a `Bundle`, a `CapabilityStatement`'s FHIR version. A
resource they do not model passes through as JSON, so the list of models never gates a pipeline.
The models come from `dhis2w-fhir-engine`, as published on PyPI. The pack imports none of the
engine's DHIS2 code; the engine as published depends on `dhis2w-client`, so installing the pack
installs that too.

The REST client is the pack's own: search paging, conditional create and update, transaction
Bundles, `OperationOutcome` classification and `/metadata`, every request held to the connection's
origin and limits.

## Install

Install the pack into a dirigent image:

```bash
uv pip install dirigent-fhir
```

or add it to a project:

```bash
uv add dirigent-fhir
```

An instance discovers the pack by installing it: the `dirigent.plugins.v1` entry point is the whole
registration. It moves in step with dirigent: install the version that matches the runtime.
