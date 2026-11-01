# Block reference

Every block this pack contributes, with the config it takes and the output it produces. The
tables are the two JSON Schemas each block publishes into the catalog -- the same ones
`GET /blocks` serves, the same ones the UI builds its step form from.

What a block is, and the contract behind these tables, is
[dirigent's own](https://winterop-com.github.io/dirigent/blocks/). Every block here is an
operator, declares the `fhir` group, and takes a `connection` naming a
[`fhir` connection](connection.md) by its code.

**Reads or writes** says what a block does to the server: the two reads change nothing there,
and the three writes do. **Idempotent** says whether re-running the block after an unclear
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

Searches `GET [base]/{resource_type}` and follows `Bundle.link[next]` for at most `max_pages` pages, folding every page into one `searchset` Bundle built through the engine's R4 `Bundle` model. A next link is followed only on the origin of the connection's `base_url`: a link to any other scheme, host or port fails the step before anything is sent there. An entry the server marks as an `outcome` is answered under `issues` rather than inside the Bundle. Every page is held to the connection's `max_response` and `request_deadline` on its own.

**Config**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `connection` | `string` | yes |  | The code of the fhir connection naming the server. |
| `resource_type` | `string` | yes |  | The resource type searched, such as `Patient`. |
| `params` | `object` |  |  | The search parameters as FHIR spells them, such as `{identifier: "http://example.org/mrn\|", _count: "50"}`; a list sends the parameter once per value, which FHIR reads as AND. |
| `max_pages` | `integer` |  | `10` | The most pages followed, the first included. |
| `on_more` | `"fail" or "stop"` |  | `"fail"` | What a next link past `max_pages` does: `fail` fails the step, `stop` answers with the pages read and `complete` false. |

**Output**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `bundle` | `object` | yes |  | A `searchset` Bundle holding every entry of every page read, as plain FHIR JSON. |
| `count` | `integer` | yes |  | How many entries the Bundle holds, included resources counted. |
| `total` | `integer or null` |  | `null` | How many matches the server says there are, when it says. |
| `pages` | `integer` | yes |  | How many pages were read. |
| `complete` | `boolean` | yes |  | Whether the last page read had no next link. |
| `issues` | `object[]` |  |  | What the server said about the search in an `OperationOutcome` entry, such as an unknown parameter. |

### `fhir.create`

Create a FHIR resource, conditionally with ifNoneExist.

Not idempotent: with `if_none_exist` unset every run creates another resource. Writes.

Posts the resource to `[base]/{resourceType}`, without its `id` and its version metadata, asking for the stored resource back. With `if_none_exist` the server is sent `If-None-Exist`, creates the resource only when nothing matches, and answers with the one that does otherwise; `outcome` says which. More than one match fails the step, rejected.

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

A conditional update: `PUT [base]/{resourceType}?identifier=system|value`, on the first of the resource's identifiers with both a system and a value, or the first under `identifier_system`. The server writes over the one resource that matches, creates it when none does, and refuses when several do. The resource's own `id` is never sent, so an id from another server never decides what is overwritten. A resource with no such identifier fails the step before anything is sent.

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
| `fhir.issues_and_more` | `{named} (and {more} more)` |
| `fhir.connection.bad_token` | `a bearer_token must be visible ASCII with no whitespace, or it cannot be sent as a header value` |
| `fhir.connection.both_credentials` | `a connection carries a bearer_token or basic credentials, not both` |
| `fhir.connection.no_basic_password` | `basic_username is set but basic_password is not` |
| `fhir.connection.no_basic_username` | `basic_password is set but basic_username is not` |
| `fhir.connection.bad_base_url` | `base_url must be an absolute http or https URL` |
| `fhir.connection.unreachable` | `the FHIR server did not answer: {detail}` |
| `fhir.connection.not_a_capability_statement` | `{where} answered with {found} rather than a CapabilityStatement, so this is not a FHIR server's base` |
| `fhir.search.more_pages` | `the search has more than max_pages ({max_pages}) pages, and on_more is fail; raise max_pages, narrow the search, or set on_more to stop` |
| `fhir.resource.no_resource_type` | `the resource carries no resourceType` |
| `fhir.resource.no_identifier` | `the {resource} carries no identifier with both a system and a value{system}, so there is nothing to match it on; the source server's id is never used` |
| `fhir.resource.identifier_system` | ` under the system {system}` |
| `fhir.transaction.nothing` | `a transaction step needs resources or a bundle, exactly one of them` |
| `fhir.transaction.unresolved_reference` | `the {resource} refers to {reference}, which the transaction does not carry, so it would point at whatever holds that id on the target; carry it, or set unresolved_references to keep` |
| `fhir.transaction.duplicate` | `the transaction carries {resource} twice, and a reference to it would be ambiguous` |
| `fhir.transaction.no_response` | `{where} answered with {found} entries for the {sent} the transaction sent, so its outcome is unknown` |
| `fhir.transaction.entries_failed` | `{failed} of {total} batch entries failed: {detail}` |
