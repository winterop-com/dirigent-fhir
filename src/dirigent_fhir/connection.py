"""The ``fhir`` connection kind: one FHIR server's base, its credential, and the client for it."""

import base64
import re
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import ClassVar, Final

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from dirigent_common import BlockModel, Duration, HealthReport, Size
from dirigent_fhir.client import DEFAULT_PORTS, FHIR_JSON, FhirClient, Origin, ensure_on_origin, origin_of
from dirigent_fhir.limits import BoundedClient, Limits
from dirigent_fhir.messages import (
    BAD_BASE_URL,
    BAD_TOKEN,
    BOTH_CREDENTIALS,
    CONNECTION_UNREACHABLE,
    NO_BASIC_PASSWORD,
    NO_BASIC_USERNAME,
    PAST_DEADLINE,
    RESPONSE_TOO_LARGE,
    TOO_MANY_REDIRECTS,
)
from dirigent_plugin import BlockFailure, ConnectionKind, ConnectionRef, StepContext

#: What a bearer token may hold: visible ASCII, the only text a header value carries unchanged.
TOKEN: Final = re.compile(r"[\x21-\x7e]+")

#: The header a credential travels in.
AUTHORIZATION: Final = "Authorization"


class FhirConnectionConfig(BlockModel):
    """Everything needed to talk to one FHIR server.

    With neither a `bearer_token` nor basic credentials the server is called without any.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    base_url: str = Field(min_length=1)
    """The server's base, which every interaction is resolved against, such as `http://localhost:8080/fhir`."""

    bearer_token: SecretStr | None = None
    """A token sent as `Authorization: Bearer`: visible ASCII with no whitespace."""

    basic_username: str | None = None
    """The user half of HTTP basic authentication."""

    basic_password: SecretStr | None = None
    """The secret half of HTTP basic authentication."""

    verify_tls: bool = True
    """Whether certificates are verified; turning this off is a per-connection decision."""

    timeout: Duration = Field(default=timedelta(seconds=30), gt=timedelta(0))
    """The timeout applied to each read of every request through this connection."""

    request_deadline: Duration = Field(default=timedelta(minutes=10), gt=timedelta(0))
    """The longest one request through this connection may take, start to last byte."""

    max_response: Size = Field(default=64 * 1024 * 1024, ge=1)
    """The most one response through this connection may decode to; a larger one is refused."""

    @field_validator("base_url")
    @classmethod
    def _absolute(cls, url: str) -> str:
        """Refuse a base that names no http or https origin."""
        parsed = httpx2.URL(url)
        if parsed.scheme not in DEFAULT_PORTS or not parsed.host:
            raise ValueError(BAD_BASE_URL.render())
        return url

    @field_validator("bearer_token")
    @classmethod
    def _token_fits_a_header(cls, token: SecretStr | None) -> SecretStr | None:
        """Read an empty token as unset, and reject one that cannot be a header value without saying what it held."""
        if token is None or not token.get_secret_value():
            return None
        if TOKEN.fullmatch(token.get_secret_value()) is None:
            raise ValueError(BAD_TOKEN.render())
        return token

    @model_validator(mode="after")
    def _one_credential(self) -> "FhirConnectionConfig":
        """Reject both credential kinds at once, and half of the basic pair."""
        if self.bearer_token is not None and (self.basic_username or self.basic_password is not None):
            raise ValueError(BOTH_CREDENTIALS.render())
        if self.basic_username and self.basic_password is None:
            raise ValueError(NO_BASIC_PASSWORD.render())
        if self.basic_password is not None and not self.basic_username:
            raise ValueError(NO_BASIC_USERNAME.render())
        return self


def stays_on_origin(origin: Origin) -> Callable[[httpx2.Request], Awaitable[None]]:
    """Build the request hook that refuses every request, a redirect's included, off the origin."""

    async def refuse_elsewhere(request: httpx2.Request) -> None:
        ensure_on_origin(request.url, origin)

    return refuse_elsewhere


def settings_of(config: BaseModel) -> FhirConnectionConfig:
    """Read a connection's config as this kind's own model, whichever model the caller held."""
    if isinstance(config, FhirConnectionConfig):
        return config
    return FhirConnectionConfig.model_validate(config.model_dump())


