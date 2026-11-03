"""The pack's wiring: its blocks, its connection kind, and what each block says it does."""

from importlib.metadata import distribution
from pathlib import Path
from typing import Any, cast

from pluginkit import PluginManager

import dirigent_fhir
from dirigent_common import API_VERSION, HumaneJsonSchema
from dirigent_fhir import FhirPlugin, plugin
from dirigent_plugin import ENTRY_POINT_GROUP, PROJECT_NAME, contribute, markers
from dirigent_testing import assert_contribution_conforms, check_pack_messages

FHIR_BLOCKS = [
    "fhir.cql",
    "fhir.create",
    "fhir.delete",
    "fhir.measure",
    "fhir.operation",
    "fhir.patch",
    "fhir.read",
    "fhir.search",
    "fhir.transaction",
    "fhir.update",
    "fhir.validate",
]

#: The fhir blocks that compute on what they are handed and reach no server.
OFFLINE = {"fhir.cql", "fhir.measure", "fhir.validate"}

#: The FHIRPath engines on dirigent's verb frames, which compute and reach no server.
FHIRPATH_VERBS = ["filter.fhirpath", "map.fhirpath", "transform.fhirpath"]

BLOCKS = sorted(FHIR_BLOCKS + FHIRPATH_VERBS)

#: The blocks that only read, and are safe to run again.
READS = {"fhir.read", "fhir.search"}

#: The writes a repeat leaves as the first run left them: each is matched on an id or an identifier.
IDEMPOTENT_WRITES = {"fhir.update", "fhir.transaction", "fhir.delete"}

#: The blocks whose effect is the operation's they invoke, which may read or write.
EITHER = {"fhir.operation"}

CONTRIBUTION = FhirPlugin().contribute()

#: The blocks that talk to a FHIR server through a connection.
REST = [operator for operator in CONTRIBUTION.operators if operator.spec.id in set(FHIR_BLOCKS) - OFFLINE]


def test_the_pack_contributes_the_fhir_blocks_and_connection_kind() -> None:
    assert CONTRIBUTION.api_version == API_VERSION
    assert sorted(CONTRIBUTION.block_ids()) == BLOCKS
    assert [connection.id for connection in CONTRIBUTION.connection_kinds] == ["fhir"]
    assert CONTRIBUTION.sensors == []
    assert CONTRIBUTION.storage_backends == []


def test_every_block_shelves_under_its_group_and_runs_no_code() -> None:
    for operator in CONTRIBUTION.operators:
        assert operator.spec.group == ("fhir" if operator.spec.id in FHIR_BLOCKS else "transform")
        assert operator.spec.local_execution is False


def test_the_blocks_that_reach_no_server_take_no_connection_and_are_idempotent() -> None:
    for operator in CONTRIBUTION.operators:
        if operator.spec.id in OFFLINE or operator.spec.id in FHIRPATH_VERBS:
            assert "connection" not in operator.config_model.model_fields
            assert operator.spec.idempotent is True
            if operator.spec.id in OFFLINE:
                assert (operator.__class__.__doc__ or "").endswith("It touches no server."), operator.spec.id


def test_reads_say_they_read_and_writes_say_they_write() -> None:
    for operator in REST:
        doc = operator.__class__.__doc__ or ""
        if operator.spec.id in READS:
            assert doc.endswith("It only reads."), operator.spec.id
            assert operator.spec.idempotent is True
        elif operator.spec.id in EITHER:
            assert doc.endswith("It reads or writes, as the operation does."), operator.spec.id
            assert operator.spec.idempotent is False
        else:
            assert doc.endswith("It writes."), operator.spec.id
            assert operator.spec.idempotent is (operator.spec.id in IDEMPOTENT_WRITES)


def test_every_rest_block_marks_its_connection_field_as_a_reference() -> None:
    for block in REST:
        schema = block.config_model.model_json_schema(mode="serialization", schema_generator=HumaneJsonSchema)
        field = cast("dict[str, Any]", schema["properties"]["connection"])
        ref = cast("str", field["$ref"]).removeprefix("#/$defs/")
        assert schema["$defs"][ref]["x-dirigent-ref"] == "connection", block.spec.id


def test_the_contribution_conforms() -> None:
    assert assert_contribution_conforms(CONTRIBUTION) == []


def test_every_refusal_the_pack_makes_carries_a_code() -> None:
    assert check_pack_messages(Path(dirigent_fhir.__file__).parent) == []


def test_the_pack_registers_through_a_plugin_manager() -> None:
    manager = PluginManager(PROJECT_NAME)
    manager.add_extension_points(markers)
    manager.register(plugin, name="fhir")
    assert [sorted(found.block_ids()) for found in manager.caller(contribute)()] == [BLOCKS]


def test_the_pack_is_discovered_through_the_entry_point_group() -> None:
    entry_points = distribution("dirigent-fhir").entry_points.select(group=ENTRY_POINT_GROUP)
    assert {entry_point.name: entry_point.value for entry_point in entry_points} == {"fhir": "dirigent_fhir:plugin"}
    manager = PluginManager(PROJECT_NAME)
    manager.add_extension_points(markers)
    manager.load_entrypoints(ENTRY_POINT_GROUP)
    assert manager.get_plugin("fhir") is not None


def test_the_pack_carries_no_dhis2_code_of_its_own() -> None:
    source = Path(dirigent_fhir.__file__).parent
    for path in source.rglob("*.py"):
        text = path.read_text()
        assert "dhis2w_client" not in text and "dirigent_dhis2" not in text, path.name
