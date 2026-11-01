"""Installs the fhir connection and answers every block from a fake FHIR server."""

import pytest

from dirigent_fhir.connection import FhirConnectionConfig
from dirigent_testing import FakeContext
from fhirserver import BASE_URL, CONNECTION, FhirServer


@pytest.fixture
def server() -> FhirServer:
    """An empty fake FHIR server."""
    return FhirServer()


@pytest.fixture
def ctx(block_ctx: FakeContext, server: FhirServer) -> FakeContext:
    """A context whose fhir connection points at the fake server, and whose HTTP is that server."""
    block_ctx.connections[CONNECTION] = FhirConnectionConfig(base_url=BASE_URL)
    block_ctx.handler = server
    return block_ctx


@pytest.fixture(autouse=True)
def _no_connection_outlives_its_loop(no_connection_outlives_its_loop: None) -> None:
    """Hold every test in this pack to the packaged connection guard."""
