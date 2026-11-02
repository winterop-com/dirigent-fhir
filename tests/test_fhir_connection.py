"""The ``fhir`` connection: its credential, its health check, and the one origin it sends requests to."""

import base64
from datetime import timedelta
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from dirigent_fhir.connection import FhirConnectionConfig, FhirConnectionKind
from dirigent_fhir.read import FhirReadOperator
from dirigent_plugin import BlockFailure, ErrorClass
from dirigent_testing import FakeContext, call_block
from fhirserver import BASE_URL, CONNECTION, FhirServer, outcome, redirect

TOKEN = "t" * 32


async def test_the_health_check_reads_metadata_and_reports_the_fhir_version(server: FhirServer) -> None:
    report = await FhirConnectionKind(httpx2.MockTransport(server)).check(FhirConnectionConfig(base_url=BASE_URL))
    assert report.healthy is True, report.detail
    assert report.version == "4.0.1"
    assert str(server.requests[0].url) == "http://fhir.test/fhir/metadata"


async def test_a_base_that_answers_no_capability_statement_is_unhealthy(server: FhirServer) -> None:
    server.script(lambda request: httpx2.Response(200, json={"resourceType": "Patient"}))
    report = await FhirConnectionKind(httpx2.MockTransport(server)).check(FhirConnectionConfig(base_url=BASE_URL))
    assert report.healthy is False
    assert report.detail is not None and "rather than a CapabilityStatement" in report.detail


async def test_a_refused_metadata_read_is_unhealthy_in_the_servers_words(server: FhirServer) -> None:
    server.script(lambda request: outcome(401, "login", "a token is required"))
    report = await FhirConnectionKind(httpx2.MockTransport(server)).check(FhirConnectionConfig(base_url=BASE_URL))
    assert report.healthy is False
    assert (
        report.detail == "the FHIR server did not answer: GET metadata answered 401: error login: a token is required"
    )


async def test_an_unreachable_server_is_unhealthy_and_never_raises() -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    report = await FhirConnectionKind(httpx2.MockTransport(down)).check(FhirConnectionConfig(base_url=BASE_URL))
    assert report.healthy is False
    assert report.detail == "the FHIR server did not answer: connection refused"


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, None),
        ({"bearer_token": SecretStr(TOKEN)}, f"Bearer {TOKEN}"),
        (
            {"basic_username": "admin", "basic_password": SecretStr("s3cret")},
            "Basic " + base64.b64encode(b"admin:s3cret").decode(),
        ),
    ],
)
async def test_the_credential_is_sent_as_configured(
    ctx: FakeContext, server: FhirServer, config: dict[str, Any], expected: str | None
) -> None:
    server.seed({"resourceType": "Patient"})
    ctx.connections[CONNECTION] = FhirConnectionConfig(base_url=BASE_URL, **config)
    await call_block(FhirReadOperator(), {"connection": CONNECTION, "resource_type": "Patient", "id": "1"}, ctx)
    assert server.requests[0].headers.get("Authorization") == expected


@pytest.mark.parametrize(
    "elsewhere",
    ["http://attacker.test/fhir/Patient/1", "https://fhir.test/fhir/Patient/1", "http://fhir.test:81/fhir/Patient/1"],
)
async def test_a_redirect_off_the_origin_is_refused_before_the_credential_goes(
    ctx: FakeContext, server: FhirServer, elsewhere: str
) -> None:
    ctx.connections[CONNECTION] = FhirConnectionConfig(base_url=BASE_URL, bearer_token=SecretStr(TOKEN))
    server.script(lambda request: redirect(elsewhere) if request.url.host == "fhir.test" else None)
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirReadOperator(), {"connection": CONNECTION, "resource_type": "Patient", "id": "1"}, ctx)
    assert raised.value.code == "fhir.off_origin"
    assert raised.value.error_class is ErrorClass.REJECTED
    assert [str(request.url) for request in server.requests] == ["http://fhir.test/fhir/Patient/1"]


async def test_a_redirect_on_the_origin_is_followed_with_the_credential(ctx: FakeContext, server: FhirServer) -> None:
    ctx.connections[CONNECTION] = FhirConnectionConfig(base_url=BASE_URL, bearer_token=SecretStr(TOKEN))
    server.seed({"resourceType": "Patient"})
    server.script(lambda request: redirect("/fhir/Patient/1") if request.url.path == "/fhir/Patient/old" else None)
    out = await call_block(FhirReadOperator(), {"connection": CONNECTION, "resource_type": "Patient", "id": "old"}, ctx)
    assert out.model_dump()["resource"]["id"] == "1"
    assert [request.headers["Authorization"] for request in server.requests] == [f"Bearer {TOKEN}"] * 2


async def test_the_default_port_is_the_same_origin(ctx: FakeContext, server: FhirServer) -> None:
    server.seed({"resourceType": "Patient"})
    server.script(
        lambda request: redirect("http://fhir.test:80/fhir/Patient/1") if request.url.path.endswith("old") else None
    )
    await call_block(FhirReadOperator(), {"connection": CONNECTION, "resource_type": "Patient", "id": "old"}, ctx)
    assert len(server.requests) == 2


