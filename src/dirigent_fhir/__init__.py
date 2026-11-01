"""The FHIR pack: the ``fhir`` connection kind and the REST blocks for one FHIR server."""

from collections.abc import Sequence
from importlib.resources import files
from importlib.resources.abc import Traversable

from dirigent_fhir.client import FhirClient
from dirigent_fhir.connection import FhirConnectionConfig, FhirConnectionKind, build_client, client_for
from dirigent_fhir.create import FhirCreateConfig, FhirCreateOperator
from dirigent_fhir.fhirpath import FhirPathConfig, FhirPathFilterer, FhirPathMapper, FhirPathTransformer
from dirigent_fhir.messages import FHIR
from dirigent_fhir.r4 import FhirIssue
from dirigent_fhir.read import FhirReadConfig, FhirReadOperator, FhirReadOutput
from dirigent_fhir.search import FhirSearchConfig, FhirSearchOperator, FhirSearchOutput
from dirigent_fhir.transaction import (
    FhirEntryResult,
    FhirTransactionConfig,
    FhirTransactionOperator,
    FhirTransactionOutput,
)
from dirigent_fhir.update import FhirUpdateConfig, FhirUpdateOperator
from dirigent_fhir.writes import FhirWriteOutput
from dirigent_plugin import Contribution, extension

#: The directory the pack's example shelves live in, inside this distribution.
SHELVES_DIRECTORY = "shelves"


class FhirPlugin:
    """The plugin object the host discovers under the dirigent.plugins.v1 entry-point group."""

    @extension
    def contribute(self) -> Contribution:
        """Contribute the FHIR blocks, the FHIRPath verbs, and the ``fhir`` connection kind."""
        return Contribution(
            operators=[
                FhirReadOperator(),
                FhirSearchOperator(),
                FhirCreateOperator(),
                FhirUpdateOperator(),
                FhirTransactionOperator(),
                FhirPathTransformer(),
                FhirPathMapper(),
                FhirPathFilterer(),
            ],
            connection_kinds=[FhirConnectionKind()],
            labels=[FHIR],
        )

    @extension
    def examples(self) -> Sequence[Traversable]:
        """Contribute the example shelves this distribution carries."""
        return [files(__package__ or "dirigent_fhir") / SHELVES_DIRECTORY]


plugin = FhirPlugin()

__all__ = [
    "SHELVES_DIRECTORY",
    "FhirClient",
    "FhirConnectionConfig",
    "FhirConnectionKind",
    "FhirCreateConfig",
    "FhirCreateOperator",
    "FhirEntryResult",
    "FhirIssue",
    "FhirPathConfig",
    "FhirPathFilterer",
    "FhirPathMapper",
    "FhirPathTransformer",
    "FhirPlugin",
    "FhirReadConfig",
    "FhirReadOperator",
    "FhirReadOutput",
    "FhirSearchConfig",
    "FhirSearchOperator",
    "FhirSearchOutput",
    "FhirTransactionConfig",
    "FhirTransactionOperator",
    "FhirTransactionOutput",
    "FhirUpdateConfig",
    "FhirUpdateOperator",
    "FhirWriteOutput",
    "build_client",
    "client_for",
    "plugin",
]
