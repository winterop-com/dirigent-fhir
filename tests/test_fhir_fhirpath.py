"""The FHIRPath verbs: ``transform.fhirpath``, ``map.fhirpath`` and ``filter.fhirpath`` on the engine's evaluator."""

import asyncio
import contextlib
import time
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import BaseModel

from dirigent_fhir import FhirPathConfig, FhirPathFilterer, FhirPathMapper, FhirPathTransformer
from dirigent_fhir.fhirpath import FHIRPATH_MEDIA_TYPE
from dirigent_plugin import BlockFailure, ErrorClass, Filterer, Mapper, Transformer, runners
from dirigent_plugin.runners import ProgramRunner
from dirigent_testing import FakeContext, call_block

KARI: dict[str, Any] = {
    "resourceType": "Patient",
    "id": "kari",
    "active": True,
    "birthDate": "1980-04-01",
    "identifier": [{"system": "http://example.org/mrn", "value": "12345"}],
    "name": [
        {"use": "official", "family": "Nordmann", "given": ["Kari", "Anne"]},
        {"use": "nickname", "given": ["KN"]},
    ],
}

OLA: dict[str, Any] = {
    "resourceType": "Patient",
    "id": "ola",
    "active": False,
    "birthDate": "2001-12-24",
    "name": [{"use": "official", "family": "Hansen", "given": ["Ola"]}],
}

#: A quadratic walk over a list the step binds, which keeps the evaluator busy for seconds.
SLOW = "%n.select(%n.where($this > 0).count()).count()"


def _value(output: BaseModel) -> Any:
    return output.model_dump()["value"]


async def test_one_item_is_the_value_itself(ctx: FakeContext) -> None:
    program = "name.where(use = 'official').family.first()"
    output = await call_block(FhirPathTransformer(), {"input": KARI, "program": program}, ctx)
    assert _value(output) == "Nordmann"


async def test_several_items_are_their_list_and_none_is_null(ctx: FakeContext) -> None:
    several = await call_block(FhirPathTransformer(), {"input": KARI, "program": "name.given"}, ctx)
    none = await call_block(FhirPathTransformer(), {"input": KARI, "program": "deceased"}, ctx)
    assert _value(several) == ["Kari", "Anne", "KN"]
    assert _value(none) is None


async def test_answers_are_plain_json(ctx: FakeContext) -> None:
    program = "birthDate | (1 + 1) | 1.5 | @2020-01-01T10:00:00Z | 5 'mg' | name.first()"
    output = await call_block(FhirPathTransformer(), {"input": KARI, "program": program}, ctx)
    assert _value(output) == [
        "1980-04-01",
        2,
        1.5,
        "2020-01-01T10:00:00Z",
        {"value": 5, "unit": "mg"},
        KARI["name"][0],
    ]


async def test_a_resource_is_reshaped_into_another(ctx: FakeContext) -> None:
    program = "name.where(use = 'official').select(family & ', ' & given.first())"
    output = await call_block(FhirPathTransformer(), {"input": KARI, "program": program}, ctx)
    assert _value(output) == "Nordmann, Kari"


async def test_a_variable_is_read_as_a_value_and_never_as_expression_text(ctx: FakeContext) -> None:
    program = "name.where(family = %family).given.first()"
    found = await call_block(
        FhirPathTransformer(), {"input": KARI, "program": program, "variables": {"family": "Nordmann"}}, ctx
    )
    hostile = await call_block(
        FhirPathTransformer(),
        {"input": KARI, "program": program, "variables": {"family": "x') or true or ('"}},
        ctx,
    )
    assert _value(found) == "Kari"
    assert _value(hostile) is None


async def test_a_variable_keeps_its_json_type(ctx: FakeContext) -> None:
    config = {
        "input": KARI,
        "program": "(%n + 1) | (%limit > 1.25) | %codes.count() | %patient.id",
        "variables": {"n": 41, "limit": 1.5, "codes": ["a", "b"], "patient": OLA},
    }
    output = await call_block(FhirPathTransformer(), config, ctx)
    assert _value(output) == [42, True, 2, "ola"]