@pytest.mark.parametrize(
    "config",
    [
        {"bearer_token": SecretStr(TOKEN), "basic_username": "a", "basic_password": SecretStr("b")},
        {"basic_username": "a"},
        {"basic_password": SecretStr("b")},
        {"bearer_token": SecretStr("has space")},
    ],
)
def test_a_credential_that_cannot_be_sent_is_refused(config: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as raised:
        FhirConnectionConfig(base_url=BASE_URL, **config)
    assert "has space" not in str(raised.value)


@pytest.mark.parametrize("base_url", ["fhir.test/fhir", "ftp://fhir.test", "/fhir"])
def test_a_base_url_must_be_absolute_http(base_url: str) -> None:
    with pytest.raises(ValidationError):
        FhirConnectionConfig(base_url=base_url)


def test_the_limits_are_humane() -> None:
    config = FhirConnectionConfig.model_validate(
        {"base_url": BASE_URL, "max_response": "16KB", "request_deadline": "90s", "timeout": "5s"}
    )
    assert config.max_response == 16 * 1024
    assert config.request_deadline == timedelta(seconds=90)
    assert config.timeout == timedelta(seconds=5)


def test_an_empty_token_is_no_token() -> None:
    assert FhirConnectionConfig(base_url=BASE_URL, bearer_token=SecretStr("")).bearer_token is None


#: A basic pair, and the bearer token beside it, as the connection sends them.
BASIC = {"basic_username": "dummyuser", "basic_password": SecretStr("dummy-basic-pw")}
BASIC_TOKEN = base64.b64encode(b"dummyuser:dummy-basic-pw").decode()
CREDENTIALS = [
    pytest.param(BASIC, [f"Basic {BASIC_TOKEN}", BASIC_TOKEN, "dummyuser:dummy-basic-pw"], id="basic"),
    pytest.param({"bearer_token": SecretStr(TOKEN)}, [f"Bearer {TOKEN}", TOKEN], id="bearer"),
]


def _quoting(as_outcome: bool, status: int = 401) -> Any:
    """A server that quotes the Authorization header it was sent, in an outcome or as plain text."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        said = f"refused Authorization: {request.headers.get('Authorization')}"
        if as_outcome:
            return outcome(status, "login", said)
        return httpx2.Response(status, text=said, headers={"Location": said})

    return answer


@pytest.mark.parametrize(("credential", "spelled"), CREDENTIALS)
@pytest.mark.parametrize("as_outcome", [True, False], ids=["outcome", "text"])
async def test_a_server_quoting_the_credential_never_puts_it_in_the_health_detail(
    server: FhirServer, credential: dict[str, Any], spelled: list[str], as_outcome: bool
) -> None:
    server.script(_quoting(as_outcome))
    config = FhirConnectionConfig(base_url=BASE_URL, **credential)
    report = await FhirConnectionKind(httpx2.MockTransport(server)).check(config)
    assert report.healthy is False
    assert report.detail is not None and "refused Authorization: ***" in report.detail
    for one in spelled:
        assert one not in report.detail


@pytest.mark.parametrize(("credential", "spelled"), CREDENTIALS)
@pytest.mark.parametrize("as_outcome", [True, False], ids=["outcome", "text"])
async def test_a_server_quoting_the_credential_never_puts_it_in_a_refusal(
    ctx: FakeContext, server: FhirServer, credential: dict[str, Any], spelled: list[str], as_outcome: bool
) -> None:
    ctx.connections[CONNECTION] = FhirConnectionConfig(base_url=BASE_URL, **credential)
    server.script(_quoting(as_outcome))
    with pytest.raises(BlockFailure) as refused:
        await call_block(FhirReadOperator(), {"connection": CONNECTION, "resource_type": "Patient", "id": "1"}, ctx)
    text = f"{refused.value.message} {refused.value.params}"
    assert "refused Authorization: ***" in text
    for one in spelled:
        assert one not in text


@pytest.mark.parametrize(("credential", "spelled"), CREDENTIALS)
async def test_a_server_quoting_the_credential_never_puts_it_in_an_output(
    ctx: FakeContext, server: FhirServer, credential: dict[str, Any], spelled: list[str]
) -> None:
    ctx.connections[CONNECTION] = FhirConnectionConfig(base_url=BASE_URL, **credential)
    server.script(
        lambda request: httpx2.Response(
            200, json={"resourceType": "Patient", "id": "1", "text": {"div": request.headers["Authorization"]}}
        )
    )
    out = await call_block(FhirReadOperator(), {"connection": CONNECTION, "resource_type": "Patient", "id": "1"}, ctx)
    assert out.model_dump()["resource"]["text"] == {"div": "***"}


@pytest.mark.parametrize(
    "url",
    [
        "https://user:DUMMYSECRET@fhir.test/fhir",
        "https://DUMMYSECRET@fhir.test/fhir",
        "https://fhir.test/fhir?api_key=DUMMYSECRET",
        "https://fhir.test/fhir#DUMMYSECRET",
    ],
)
def test_a_base_url_holding_more_than_where_is_refused_without_repeating_it(url: str) -> None:
    with pytest.raises(ValidationError) as refused:
        FhirConnectionConfig(base_url=url)
    assert "base_url says where the server is" in str(refused.value)
    assert "DUMMYSECRET" not in str(refused.value)
