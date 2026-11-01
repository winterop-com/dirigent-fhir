"""Opt-in checks against a real FHIR server: ``DIRIGENT_LIVE=1``, and ``FHIR_LIVE_URL`` naming its base.

They only read.
"""

import os

import httpx2
import pytest

from dirigent_fhir.connection import FhirConnectionConfig, FhirConnectionKind
from dirigent_fhir.search import FhirSearchOperator, FhirSearchOutput
from dirigent_testing import FakeContext, call_block

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("DIRIGENT_LIVE") != "1" or not os.environ.get("FHIR_LIVE_URL"),
        reason="live checks run with DIRIGENT_LIVE=1 and FHIR_LIVE_URL",
    ),
]


def _config() -> FhirConnectionConfig:
    return FhirConnectionConfig(base_url=os.environ["FHIR_LIVE_URL"])


async def test_the_health_check_reports_the_fhir_version() -> None:
    report = await FhirConnectionKind().check(_config())
    assert report.healthy is True, report.detail
    assert report.version and report.version.startswith("4.")


async def test_a_search_reads_two_pages(block_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config()

    def http(ref: str) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(base_url=config.base_url)

    block_ctx.connections["live"] = config
    monkeypatch.setattr(block_ctx, "http", http)
    out = await call_block(
        FhirSearchOperator(),
        {
            "connection": "live",
            "resource_type": "Patient",
            "params": {"_count": "2"},
            "max_pages": 2,
            "on_more": "stop",
        },
        block_ctx,
    )
    assert isinstance(out, FhirSearchOutput)
    assert out.pages >= 1
