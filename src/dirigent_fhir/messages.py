"""Every refusal the FHIR pack makes, catalogued under the ``fhir`` prefix."""

from dirigent_common import Catalogue

FHIR = Catalogue("fhir")

ANSWERED = FHIR.define("answered", "{where} answered {status}: {remote}")

NOT_JSON = FHIR.define("not_json", "{where} answered with a body that is not FHIR JSON")

NOT_A_RESOURCE = FHIR.define("not_a_resource", "{where} answered with {found} where a {expected} resource was expected")

RESPONSE_TOO_LARGE = FHIR.define(
    "response_too_large",
    "{where} answered with more than max_response ({limit} bytes) and the rest was not read; "
    "raise max_response if the answer is expected",
)

PAST_DEADLINE = FHIR.define("past_deadline", "{where} took longer than the {seconds} seconds it is allowed")

OFF_ORIGIN = FHIR.define(
    "off_origin",
    "a request to {target} was refused, because only {origin}, the origin of base_url, is sent requests "
    "through this connection",
)

TOO_MANY_REDIRECTS = FHIR.define(
    "too_many_redirects", "{where} was redirected more than {limit} times, and the redirects were not followed further"
)

ISSUES_AND_MORE = FHIR.define("issues_and_more", "{named} (and {more} more)")

BAD_TOKEN = FHIR.define(
    "connection.bad_token",
    "a bearer_token must be visible ASCII with no whitespace, or it cannot be sent as a header value",
)

BOTH_CREDENTIALS = FHIR.define(
    "connection.both_credentials", "a connection carries a bearer_token or basic credentials, not both"
)

NO_BASIC_PASSWORD = FHIR.define("connection.no_basic_password", "basic_username is set but basic_password is not")

NO_BASIC_USERNAME = FHIR.define("connection.no_basic_username", "basic_password is set but basic_username is not")

BAD_BASE_URL = FHIR.define("connection.bad_base_url", "base_url must be an absolute http or https URL")

BASE_URL_HOLDS_MORE = FHIR.define(
    "connection.base_url_holds_more",
    "base_url says where the server is and nothing else: a user, a password, a query or a fragment in it is refused, "
    "because it is stored as written; a credential goes in bearer_token or basic_password",
)

SEARCH_CREDENTIAL_REFUSED = FHIR.define(
    "search.credential_refused",
    "the {parameter} search parameter carries a credential, and a document is stored as written; put it on the fhir "
    "connection, in bearer_token or basic_password",
)

CONNECTION_UNREACHABLE = FHIR.define("connection.unreachable", "the FHIR server did not answer: {detail}")

NOT_A_CAPABILITY_STATEMENT = FHIR.define(
    "connection.not_a_capability_statement",
    "{where} answered with {found} rather than a CapabilityStatement, so this is not a FHIR server's base",
)

SEARCH_MORE_PAGES = FHIR.define(
    "search.more_pages",
    "the search has more than max_pages ({max_pages}) pages, and on_more is fail; "
    "raise max_pages, narrow the search, or set on_more to stop",
)

SEARCH_TOO_LARGE = FHIR.define(
    "search.too_large",
    "{where} took the search's pages past max_total ({limit} bytes) together, and the rest was not read; "
    "raise max_total, narrow the search, or lower max_pages",
)

NO_RESOURCE_TYPE = FHIR.define("resource.no_resource_type", "the resource carries no resourceType")

BAD_RESOURCE_TYPE = FHIR.define(
    "resource.bad_resource_type",
    "the resource's resourceType is not a FHIR resource type, an upper-case letter and then letters such as "
    "Patient, so it is not written",
)

NO_IDENTIFIER = FHIR.define(
    "resource.no_identifier",
    "the {resource} carries no identifier with both a system and a value{system}, so there is nothing to "
    "match it on; the source server's id is never used",
)

IDENTIFIER_SYSTEM = FHIR.define("resource.identifier_system", " under the system {system}")

TRANSACTION_NOTHING = FHIR.define(
    "transaction.nothing", "a transaction step needs resources or a bundle, exactly one of them"
)