async def test_map_replaces_every_element_and_keeps_the_length(ctx: FakeContext) -> None:
    program = "name.where(use = 'official').family.first()"
    config = {"input": [KARI, OLA, {"resourceType": "Patient"}], "program": program}
    mapped = await call_block(FhirPathMapper(), config, ctx)
    assert _value(mapped) == ["Nordmann", "Hansen", None]


async def test_filter_keeps_what_answers_true_and_drops_what_answers_nothing(ctx: FakeContext) -> None:
    config = {
        "input": [KARI, OLA, {"resourceType": "Patient"}],
        "program": "active and birthDate < %born",
        "variables": {"born": "1990-01-01"},
    }
    output = await call_block(FhirPathFilterer(), config, ctx)
    assert _value(output) == [KARI]


async def test_filter_refuses_an_answer_that_is_not_one_boolean(ctx: FakeContext) -> None:
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirPathFilterer(), {"input": [KARI], "program": "name.given"}, ctx)
    assert raised.value.error_class is ErrorClass.REJECTED
    assert raised.value.params["index"] == 0
    assert "a filter answers true, false or nothing" in str(raised.value)


CITIES: list[dict[str, Any]] = [
    {"name": "oslo", "first-name": "Christiania", "pop": 700000, "active": True, "founded": "1048-01-01"},
    {"name": "bergen", "pop": 290000, "active": False, "founded": "1070-01-01"},
    {"name": "tromso", "active": True, "founded": "1794-01-01"},
]


async def test_filter_reads_json_that_is_not_fhir(ctx: FakeContext) -> None:
    config = {"input": CITIES, "program": "pop > %limit", "variables": {"limit": 500000}}
    output = await call_block(FhirPathFilterer(), config, ctx)
    assert _value(output) == [CITIES[0]]


async def test_filter_reads_a_list_of_numbers_through_this(ctx: FakeContext) -> None:
    output = await call_block(FhirPathFilterer(), {"input": [1, 5, 9], "program": "$this > 3"}, ctx)
    assert _value(output) == [5, 9]


async def test_a_misspelt_field_drops_every_element_and_the_step_succeeds(ctx: FakeContext) -> None:
    output = await call_block(FhirPathFilterer(), {"input": CITIES, "program": "popp > 1"}, ctx)
    assert _value(output) == []


async def test_map_answers_null_for_a_field_an_element_lacks(ctx: FakeContext) -> None:
    output = await call_block(FhirPathMapper(), {"input": CITIES, "program": "pop"}, ctx)
    assert _value(output) == [700000, 290000, None]


@pytest.mark.parametrize(
    ("program", "answer"),
    [
        ("`first-name`", "Christiania"),
        ("founded > @1000-01-01", True),
        ("name.upper()", "OSLO"),
        ("Patient.name", None),
    ],
    ids=["backticks", "date-literal", "string-function", "type-name"],
)
async def test_an_object_that_is_not_fhir_is_walked_like_a_resource(
    program: str, answer: Any, ctx: FakeContext
) -> None:
    output = await call_block(FhirPathTransformer(), {"input": CITIES[0], "program": program}, ctx)
    assert _value(output) == answer


@pytest.mark.parametrize(
    ("program", "answer"),
    [
        ("name", ["oslo", "bergen", "tromso"]),
        ("where(active).name", ["oslo", "tromso"]),
        ("$this.name", None),
    ],
    ids=["bare-path", "where", "this"],
)
async def test_a_list_given_to_transform_is_a_collection(program: str, answer: Any, ctx: FakeContext) -> None:
    output = await call_block(FhirPathTransformer(), {"input": CITIES, "program": program}, ctx)
    assert _value(output) == answer


