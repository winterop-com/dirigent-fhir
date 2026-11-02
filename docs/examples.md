# Examples

The pack ships one shelf, `fhir`, which an instance lists under its examples once the pack is
installed. Every document but `fhir-fhirpath-plain-json` talks to a local HAPI FHIR server, never a
public one, because three of them write. Start one, and a second for the copy:

```bash
docker run -d --name fhir-source -p 8090:8080 hapiproject/hapi:v8.12.0-2
docker run -d --name fhir-target -p 8091:8080 hapiproject/hapi:v8.12.0-2
```

| Document | What it does |
| --- | --- |
| `fhir-read` | Read one resource by its type and id. |
| `fhir-search-paged` | Search one type, following next links for at most five pages. |
| `fhir-create-if-none-exist` | Create a patient unless one with the same identifier is already there. |
| `fhir-update-by-identifier` | Write a patient over the one with the same identifier, or create it. |
| `fhir-copy-on-schedule` | Copy patients and their observations from one server to another, nightly. |
| `fhir-copy-page-by-page` | Copy patients and observations to another server, a transaction per page. |
| `fhir-fhirpath-high-readings` | Find body weights over a limit, and the patients they belong to, with FHIRPath. |
| `fhir-fhirpath-plain-json` | Filter, map and reshape records that are not FHIR, with no server. |
| `fhir-validate-before-copy` | Validate a search's resources, and copy them only if they are sound. |
| `fhir-measure-heavy-adults` | Score heavy adults as a proportion of adults, with CQL, into a MeasureReport. |

## FHIR to FHIR on a schedule

`fhir-copy-on-schedule` is the pack's first flagship. One search reads every patient on the
source with the observations that point at them (`_revinclude=Observation:subject`), and one
transaction writes them to the target:

```yaml
steps:
  find:
    block: fhir.search
    config:
      connection: fhir-source
      resource_type: Patient
      params:
        _revinclude: Observation:subject
        _count: "100"
      max_pages: 20

  copy:
    block: fhir.transaction
    depends_on: [find]
    config:
      connection: fhir-target
      bundle: ${steps.find.output.bundle}
```

The transaction rewrites each `Observation.subject` from the source's `Patient/17` to the
patient's `urn:uuid`, and writes every resource as a conditional update on its identifier. The
source's ids never reach the target, and the second night writes over what the first created:
`created` is zero and `updated` counts everything.

## A page at a time

`fhir-copy-page-by-page` copies the same search with `emit: pages`, so the search hands on one
Bundle per page and the copy fans out over them, one transaction per page:

```yaml
steps:
  find:
    block: fhir.search
    config:
      connection: fhir-source
      resource_type: Patient
      params:
        _revinclude: Observation:subject
        _count: "50"
      max_pages: 100
      emit: pages

  copy:
    block: fhir.transaction
    depends_on: [find]
    for_each: ${steps.find.output.bundles}
    items: continue
    config:
      connection: fhir-target
      bundle: ${item}
```

Each page is its own item with its own status and retry, and one refused page fails its own
item rather than the copy. `emit: resources` hands on one resource per item instead, for a
step such as `fhir.update` that writes one at a time.

A complete project around the copy, with a `verify.sh` that starts two HAPI servers, seeds one, copies,
and asserts the copy, is `fhir-to-fhir` in
[dirigent-examples](https://github.com/winterop-com/dirigent-examples).

## FHIRPath on plain JSON

`fhir-fhirpath-plain-json` runs the three FHIRPath verbs over records that are not FHIR, written
inline, with no connection and nothing else running:

```bash
dg run --local examples/fhir/fhir-fhirpath-plain-json.yaml -p limit=500000
```

`filter.fhirpath` keeps the cities over the limit, `map.fhirpath` builds a line from each and reads
a hyphenated key in backticks, and `transform.fhirpath` walks into one object. The
[block reference](blocks.md#on-json-that-is-not-fhir) says what to expect on data with no schema
behind it.
