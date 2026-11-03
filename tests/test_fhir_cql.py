"""``fhir.cql`` and ``fhir.measure`` on the engine's CQL evaluator, over resources a step produced."""

import asyncio
import time
from typing import Any

import pytest

from dirigent_fhir import FhirCqlConfig, FhirCqlOperator, FhirCqlOutput, FhirMeasureOperator, FhirMeasureOutput
from dirigent_plugin import BlockFailure, ErrorClass
from dirigent_testing import FakeContext, call_block

#: Four patients, two of them adults, and one body weight each: 70, 90, 110 and 130 kg.
PATIENTS = [
    {"resourceType": "Patient", "id": f"p{i}", "birthDate": "1980-01-01" if i % 2 else "2015-01-01"} for i in range(4)
]
WEIGHTS = [
    {
        "resourceType": "Observation",
        "id": f"o{i}",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "29463-7"}]},
        "subject": {"reference": f"Patient/p{i}"},
        "valueQuantity": {"value": 70 + i * 20, "unit": "kg"},
    }
    for i in range(4)
]
BUNDLE = {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in PATIENTS + WEIGHTS],
}

HEADER = """
library Weights version '1.0.0'
using FHIR version '4.0.1'
include FHIRHelpers version '4.0.1'
codesystem "LOINC": 'http://loinc.org'
code "Body weight": '29463-7' from "LOINC"
parameter Limit Decimal default 90.0
"""

#: A measure whose numerator does not repeat the denominator's criterion, as real measures do not:
#: p2 is heavy but a child, and only the population rules keep p2 out of the numerator.
MEASURE = (
    HEADER
    + """
context Patient
define "Initial Population": exists [Observation: "Body weight"]
define "Denominator": AgeInYears() >= 18
define "Numerator": exists ([Observation: "Body weight"] O where (O.value as Quantity).value > Limit)
"""
)

LIBRARY = (
    HEADER
    + """
context Patient
define "Adult": AgeInYears() >= 18
define "Heaviest": Max([Observation: "Body weight"] O return (O.value as Quantity).value)
define "Heavy": "Heaviest" > Limit
"""
)

MEASURE_CONFIG: dict[str, Any] = {
    "library": MEASURE,
    "data": BUNDLE,
    "measure": "http://example.org/Measure/heavy-adults",
    "period_start": "2026-01-01",
    "period_end": "2026-12-31",
}


async def test_a_library_is_evaluated_once_per_patient(ctx: FakeContext) -> None:
    output = await call_block(FhirCqlOperator(), {"library": LIBRARY, "data": BUNDLE}, ctx)
    assert isinstance(output, FhirCqlOutput)
    assert output.context == "Patient"
    assert [(one.patient, one.values) for one in output.patients] == [
        ("Patient/p0", {"Adult": False, "Heaviest": 70, "Heavy": False}),
        ("Patient/p1", {"Adult": True, "Heaviest": 90, "Heavy": False}),
        ("Patient/p2", {"Adult": False, "Heaviest": 110, "Heavy": True}),
        ("Patient/p3", {"Adult": True, "Heaviest": 130, "Heavy": True}),
    ]


async def test_a_parameter_is_a_value_the_library_reads(ctx: FakeContext) -> None:
    config = {"library": LIBRARY, "data": PATIENTS + WEIGHTS, "parameters": {"Limit": 120}, "definitions": ["Heavy"]}
    output = await call_block(FhirCqlOperator(), config, ctx)
    assert isinstance(output, FhirCqlOutput)
    assert [one.values for one in output.patients] == [{"Heavy": False}] * 3 + [{"Heavy": True}]


async def test_an_unfiltered_library_is_evaluated_once(ctx: FakeContext) -> None:
    library = HEADER + 'define "Weights": Count([Observation: "Body weight"])\ndefine "Codes": "Body weight"\n'
    output = await call_block(FhirCqlOperator(), {"library": library, "data": BUNDLE}, ctx)
    assert isinstance(output, FhirCqlOutput)
    assert output.context == "Unfiltered"
    assert output.values == {"Weights": 4, "Codes": {"code": "29463-7", "system": "http://loinc.org"}}


async def test_a_measure_nests_its_populations_and_answers_a_measure_report(ctx: FakeContext) -> None:
    output = await call_block(FhirMeasureOperator(), MEASURE_CONFIG, ctx)
    assert isinstance(output, FhirMeasureOutput)
    assert output.populations == {"initial-population": 4, "denominator": 2, "numerator": 1}
    assert output.members["numerator"] == ["Patient/p3"]
    assert output.score == 0.5
    assert output.report == {
        "resourceType": "MeasureReport",
        "status": "complete",
        "type": "summary",
        "measure": "http://example.org/Measure/heavy-adults",
        "period": {"start": "2026-01-01", "end": "2026-12-31"},
        "group": [
            {
                "population": [
                    {
                        "code": {
                            "coding": [
                                {"system": "http://terminology.hl7.org/CodeSystem/measure-population", "code": code}
                            ]
                        },
                        "count": count,
                    }
                    for code, count in (("initial-population", 4), ("denominator", 2), ("numerator", 1))
                ],
                "measureScore": {"value": 0.5},
            }
        ],
    }