TRANSACTION_UNRESOLVED = FHIR.define(
    "transaction.unresolved_reference",
    "the {resource} refers to {reference}, which the transaction does not carry, so it would point at whatever "
    "holds that id on the target; carry it, or set unresolved_references to keep",
)

TRANSACTION_DUPLICATE = FHIR.define(
    "transaction.duplicate", "the transaction carries {resource} twice, and a reference to it would be ambiguous"
)

TRANSACTION_NO_RESPONSE = FHIR.define(
    "transaction.no_response",
    "{where} answered with {found} entries for the {sent} the transaction sent, so its outcome is unknown",
)

BATCH_ENTRIES_FAILED = FHIR.define("transaction.entries_failed", "{failed} of {total} batch entries failed: {detail}")

FHIRPATH_REFUSED = FHIR.define("fhirpath.refused", "the FHIRPath expression does not parse: {detail}")

FHIRPATH_UNBOUND_VARIABLE = FHIR.define(
    "fhirpath.unbound_variable",
    "the expression reads %{name}, which the step's variables do not bind; give it a value there, or correct the name",
)

FHIRPATH_RESERVED_VARIABLE = FHIR.define(
    "fhirpath.reserved_variable", "variables binds %{name}, which FHIRPath defines itself; choose another name"
)

FHIRPATH_OUTPUT_TOO_LARGE = FHIR.define(
    "fhirpath.output_too_large",
    "the expression's answer runs past max_output ({limit} bytes); narrow the expression, or raise max_output",
)

FHIRPATH_FILTER_ANSWER = FHIR.define(
    "fhirpath.filter_answer",
    "the expression answered {answer}, and a filter answers true, false or nothing; "
    "end it in a boolean, such as exists() or count() > 0",
)

VALIDATE_INVALID = FHIR.define(
    "validate.invalid", "{count} issues were found in the {checked} resources checked: {detail}"
)

VALIDATE_NOT_A_RESOURCE = FHIR.define(
    "validate.not_a_resource", "this is not a FHIR resource: an object with a resourceType such as Patient"
)

VALIDATE_RESOURCE_ID = FHIR.define("validate.resource_id", "an id is 1 to 64 letters, digits, hyphens and dots")

VALIDATE_MODEL = FHIR.define("validate.model", "the engine's R4 model refuses it: {detail}")

VALIDATE_EMPTY = FHIR.define(
    "validate.empty", "an element is left out rather than written empty, which FHIR JSON does not allow"
)

VALIDATE_NULL = FHIR.define(
    "validate.null",
    "null is not a FHIR value; it stands in a repeating primitive only where the _element beside it carries extensions",
)

VALIDATE_UNRESOLVABLE_REFERENCE = FHIR.define(
    "validate.unresolvable_reference",
    "{reference} is not a reference a server resolves: Type/id, an absolute URL, a urn:uuid or urn:oid, or #id",
)

VALIDATE_CONTAINED_MISSING = FHIR.define(
    "validate.contained_missing", "{reference} names a contained resource this resource does not contain"
)

VALIDATE_DUPLICATE_FULL_URL = FHIR.define(
    "validate.duplicate_full_url", "the Bundle carries {full_url} as the fullUrl of more than one entry"
)

VALIDATE_PROFILE = FHIR.define(
    "validate.profile",
    "the resource does not conform to {profile}: it does not claim it under meta.profile, and it is not the "
    "base definition of its type",
)

CQL_REFUSED = FHIR.define("cql.refused", "the CQL library was refused: {detail}")

CQL_DEFINITIONS_FAILED = FHIR.define("cql.definitions_failed", "{count} definitions failed to evaluate: {detail}")

CQL_OUTPUT_TOO_LARGE = FHIR.define(
    "cql.output_too_large",
    "the evaluation's answer runs past max_output ({limit} bytes); ask for fewer definitions, or raise max_output",
)

MEASURE_NO_POPULATIONS = FHIR.define(
    "measure.no_populations",
    "the library defines no Initial Population, so there is nothing to count; a measure's library names its "
    "populations Initial Population, Denominator, Numerator and their exclusions and exceptions",
)
