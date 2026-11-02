# The `fhir` connection

A `fhir` connection is one FHIR server: the base every interaction is resolved against, the
credential sent with it, and what one answer may cost. Every block in the pack names one by its
code under `connection`.

```bash
dg connection create fhir fhir-source --set base_url=http://localhost:8090/fhir
```

or in a document:

```yaml
connections:
  fhir-source:
    kind: fhir
    config:
      base_url: http://localhost:8090/fhir
      timeout: 30s
      request_deadline: 10m
      max_response: 64MB
```

## Settings

| Field | Default | What it is |
| --- | --- | --- |
| `base_url` | required | The server's base, such as `http://localhost:8090/fhir`. An absolute `http` or `https` URL. |
| `bearer_token` | none | A token sent as `Authorization: Bearer`: visible ASCII with no whitespace. |
| `basic_username` | none | The user half of HTTP basic authentication. |
| `basic_password` | none | The secret half of HTTP basic authentication. |
| `verify_tls` | `true` | Whether certificates are verified. |
| `timeout` | `30s` | The timeout applied to each read of every request. |
| `request_deadline` | `10m` | The longest one request may take, start to last byte. |
| `max_response` | `64MB` | The most one response may decode to; a larger one is refused. |

A connection carries no credential, a `bearer_token`, or both halves of the basic pair, and
validation refuses anything else: both kinds at once, or half a pair. A refusal never repeats
what the token held.

A server or proxy that quotes the `Authorization` header it was sent never puts the credential
in a refusal, a health check's detail, a log line or a step's output: the header value, the
credential after its scheme, and a basic pair decoded are each replaced by `***` wherever the
answer says them.

## One origin

The credential goes only to the origin of `base_url`: its scheme, host and port. Every request
through the connection is checked against that origin before it is sent, so:

- a redirect is followed only on the origin, with the credential, and a redirect anywhere else
  fails the step, `fhir.off_origin`, before the other host is asked anything;
- a search's `Bundle.link[next]` is followed only on the origin. A server that answers with a
  next link elsewhere fails the step rather than leading the connection to it.

`http://fhir.test` and `http://fhir.test:80` are the same origin; `https://fhir.test`,
`http://fhir.test:8080` and `http://api.fhir.test` are not.

## What one answer may cost

Every response is read as it decodes and refused past `max_response`, so a small compressed
answer that decodes to gigabytes costs no more than the limit. Every request is cut at
`request_deadline`, however steadily the server keeps sending. A search holds each page to both
on its own, and `max_pages` bounds how many pages there are. An error answer is read only far
enough to quote it.

## The health check

The check reads `GET [base]/metadata`, the server's CapabilityStatement, and reports the
`fhirVersion` it states, such as `4.0.1`. An answer that is not a CapabilityStatement is
reported unhealthy, because the base is not a FHIR server's. Most servers serve `/metadata`
without authentication, so a healthy check says the server is there and speaks FHIR; the first
block that reads proves the credential.
