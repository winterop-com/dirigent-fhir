# Block reference

Every block this pack contributes, with the config it takes and the output it produces. The
tables are the two JSON Schemas each block publishes into the catalog -- the same ones
`GET /blocks` serves, the same ones the UI builds its step form from.

What a block is, and the contract behind these tables, is
[dirigent's own](https://winterop-com.github.io/dirigent/blocks/). Every block here is an
operator. The REST blocks declare the `fhir` group and take a `connection` naming a
[`fhir` connection](connection.md) by its code. The [FHIRPath verbs](#fhirpath-on-the-verb-frames)
are dirigent's own `transform`, `map` and `filter` frames with FHIRPath as their language: they
declare the `transform` group, take no connection, and reach no server.

**Effect** says what a block does to the server: the two reads change nothing there, the three
writes do, and `fhir.validate`, `fhir.cql`, `fhir.measure` and the three FHIRPath verbs only compute, on what
they are handed. **Idempotent** says whether re-running the block after an unclear
failure is safe. The engine spends the step's retry budget either way, so this is what to read
before writing `max_attempts` above 1. No block in this pack runs code on the worker, so none
has to be allowlisted.

Between steps a resource or a Bundle is plain FHIR JSON, so `transform.jq`, references and
storage work on it unchanged.

| Block | Effect | Idempotent | Summary |
| --- | --- | --- | --- |
| [`fhir.read`](#fhirread) | reads | yes | Read one FHIR resource by type and id. |
| [`fhir.search`](#fhirsearch) | reads | yes | Search FHIR resources, following next links. |
| [`fhir.create`](#fhircreate) | writes | no | Create a FHIR resource, conditionally with ifNoneExist. |
| [`fhir.update`](#fhirupdate) | writes | yes | Update a FHIR resource matched on its identifier. |
| [`fhir.transaction`](#fhirtransaction) | writes | yes | Write FHIR resources in one transaction or batch. |
| [`fhir.validate`](#fhirvalidate) | computes | yes | Validate FHIR resources and Bundles structurally. |
| [`fhir.cql`](#fhircql) | computes | yes | Evaluate a CQL library over FHIR resources. |
| [`fhir.measure`](#fhirmeasure) | computes | yes | Evaluate a measure and answer a MeasureReport. |
| [`transform.fhirpath`](#transformfhirpath) | computes | yes | Reshape a value with a FHIRPath expression. |
| [`map.fhirpath`](#mapfhirpath) | computes | yes | Replace every element of a list with what a FHIRPath expression makes of it. |
| [`filter.fhirpath`](#filterfhirpath) | computes | yes | Keep the elements of a list a FHIRPath expression answers true for. |

## Operators

### `fhir.read`

Read one FHIR resource by type and id.

Idempotent. Reads.

Reads `GET [base]/{resource_type}/{id}` and hands the resource on as plain FHIR JSON. An answer that is not a resource of the type asked for fails the step, and so does a resource the server does not hold, with the server's `OperationOutcome` in the message.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `connection` | `string` | yes |  | The code of the fhir connection naming the server. |
| `resource_type` | `string` | yes |  | The resource's type, such as `Patient` or `Observation`. |
| `id` | `string` | yes |  | The resource's id on that server. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `resource` | `object` | yes |  | The resource as plain FHIR JSON. |
| `version_id` | `string or null` |  | `null` | The version the server holds, from `meta.versionId`. |

### `fhir.search`

Search FHIR resources, following next links.

Idempotent. Reads.

Searches `GET [base]/{resource_type}` and follows `Bundle.link[next]` for at most `max_pages` pages, folding every page into one `searchset` Bundle built through the engine's R4 `Bundle` model. A next link is followed only on the origin of the connection's `base_url`: a link to any other scheme, host or port fails the step before anything is sent there. An entry the server marks as an `outcome` is answered under `issues` rather than inside the Bundle. Every page is held to the connection's `max_response` and `request_deadline` on its own, and every page read together to `max_total`: the search fails, rejected, with `fhir.search.too_large` and reads no further once its pages pass it. Each page is parsed, and the pages folded, off the event loop.

`emit` decides what the step hands on. The default folds every page into one Bundle. `pages` and `resources` hand on a list instead, which is what a following step's `for_each` maps over: `for_each: ${steps.find.output.bundles}` gives each page its own item, with its own status and retry, and `${item}` is that page's Bundle. A page that holds no result is left out, so an empty search hands on an empty list, and an empty list skips the fan-out. The grid is written when the search has succeeded, and it is held to the instance's `fan_out_max_items`.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `connection` | `string` | yes |  | The code of the fhir connection naming the server. |
| `resource_type` | `string` | yes |  | The resource type searched, such as `Patient`. |
| `params` | `object of string or string[]` |  |  | The search parameters as FHIR spells them, such as `{identifier: "http://example.org/mrn\|", _count: "50"}`; a list sends the parameter once per value, which FHIR reads as AND. |
| `max_pages` | `integer` |  | `10` | The most pages followed, the first included. |
| `max_total` | `size` |  | `"64MB"` | The most every page read may decode to together; a search past it fails, and the rest is not read. |
| `on_more` | `"fail" or "stop"` |  | `"fail"` | What a next link past `max_pages` does: `fail` fails the step, `stop` answers with the pages read and `complete` false. |
| `emit` | `"bundle" or "pages" or "resources"` |  | `"bundle"` | What the step hands on: `bundle` folds every page into one Bundle under `bundle`, `pages` lists each page as its own searchset Bundle under `bundles`, and `resources` lists every resource under `resources`, so a following step's `for_each` maps over either list. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `bundle` | `object or null` |  | `null` | With `emit: bundle`, a `searchset` Bundle holding every entry of every page read, as plain FHIR JSON. |
| `bundles` | `object[] or null` |  | `null` | With `emit: pages`, one `searchset` Bundle per page read that holds a result, in the order the server answered them. |
| `resources` | `object[] or null` |  | `null` | With `emit: resources`, every resource of every page read, included resources counted, in order. |
| `count` | `integer` | yes |  | How many entries the pages hold, included resources counted. |
| `total` | `integer or null` |  | `null` | How many matches the server says there are, when it says. |
| `pages` | `integer` | yes |  | How many pages were read. |
| `complete` | `boolean` | yes |  | Whether the last page read had no next link. |
| `issues` | `object[]` |  |  | What the server said about the search in an `OperationOutcome` entry, such as an unknown parameter. |

### `fhir.create`

Create a FHIR resource, conditionally with ifNoneExist.

Not idempotent: with `if_none_exist` unset every run creates another resource. Writes.

Posts the resource to `[base]/{resourceType}`, without its `id` and its version metadata, asking for the stored resource back. With `if_none_exist` the server is sent `If-None-Exist`, creates the resource only when nothing matches, and answers with the one that does otherwise; `outcome` says which. More than one match fails the step, rejected. A `resourceType` that is not a FHIR type name, an upper-case letter and then letters, fails the step, rejected, before anything is sent, so the path is always `[base]/{resourceType}`.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `connection` | `string` | yes |  | The code of the fhir connection naming the server. |
| `resource` | `object` | yes |  | The resource as plain FHIR JSON; its `id` and version metadata are not sent, since the server assigns its own. |
| `if_none_exist` | `string or null` |  | `null` | A search, such as `identifier=http://example.org/mrn\|12345`, that makes the create conditional: when one resource matches, nothing is written and the step answers `existing`. Unset, every run creates another. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `outcome` | `"created" or "updated" or "existing"` | yes |  | `created` when the server made a new resource, `updated` when it wrote over the one that matched, and `existing` when a conditional create found one already there and wrote nothing. |
| `status` | `integer` | yes |  | The HTTP status the server answered with. |
| `id` | `string or null` |  | `null` | The id the server holds the resource under. |
| `version_id` | `string or null` |  | `null` | The version the server holds. |
| `location` | `string or null` |  | `null` | Where the server says the resource lives. |
| `resource` | `object or null` |  | `null` | The resource as the server stored it, when it answered with it. |
| `issues` | `object[]` |  |  | What the server said about the write in an `OperationOutcome`, such as a warning. |

### `fhir.update`

Update a FHIR resource matched on its identifier.

Idempotent. Writes.

A conditional update: `PUT [base]/{resourceType}?identifier=system|value`, on the first of the resource's identifiers with both a system and a value, or the first under `identifier_system`. The server writes over the one resource that matches, creates it when none does, and refuses when several do. The resource's own `id` is never sent, so an id from another server never decides what is overwritten. A resource with no such identifier, or with a `resourceType` that is not a FHIR type name, fails the step before anything is sent.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `connection` | `string` | yes |  | The code of the fhir connection naming the server. |
| `resource` | `object` | yes |  | The resource as plain FHIR JSON; its `id` and version metadata are not sent, so the source server's id never decides what is overwritten. |
| `identifier_system` | `string or null` |  | `null` | The system of the identifier the resource is matched on; the first identifier with both a system and a value when unset. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `outcome` | `"created" or "updated" or "existing"` | yes |  | `created` when the server made a new resource, `updated` when it wrote over the one that matched, and `existing` when a conditional create found one already there and wrote nothing. |
| `status` | `integer` | yes |  | The HTTP status the server answered with. |
| `id` | `string or null` |  | `null` | The id the server holds the resource under. |
| `version_id` | `string or null` |  | `null` | The version the server holds. |
| `location` | `string or null` |  | `null` | Where the server says the resource lives. |
| `resource` | `object or null` |  | `null` | The resource as the server stored it, when it answered with it. |
| `issues` | `object[]` |  |  | What the server said about the write in an `OperationOutcome`, such as a warning. |

### `fhir.transaction`

Write FHIR resources in one transaction or batch.

Idempotent: every entry is conditional on its identifier. Writes.

Takes the resources from `resources`, or from the entries of `bundle` such as a `fhir.search` step's output, and posts one `transaction` or `batch` Bundle to the base. Each resource becomes an entry with a fresh `urn:uuid` `fullUrl`, without its source `id` and version metadata, and with a conditional request on its identifier: `PUT Type?identifier=` under `write: update`, `POST Type` with `ifNoneExist` under `write: create`.

Every reference between the resources the Bundle carries is rewritten to the carried resource's `urn:uuid`, whether it named the resource as `Type/id`, `Type/id/_history/n`, or by the `fullUrl` the source entry gave, and in any element: `subject`, a list such as `performer`, or an extension's `valueReference`. The target server resolves each urn to wherever it stores the resource. A contained reference (`#id`), a `urn:`, a conditional reference (`Type?search`) and an absolute URL no carried entry was read from are left as written. A relative `Type/id` to a resource the Bundle does not carry fails the step under the default `unresolved_references: fail`, because on the target it would name whatever holds that id there.

A transaction the server refuses fails the step with its `OperationOutcome`, and nothing is written. A batch writes each entry on its own: a failed entry fails the step under `on_failed: fail`, and is counted under `failed` and logged under `report`.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `connection` | `string` | yes |  | The code of the fhir connection naming the server written to. |
| `resources` | `object[] or null` |  | `null` | The resources to write, as plain FHIR JSON. Set this or `bundle`. |
| `bundle` | `object or null` |  | `null` | A Bundle whose entries hold the resources to write, such as a `fhir.search` step's `bundle`. Set this or `resources`. |
| `mode` | `"transaction" or "batch"` |  | `"transaction"` | `transaction` writes everything or nothing; `batch` writes each entry on its own. |
| `write` | `"update" or "create"` |  | `"update"` | `update` writes each resource with a conditional update on its identifier, creating it when nothing matches; `create` writes each with a conditional create, leaving one that matches as it is. |
| `identifier_system` | `string or null` |  | `null` | The system of the identifier each resource is matched on; each resource's first identifier with both a system and a value when unset. A resource without one fails the step: the source server's id is never used. |
| `unresolved_references` | `"fail" or "keep"` |  | `"fail"` | What a relative reference such as `Patient/123` to a resource the transaction does not carry does: `fail` fails the step, since on the target it would name whatever holds that id; `keep` sends it as written. |
| `on_failed` | `"fail" or "report"` |  | `"fail"` | What a failed batch entry does: `fail` fails the step, `report` counts it under `failed` and logs it. A transaction is written whole or not at all, so this only reaches a batch. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `created` | `integer` | yes |  | How many resources the server created. |
| `updated` | `integer` | yes |  | How many the server wrote over, or found already there. |
| `failed` | `integer` | yes |  | How many entries of a batch failed, under `on_failed: report`. |
| `entries` | `object[]` | yes |  | One result per resource written, in the order they were sent. |
| `response` | `object` | yes |  | The server's `transaction-response` or `batch-response` Bundle, as plain FHIR JSON. |

### `fhir.validate`

Validate FHIR resources and Bundles structurally.

Idempotent. Computes. Takes no connection.

Validates a resource, a Bundle with every entry, or a list of resources, and every resource they contain. Two layers, both structural:

- **FHIR's JSON rules, for every resource of any type.** A `resourceType`, an `id` of 1 to 64 letters, digits, hyphens and dots, no element written empty (`""`, `{}`, `[]`) or `null` (a `null` stands in a repeating primitive only where the `_element` beside it carries extensions), and every `reference` in a form a server resolves: `Type/id`, a versioned or conditional one, an absolute URL, a `urn:uuid` or `urn:oid`, or `#id`, which has to name a resource the referring one contains. A Bundle uses each `fullUrl` once.
- **The engine's R4 model, for a type it models.** `Patient`, `Observation`, `Condition`, `AllergyIntolerance`, `Immunization`, `Organization`, `Location`, `List`, `Questionnaire`, `QuestionnaireResponse`, `Composition`, `Bundle`, `OperationOutcome`, `Parameters`, `CodeSystem`, `ValueSet`, `ConceptMap` and `CapabilityStatement` are validated strictly through the engine's closed model: a coded element's value set, such as `Patient.gender` or `Observation.status`, and every element's JSON type. The engine's models name a subset of each resource's elements, so the resource is projected onto the elements the model names first: an element it does not name, such as `Observation.valueQuantity`, is left unjudged rather than refused.

`profile` holds every resource handed in, a Bundle's entries rather than the Bundle, to the engine's `conformsTo()`: the resource claims the profile under `meta.profile`, or the profile is the base StructureDefinition of its type. That is all the profile support the engine has. It resolves no StructureDefinition, so a profile's own constraints, cardinalities and bindings are not checked, and no package or implementation guide is loaded.

An error fails the step with `fhir.validate.invalid`, classified `rejected`, naming the first three issues and counting the rest, with every issue under the failure's `issues` param. Under `on_invalid: report` the step succeeds and answers `valid` false. Either way each issue carries FHIR's issue type and the FHIRPath of the element it is about, such as `Bundle.entry[1].resource.gender`, and `outcome` is the same issues as an `OperationOutcome` a later step can write or hand on. Validation runs off the event loop.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `input` | `any JSON value` | yes |  | A resource, a Bundle whose entries are validated too, or a list of resources, such as a `fhir.search` step's `bundle` or `resources`. |
| `profile` | `string or null` |  | `null` | A profile's canonical URL every resource handed in must conform to, by the engine's `conformsTo()`: the resource claims it under `meta.profile`, or it is the base StructureDefinition of the resource's type. |
| `on_invalid` | `"fail" or "report"` |  | `"fail"` | What an error does: `fail` fails the step listing the issues, `report` answers with `valid` false. |
| `max_issues` | `integer` |  | `100` | The most issues collected before validation stops. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `valid` | `boolean` | yes |  | Whether no issue is an error. |
| `checked` | `integer` | yes |  | How many resources were checked, contained and Bundle entries counted. |
| `issues` | `object[]` |  |  | Every issue found, each with the FHIRPath of the element it is about. |
| `truncated` | `boolean` |  | `false` | Whether validation stopped at `max_issues` with more left to check. |
| `outcome` | `object` | yes |  | The issues as an `OperationOutcome`, built through the engine's model, as plain FHIR JSON. |

### `fhir.cql`

Evaluate a CQL library over FHIR resources.

Idempotent. Computes. Takes no connection.

Evaluates a CQL library, written in the step as source, on the engine's CQL evaluator over the resources in `data`: a Bundle, such as a `fhir.search` step's `bundle`, or a list of resources, read through the engine's Bundle data source, so a retrieve such as `[Observation: "Body weight"]` reads what the step was handed and nothing else. A library in the `Patient` context is evaluated once for each patient the data carries, answering under `patients`; one with no `Patient` context is evaluated once, answering under `values`. FHIRHelpers 4.0.1 is built in. A value reaches the library only as a CQL parameter, by name under `parameters`, and dirigent refuses a `${...}` written in `library` itself.

A library is compiled at apply, where a bad one is refused with the compiler's message, and evaluated in a runner process of its own, so a step's timeout ends a long evaluation and the worker's event loop stays free. A definition that fails to evaluate fails the step with `fhir.cql.definitions_failed`, `rejected`, naming the first three. Values are plain JSON: a decimal a number, a date its FHIR spelling, a code `{code, system}`, an interval `{low, high, lowClosed, highClosed}`, a resource as FHIR JSON. Libraries are not resolved from storage or a package, and terminology for `in` comes only from what the library and the data carry.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `library` | `string` | yes |  | The CQL library, as source, with `using FHIR version '4.0.1'`. |
| `data` | `any JSON value` | yes |  | The resources the library reads: a Bundle, such as a `fhir.search` step's `bundle`, or a list of resources. |
| `parameters` | `object` |  |  | Values for the library's parameters by name, each written inline or as a `${...}` reference. |
| `max_output` | `size` |  | `"16mb"` | The most the evaluation's answer may run to as JSON, such as `16MB`. |
| `definitions` | `string[] or null` |  | `null` | The definitions answered, every one the library defines when unset. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `context` | `"Patient" or "Unfiltered"` | yes |  | `Patient` when the library evaluates per patient, `Unfiltered` when once over everything. |
| `values` | `object` |  |  | In the Unfiltered context, each definition's value by name. |
| `patients` | `object[]` |  |  | In the Patient context, each patient the data carries with that patient's values. |

### `fhir.measure`

Evaluate a measure and answer a MeasureReport.

Idempotent. Computes. Takes no connection.

Evaluates a measure's CQL library over the resources in `data` as `fhir.cql` does, and answers a summary `MeasureReport`. The library names its populations the conventional way, `Initial Population`, `Denominator`, `Denominator Exclusion`, `Denominator Exception`, `Numerator` and `Numerator Exclusion`, in the `Patient` context. The engine evaluates each definition for each patient the data carries; the block then nests them the way the scoring nests them before it counts, so a patient is in the numerator only when the denominator holds it:

- `proportion`: the denominator is drawn from the initial population, the numerator from the denominator less its exclusions, and an exception only from a denominator member the numerator does not hold. The score is (numerator - numerator exclusion) / (denominator - denominator exclusion - denominator exception).
- `ratio`: the numerator and the denominator are each drawn from the initial population, apart. The score is (numerator - numerator exclusion) / (denominator - denominator exclusion).
- `cohort`: the initial population only, and no score.

A library with no `Initial Population` fails the step with `fhir.measure.no_populations`. The report states `measure` and the reporting period as given, and each population with its code from `http://terminology.hl7.org/CodeSystem/measure-population`. Continuous-variable measures, stratifiers and the library's own `Measurement Period` are not read into the report.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `library` | `string` | yes |  | The CQL library, as source, with `using FHIR version '4.0.1'`. |
| `data` | `any JSON value` | yes |  | The resources the library reads: a Bundle, such as a `fhir.search` step's `bundle`, or a list of resources. |
| `parameters` | `object` |  |  | Values for the library's parameters by name, each written inline or as a `${...}` reference. |
| `max_output` | `size` |  | `"16mb"` | The most the evaluation's answer may run to as JSON, such as `16MB`. |
| `measure` | `string` | yes |  | The canonical URL of the Measure the report is about, such as `http://example.org/Measure/heavy`. |
| `period_start` | `string` | yes |  | The start of the reporting period, a FHIR date or dateTime, as the report states it. |
| `period_end` | `string` | yes |  | The end of the reporting period, a FHIR date or dateTime, as the report states it. |
| `scoring` | `"proportion" or "ratio" or "cohort"` |  | `"proportion"` | How the populations nest and are scored: `proportion` nests each in the one before it, `ratio` keeps the numerator and denominator apart, `cohort` counts the initial population only. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `populations` | `object` | yes |  | How many patients each population the library defines holds, by population code. |
| `score` | `number or null` |  | `null` | The measure score: numerator over denominator after exclusions and exceptions; none for a cohort or an empty denominator. |
| `members` | `object` | yes |  | Which patients each population holds, as `Patient/id`. |
| `report` | `object` | yes |  | A summary `MeasureReport`, as plain FHIR JSON, for a later step to write or hand on. |

## FHIRPath on the verb frames

`transform.fhirpath`, `map.fhirpath` and `filter.fhirpath` are dirigent's
[transform verbs](https://winterop-com.github.io/dirigent/transforms/) with FHIRPath as their
language, evaluated by the engine's FHIRPath evaluator. Each keeps its frame's promise: a
transform reshapes one value, a map keeps the list's length, and a filter keeps elements
unmodified. Mapping between two servers' profiles is where FHIRPath earns its place over jq: it
walks a resource the way FHIR names it, `name.where(use = 'official').family`, with FHIR's own
dates, quantities and `ofType()`.

**What a collection becomes.** A FHIRPath answer is a collection. Under `transform.fhirpath` and
`map.fhirpath` an empty one is `null`, one item is the item itself, and several are their list.
Under `filter.fhirpath` the answer is read the way FHIRPath's own `where()` reads its criteria:
`true` keeps the element, `false` and an empty answer drop it, and anything else is a refusal.
A date or a time is its FHIR spelling, a decimal a number, and a quantity `{value, unit}`.

**A value reaches an expression only as a variable.** Each entry of `variables` is bound as a
FHIRPath environment variable and read as `%name`. A reference there resolves as data, so a value
from a caller's input cannot end a string or add a clause:

```yaml
keep:
  block: filter.fhirpath
  config:
    input: ${steps.find.output.resources}
    program: active and birthDate >= %born
    variables:
      born: ${params.born_after}
```

The program field publishes `application/fhirpath`, and dirigent refuses a `${...}` written in
it, at apply and when the step starts, rather than splice a value into the expression's text.
An expression reading a `%name` the step does not bind is refused too, instead of evaluating to
nothing, and so is a variable named after one FHIRPath defines itself: `resource`,
`rootResource`, `context`, `ucum`, `sct`, `loinc`.

**Bounded in time and size.** An expression is parsed at apply, where a bad one is refused with
the parser's own message, and is never evaluated on the worker: it runs in a process of its own
over dirigent's runner protocol, so the worker's event loop stays free and a step's timeout ends
a long evaluation by killing that process. One evaluation's answer may run to `max_output` as
JSON. The evaluator opens no file and no socket: `resolve()` and the terminology functions have
nothing to reach.

### `transform.fhirpath`

Reshape a value with a FHIRPath expression.

Idempotent. Computes.

Evaluates the expression over `input` as `%resource`, a resource or a Bundle, and hands the answer on as `value`. A list as `input` is evaluated as a collection.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `input` | `any JSON value` | yes |  | The value to work on, written inline or referenced from an earlier step's output. |
| `program` | `string` (FHIRPath) | yes |  | The FHIRPath expression this step evaluates, with `%resource` the value it is evaluated over. |
| `variables` | `object` |  |  | Values the expression reads as `%name`, each written inline or as a `${...}` reference. |
| `max_output` | `size` |  | `"16mb"` | The most one evaluation's answer may run to as JSON, such as `16MB`. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `value` | `any JSON value` |  | `null` | The reshaped value, which a later step reads or hands to `storage.write`. |

### `map.fhirpath`

Replace every element of a list with what a FHIRPath expression makes of it.

Idempotent. Computes.

Evaluates the expression over each element of `input` on its own, as `%resource`, and replaces the element with the answer. An `input` that is not a list fails the step.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `input` | `any JSON value` | yes |  | The value to work on, written inline or referenced from an earlier step's output. |
| `program` | `string` (FHIRPath) | yes |  | The FHIRPath expression this step evaluates, with `%resource` the value it is evaluated over. |
| `variables` | `object` |  |  | Values the expression reads as `%name`, each written inline or as a `${...}` reference. |
| `max_output` | `size` |  | `"16mb"` | The most one evaluation's answer may run to as JSON, such as `16MB`. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `value` | `any JSON value` |  | `null` | The reshaped value, which a later step reads or hands to `storage.write`. |

### `filter.fhirpath`

Keep the elements of a list a FHIRPath expression answers true for.

Idempotent. Computes.

Evaluates the expression over each element of `input` on its own, as `%resource`, and keeps the element unmodified when the answer is `true`. An answer that is not one boolean or nothing fails the step, naming the element by its index.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `input` | `any JSON value` | yes |  | The value to work on, written inline or referenced from an earlier step's output. |
| `program` | `string` (FHIRPath) | yes |  | The FHIRPath expression this step evaluates, with `%resource` the value it is evaluated over. |
| `variables` | `object` |  |  | Values the expression reads as `%name`, each written inline or as a `${...}` reference. |
| `max_output` | `size` |  | `"16mb"` | The most one evaluation's answer may run to as JSON, such as `16MB`. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `value` | `any JSON value` |  | `null` | The reshaped value, which a later step reads or hands to `storage.write`. |

## How a failure is classified

A server's refusal fails the step with `fhir.answered`, quoting its `OperationOutcome`: the
first three issues as `severity code: diagnostics (at expression)`, and how many more there
were. The issues ride along whole under the failure's `issues` param. The outcome is read
through the engine's R4 `OperationOutcome` model, projected onto the elements it names; an
outcome that does not fit even so is read as plain JSON, and a body that is no outcome at all
is quoted as text.

| The server answered | Class | Retried |
| --- | --- | --- |
| 5xx, or 429 | transient | yes |
| any status, with an error issue of type `transient`, `timeout`, `throttled` or `lock-error` | transient | yes |
| any other 4xx: `invalid`, `not-found`, `multiple-matches`, `business-rule` and the rest | rejected | no |
| a transport failure, or a request past `request_deadline` | transient | yes |
| an answer past `max_response`, or a request off the connection's origin | rejected | no |

This is the dhis2 pack's rule for an import conflict, read off FHIR's own vocabulary: the
issue type, not the English, decides whether a retry can help.

## Every refusal carries a code

A refusal is catalogued: it carries a stable dotted code beside the sentence it renders, so a
stream of run records is selected by code however the English is later reworded --
`jq 'select(.error_code == "fhir.search.more_pages")'`. The code is API and the text is not.


| Code | Text |
| --- | --- |
| `fhir.answered` | `{where} answered {status}: {remote}` |
| `fhir.not_json` | `{where} answered with a body that is not FHIR JSON` |
| `fhir.not_a_resource` | `{where} answered with {found} where a {expected} resource was expected` |
| `fhir.response_too_large` | `{where} answered with more than max_response ({limit} bytes) and the rest was not read; raise max_response if the answer is expected` |
| `fhir.past_deadline` | `{where} took longer than the {seconds} seconds it is allowed` |
| `fhir.off_origin` | `a request to {target} was refused, because only {origin}, the origin of base_url, is sent requests through this connection` |
| `fhir.too_many_redirects` | `{where} was redirected more than {limit} times, and the redirects were not followed further` |
| `fhir.issues_and_more` | `{named} (and {more} more)` |
| `fhir.connection.bad_token` | `a bearer_token must be visible ASCII with no whitespace, or it cannot be sent as a header value` |
| `fhir.connection.both_credentials` | `a connection carries a bearer_token or basic credentials, not both` |
| `fhir.connection.no_basic_password` | `basic_username is set but basic_password is not` |
| `fhir.connection.no_basic_username` | `basic_password is set but basic_username is not` |
| `fhir.connection.bad_base_url` | `base_url must be an absolute http or https URL` |
| `fhir.connection.unreachable` | `the FHIR server did not answer: {detail}` |
| `fhir.connection.not_a_capability_statement` | `{where} answered with {found} rather than a CapabilityStatement, so this is not a FHIR server's base` |
| `fhir.search.more_pages` | `the search has more than max_pages ({max_pages}) pages, and on_more is fail; raise max_pages, narrow the search, or set on_more to stop` |
| `fhir.search.too_large` | `{where} took the search's pages past max_total ({limit} bytes) together, and the rest was not read; raise max_total, narrow the search, or lower max_pages` |
| `fhir.resource.no_resource_type` | `the resource carries no resourceType` |
| `fhir.resource.bad_resource_type` | `the resource's resourceType is not a FHIR resource type, an upper-case letter and then letters such as Patient, so it is not written` |
| `fhir.resource.no_identifier` | `the {resource} carries no identifier with both a system and a value{system}, so there is nothing to match it on; the source server's id is never used` |
| `fhir.resource.identifier_system` | ` under the system {system}` |
| `fhir.transaction.nothing` | `a transaction step needs resources or a bundle, exactly one of them` |
| `fhir.transaction.unresolved_reference` | `the {resource} refers to {reference}, which the transaction does not carry, so it would point at whatever holds that id on the target; carry it, or set unresolved_references to keep` |
| `fhir.transaction.duplicate` | `the transaction carries {resource} twice, and a reference to it would be ambiguous` |
| `fhir.transaction.no_response` | `{where} answered with {found} entries for the {sent} the transaction sent, so its outcome is unknown` |
| `fhir.transaction.entries_failed` | `{failed} of {total} batch entries failed: {detail}` |
| `fhir.validate.invalid` | `{count} issues were found in the {checked} resources checked: {detail}` |
| `fhir.validate.not_a_resource` | `this is not a FHIR resource: an object with a resourceType such as Patient` |
| `fhir.validate.resource_id` | `an id is 1 to 64 letters, digits, hyphens and dots` |
| `fhir.validate.model` | `the engine's R4 model refuses it: {detail}` |
| `fhir.validate.empty` | `an element is left out rather than written empty, which FHIR JSON does not allow` |
| `fhir.validate.null` | `null is not a FHIR value; it stands in a repeating primitive only where the _element beside it carries extensions` |
| `fhir.validate.unresolvable_reference` | `{reference} is not a reference a server resolves: Type/id, an absolute URL, a urn:uuid or urn:oid, or #id` |
| `fhir.validate.contained_missing` | `{reference} names a contained resource this resource does not contain` |
| `fhir.validate.duplicate_full_url` | `the Bundle carries {full_url} as the fullUrl of more than one entry` |
| `fhir.validate.profile` | `the resource does not conform to {profile}: it does not claim it under meta.profile, and it is not the base definition of its type` |
| `fhir.cql.refused` | `the CQL library was refused: {detail}` |
| `fhir.cql.definitions_failed` | `{count} definitions failed to evaluate: {detail}` |
| `fhir.cql.output_too_large` | `the evaluation's answer runs past max_output ({limit} bytes); ask for fewer definitions, or raise max_output` |
| `fhir.measure.no_populations` | `the library defines no Initial Population, so there is nothing to count; a measure's library names its populations Initial Population, Denominator, Numerator and their exclusions and exceptions` |
| `fhir.fhirpath.refused` | `the FHIRPath expression does not parse: {detail}` |
| `fhir.fhirpath.unbound_variable` | `the expression reads %{name}, which the step's variables do not bind; give it a value there, or correct the name` |
| `fhir.fhirpath.reserved_variable` | `variables binds %{name}, which FHIRPath defines itself; choose another name` |
| `fhir.fhirpath.output_too_large` | `the expression's answer runs past max_output ({limit} bytes); narrow the expression, or raise max_output` |
| `fhir.fhirpath.filter_answer` | `the expression answered {answer}, and a filter answers true, false or nothing; end it in a boolean, such as exists() or count() > 0` |

A FHIRPath verb's refusal reaches a document at apply as dirigent's `plugin.program_refused`, and a
step as `plugin.transform_failed`, or `plugin.element_failed` naming the element of a map or a
filter, with the sentence above as its `detail`: the frame owns the failure, as it does for
`transform.jq`.