@pytest.mark.parametrize(("limit", "numerator"), [(50, ["Patient/p1", "Patient/p3"]), (90, ["Patient/p3"])])
async def test_a_parameter_reaches_the_measure_as_it_reaches_the_library(
    ctx: FakeContext, limit: int, numerator: list[str]
) -> None:
    measure = await call_block(FhirMeasureOperator(), {**MEASURE_CONFIG, "parameters": {"Limit": limit}}, ctx)
    library = await call_block(
        FhirCqlOperator(), {"library": LIBRARY, "data": BUNDLE, "parameters": {"Limit": limit}}, ctx
    )
    assert isinstance(measure, FhirMeasureOutput)
    assert isinstance(library, FhirCqlOutput)
    heavy_adults = [one.patient for one in library.patients if one.values["Adult"] and one.values["Heavy"]]
    assert measure.members["numerator"] == heavy_adults == numerator
    assert measure.populations["numerator"] == len(numerator)


async def test_a_cohort_counts_only_its_initial_population(ctx: FakeContext) -> None:
    output = await call_block(FhirMeasureOperator(), {**MEASURE_CONFIG, "scoring": "cohort"}, ctx)
    assert isinstance(output, FhirMeasureOutput)
    assert output.populations == {"initial-population": 4, "denominator": 0, "numerator": 0}
    assert output.score is None
    assert "measureScore" not in output.report["group"][0]  # type: ignore[index, operator]


async def test_a_ratio_keeps_the_numerator_apart_from_the_denominator(ctx: FakeContext) -> None:
    output = await call_block(FhirMeasureOperator(), {**MEASURE_CONFIG, "scoring": "ratio"}, ctx)
    assert isinstance(output, FhirMeasureOutput)
    assert output.populations == {"initial-population": 4, "denominator": 2, "numerator": 2}
    assert output.score == 1.0


async def test_a_library_with_no_initial_population_is_no_measure(ctx: FakeContext) -> None:
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirMeasureOperator(), {**MEASURE_CONFIG, "library": LIBRARY}, ctx)
    assert raised.value.code == "fhir.measure.no_populations"
    assert raised.value.error_class is ErrorClass.REJECTED


async def test_a_bad_library_is_refused_at_apply_and_at_run(ctx: FakeContext) -> None:
    config = FhirCqlConfig.model_validate({"library": HEADER + "define Broken: (", "data": BUNDLE})
    issues = FhirCqlOperator().check_config(config)
    assert [issue.code for issue in issues] == ["plugin.program_refused"]
    assert "the CQL library was refused" in str(issues[0].params["detail"])
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirCqlOperator(), config.model_dump(), ctx)
    assert raised.value.code == "fhir.cql.refused"
    assert raised.value.error_class is ErrorClass.REJECTED


async def test_a_definition_the_library_lacks_is_refused(ctx: FakeContext) -> None:
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirCqlOperator(), {"library": LIBRARY, "data": BUNDLE, "definitions": ["Nope"]}, ctx)
    assert raised.value.code == "fhir.cql.refused"
    assert "the library defines no Nope" in str(raised.value)


async def test_a_definition_that_fails_to_evaluate_fails_the_step(ctx: FakeContext) -> None:
    library = HEADER + 'context Patient\ndefine "Broken": singleton from ({1, 2})\n'
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirCqlOperator(), {"library": library, "data": BUNDLE}, ctx)
    assert raised.value.code == "fhir.cql.definitions_failed"
    assert raised.value.error_class is ErrorClass.REJECTED
    assert raised.value.params["count"] == 4


async def test_an_answer_past_max_output_fails_the_step(ctx: FakeContext) -> None:
    with pytest.raises(BlockFailure) as raised:
        await call_block(FhirCqlOperator(), {"library": LIBRARY, "data": BUNDLE, "max_output": "32B"}, ctx)
    assert raised.value.code == "fhir.cql.refused"
    assert "runs past max_output (32 bytes)" in str(raised.value)


async def test_a_step_timeout_ends_a_long_evaluation(ctx: FakeContext) -> None:
    library = HEADER + (
        'define "N": expand { Interval[1, 3000] }\ndefine "Slow": Count("N" X where Count("N" Y where Y < X) > 0)\n'
    )
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.5):
            await call_block(FhirCqlOperator(), {"library": library, "data": BUNDLE}, ctx)
    assert time.monotonic() - started < 2.0