@pytest.mark.parametrize(
    ("program", "variables", "said"),
    [
        ("name.where(", {}, "the FHIRPath expression does not parse"),
        ("name.where(family = %family)", {}, "reads %family, which the step's variables do not bind"),
        ("name.where(family = %`family`)", {"other": 1}, "reads %family"),
        ("%resource.id", {"resource": "x"}, "variables binds %resource, which FHIRPath defines itself"),
        ('"first-name"', {}, "the FHIRPath expression does not parse"),
    ],
    ids=["unparsed", "unbound", "unbound-delimited", "reserved", "double-quoted-key"],
)
async def test_a_bad_expression_is_refused_at_apply_and_at_run(
    ctx: FakeContext, program: str, variables: dict[str, Any], said: str
) -> None:
    for engine in (FhirPathTransformer(), FhirPathMapper(), FhirPathFilterer()):
        config = FhirPathConfig.model_validate({"input": [KARI], "program": program, "variables": variables})
        issues = engine.check_config(config)
        assert [issue.code for issue in issues] == ["plugin.program_refused"]
        assert said in str(issues[0].params["detail"])
        with pytest.raises(BlockFailure) as raised:
            await call_block(engine, config.model_dump(), ctx)
        assert raised.value.error_class is ErrorClass.REJECTED
        assert said in str(raised.value)


def test_a_good_expression_passes_the_apply_check() -> None:
    config = FhirPathConfig.model_validate(
        {"input": None, "program": "name.where(family = %family) | %resource.id | %ucum", "variables": {"family": "N"}}
    )
    assert FhirPathTransformer().check_config(config) == []


def test_a_variable_name_fhirpath_cannot_read_bare_is_refused_by_the_schema() -> None:
    with pytest.raises(ValueError, match="pattern"):
        FhirPathConfig.model_validate({"input": None, "program": "1", "variables": {"two words": 1}})


async def test_an_answer_past_max_output_fails_the_step(ctx: FakeContext) -> None:
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirPathTransformer(), {"input": KARI, "program": "name", "max_output": "64B"}, ctx)
    assert raised.value.error_class is ErrorClass.REJECTED
    assert "runs past max_output (64 bytes)" in str(raised.value)


@pytest.mark.parametrize("engine", [FhirPathTransformer, FhirPathMapper, FhirPathFilterer])
def test_every_verb_publishes_its_program_as_fhirpath(
    engine: type[Transformer] | type[Mapper] | type[Filterer],
) -> None:
    """The UI edits a field as a program when its schema names the language."""
    published = engine.config_model.model_json_schema()["properties"]["program"]
    assert published["contentMediaType"] == FHIRPATH_MEDIA_TYPE == "application/fhirpath"


def test_every_verb_is_a_frame_block_that_runs_nothing_on_the_worker() -> None:
    for engine in (FhirPathTransformer(), FhirPathMapper(), FhirPathFilterer()):
        assert engine.spec.group == "transform"
        assert engine.spec.idempotent
        assert not engine.spec.local_execution


async def test_a_long_evaluation_leaves_the_event_loop_free(ctx: FakeContext) -> None:
    beats = 0

    async def heartbeat() -> None:
        nonlocal beats
        while True:
            await asyncio.sleep(0.01)
            beats += 1

    beating = asyncio.create_task(heartbeat())
    config = {"input": None, "program": SLOW, "variables": {"n": list(range(400))}}
    output = await call_block(FhirPathTransformer(), config, ctx)
    beating.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await beating
    assert _value(output) == 400
    assert beats > 5, "the loop ran nothing while the expression did"


async def test_a_step_timeout_ends_a_long_evaluation_and_kills_its_process(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = runners._RUNNERS  # pyright: ignore[reportPrivateUsage] - the processes under test
    held: list[ProgramRunner] = []
    take = pool.take

    async def watched(command: Sequence[str]) -> ProgramRunner:
        process = await take(command)
        held.append(process)
        return process

    monkeypatch.setattr(pool, "take", watched)
    started = time.monotonic()
    config = {"input": None, "program": SLOW, "variables": {"n": list(range(3000))}}
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.5):
            await call_block(FhirPathTransformer(), config, ctx)
    assert time.monotonic() - started < 2.0
    assert len(held) == 1
    assert not held[0].alive, "the evaluation was left running"

    after = await call_block(FhirPathTransformer(), {"input": KARI, "program": "id"}, ctx)
    assert _value(after) == "kari"
