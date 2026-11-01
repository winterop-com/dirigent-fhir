# Examples

The pack ships one shelf, `fhir`, which an instance lists under its examples once the pack is
installed. Every document talks to a local HAPI FHIR server, never a public one, because three of
them write. Start one, and a second for the copy:

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

A complete project around it, with a `verify.sh` that starts two HAPI servers, seeds one, copies,
and asserts the copy, is `fhir-to-fhir` in
[dirigent-examples](https://github.com/winterop-com/dirigent-examples).
