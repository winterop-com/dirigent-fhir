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

NO_RESOURCE_TYPE = FHIR.define("resource.no_resource_type", "the resource carries no resourceType")

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
