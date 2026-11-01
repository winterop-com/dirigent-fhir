# FHIR examples: the REST blocks and FHIRPath

These pipelines use the blocks the `dirigent-fhir` pack contributes: a read, a paged search, a
conditional create, a conditional update, a transaction that carries many resources at once with
the references between them rewritten, and the FHIRPath verbs `transform.fhirpath`,
`map.fhirpath` and `filter.fhirpath`. Installing the pack is what puts
the blocks in the catalog; an instance without it refuses these at the block preflight.

Every document talks to a local HAPI FHIR server rather than a public one, because three of them
write. Start one, and a second on another port for the copy:

```bash
docker run -d --name fhir-source -p 8090:8080 hapiproject/hapi:v8.12.0-2
docker run -d --name fhir-target -p 8091:8080 hapiproject/hapi:v8.12.0-2
```

Each document carries the connection it uses so it runs standalone. `dg pipeline new` makes a
copy from a document tagged `starter`, with the `connections:` block replaced by the codes
under `requires.connections`, so create a `fhir` connection under that code or rename it.

| File | The operation |
| --- | --- |
| [fhir-read.yaml](fhir-read.yaml) | Read one resource by its type and id. |
| [fhir-search-paged.yaml](fhir-search-paged.yaml) | Search one type, following next links under a page cap. |
| [fhir-create-if-none-exist.yaml](fhir-create-if-none-exist.yaml) | Create a resource unless one with its identifier is already there. |
| [fhir-update-by-identifier.yaml](fhir-update-by-identifier.yaml) | Write a resource over the one with its identifier, or create it. |
| [fhir-copy-on-schedule.yaml](fhir-copy-on-schedule.yaml) | Copy patients and their observations from one server to another, nightly. |
| [fhir-copy-page-by-page.yaml](fhir-copy-page-by-page.yaml) | Copy a search to another server, one transaction per page, fanned out. |
| [fhir-fhirpath-high-readings.yaml](fhir-fhirpath-high-readings.yaml) | Filter, map and reshape a search's observations with FHIRPath. |