def credential(config: FhirConnectionConfig) -> str | None:
    """The ``Authorization`` value the connection sends, or None for a server without auth."""
    if config.bearer_token is not None:
        return f"Bearer {config.bearer_token.get_secret_value()}"
    if config.basic_username and config.basic_password is not None:
        pair = f"{config.basic_username}:{config.basic_password.get_secret_value()}".encode()
        return f"Basic {base64.b64encode(pair).decode()}"
    return None


def prepare(client: httpx2.AsyncClient, config: FhirConnectionConfig) -> BoundedClient:
    """Bind a client to the connection: its base, its credential, FHIR JSON, redirects on its origin only, its limits.

    Redirects are followed by the bounded client, hop by hop, never by the client itself.

    Whatever credential the client was built with is taken off first, so the only one it sends
    is the connection's, and only to the origin of ``base_url``.
    """
    client.base_url = httpx2.URL(config.base_url)
    client.auth = httpx2.Auth()
    client.headers.pop(AUTHORIZATION, None)
    value = credential(config)
    if value is not None:
        client.headers[AUTHORIZATION] = value
    client.headers["Accept"] = FHIR_JSON
    client.follow_redirects = False
    client.event_hooks = {"request": [stays_on_origin(origin_of(config.base_url))], "response": []}
    return BoundedClient(
        client,
        Limits(
            max_response=config.max_response,
            request_deadline=config.request_deadline.total_seconds(),
            too_large=RESPONSE_TOO_LARGE,
            past_deadline=PAST_DEADLINE,
            too_many_redirects=TOO_MANY_REDIRECTS,
        ),
    )


def build_client(config: FhirConnectionConfig, transport: httpx2.AsyncBaseTransport | None = None) -> BoundedClient:
    """Build a bound client for the connection, through the given transport or the network."""
    client = httpx2.AsyncClient(
        base_url=config.base_url,
        verify=config.verify_tls,
        timeout=config.timeout.total_seconds(),
        transport=transport,
    )
    return prepare(client, config)


def client_for(ctx: StepContext, ref: ConnectionRef) -> FhirClient:
    """The FHIR client one block call uses, bound to the named connection."""
    config = ctx.connection(ref, FhirConnectionConfig)
    return FhirClient(prepare(ctx.http(ref), config), origin_of(config.base_url))


#: A flame inside a ring, on the 24-unit grid every kind's mark is drawn on.
FHIR_MARK: Final = (
    "M2 12a10 10 0 1 0 20 0a10 10 0 1 0-20 0Z"
    "M4 12a8 8 0 1 1 16 0a8 8 0 1 1-16 0Z"
    "M12 5.5c2.6 2.4 4 4.6 4 6.9a4 4 0 0 1-8 0c0-1.3.5-2.5 1.4-3.6c.2 1.2.8 2 1.6 2.4c-.2-2.1.2-3.9 1-5.7Z"
)


class FhirConnectionKind(ConnectionKind):
    """The connection kind every block in this pack resolves its server and credential through."""

    id: ClassVar[str] = "fhir"
    config_model: ClassVar[type[BaseModel]] = FhirConnectionConfig
    mark: ClassVar[str | None] = FHIR_MARK

    def __init__(self, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        """Check through the given transport, or through the network when none is given."""
        self._transport = transport

    async def check(self, config: BaseModel) -> HealthReport:
        """Read the server's CapabilityStatement from ``/metadata`` and report its FHIR version, never raising."""
        try:
            settings = settings_of(config)
            async with FhirClient(build_client(settings, self._transport), origin_of(settings.base_url)) as fhir:
                statement = await fhir.capabilities()
        except BlockFailure as failure:
            return HealthReport(healthy=False, detail=_unreachable(failure.message))
        except Exception as error:  # noqa: BLE001 - a health check reports its verdict, it never raises
            return HealthReport(healthy=False, detail=_unreachable(str(error) or type(error).__name__))
        return HealthReport(healthy=True, version=statement.fhir_version)


def _unreachable(detail: str) -> str:
    """Say the server did not answer as a FHIR server, and why."""
    return CONNECTION_UNREACHABLE.render(detail=detail)
