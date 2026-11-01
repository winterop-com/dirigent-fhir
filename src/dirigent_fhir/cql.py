"""``fhir.cql`` and ``fhir.measure``: a CQL library, and a measure, over the resources a step produced.

Both evaluate on the engine's CQL evaluator, over the resources handed in as the engine's Bundle
data source, in a runner process of their own: a step's timeout ends a long evaluation by killing
that process, and the worker's event loop stays free. A value reaches a library only as a CQL
parameter, under ``parameters``.
"""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final, Literal, cast

from dhis2w_fhir_engine import CQLEvaluator
from pydantic import BaseModel, Field, JsonValue

from dirigent_common import BlockModel, Issue, Size
from dirigent_fhir.messages import (
    CQL_DEFINITIONS_FAILED,
    CQL_OUTPUT_TOO_LARGE,
    CQL_REFUSED,
    MEASURE_NO_POPULATIONS,
)
from dirigent_plugin import BlockFailure, ErrorClass, Operator, OperatorSpec, RunnerEngine, StepContext, TransformError
from dirigent_plugin.messages import PROGRAM_REFUSED

#: The media type a CQL library field publishes, which the UI edits as a program.
CQL_MEDIA_TYPE: Final = "text/cql"

#: The script a CQL runner process runs, started by path so it imports the engine and nothing of dirigent.
RUNNER = Path(__file__).with_name("cql_runner.py")

#: The code system a MeasureReport's population codes are drawn from.
MEASURE_POPULATION: Final = "http://terminology.hl7.org/CodeSystem/measure-population"

#: How many failures a refusal names before it counts the rest.
NAMED: Final = 3

Library = Annotated[str, Field(min_length=1, json_schema_extra={"contentMediaType": CQL_MEDIA_TYPE})]


class _CqlConfig(BlockModel):
    """What both blocks are told: a library, the resources it reads, and its parameters."""

    library: Library
    """The CQL library, as source, with `using FHIR version '4.0.1'`."""

    data: JsonValue
    """The resources the library reads: a Bundle, such as a `fhir.search` step's `bundle`, or a list of resources."""

    parameters: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    """Values for the library's parameters by name, each written inline or as a `${...}` reference."""

    max_output: Size = Field(default=16 * 1024 * 1024, ge=1)
    """The most the evaluation's answer may run to as JSON, such as `16MB`."""


class FhirCqlConfig(_CqlConfig):
    """A library to evaluate, and which of its definitions to answer."""

    definitions: list[str] | None = None
    """The definitions answered, every one the library defines when unset."""


class FhirCqlPatient(BlockModel):
    """One patient's answers, in the Patient context."""

    patient: str
    """The patient, as `Patient/id`."""

    values: dict[str, JsonValue]
    """Each definition's value for this patient, by name."""


class FhirCqlOutput(BlockModel):
    """The library's definitions, evaluated."""

    context: Literal["Patient", "Unfiltered"]
    """`Patient` when the library evaluates per patient, `Unfiltered` when once over everything."""

    values: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    """In the Unfiltered context, each definition's value by name."""

    patients: list[FhirCqlPatient] = Field(default_factory=list[FhirCqlPatient])
    """In the Patient context, each patient the data carries with that patient's values."""


class FhirMeasureConfig(_CqlConfig):
    """A measure's library, how it is scored, and what the report names."""

    measure: str = Field(min_length=1)
    """The canonical URL of the Measure the report is about, such as `http://example.org/Measure/heavy`."""

    period_start: str = Field(min_length=1)
    """The start of the reporting period, a FHIR date or dateTime, as the report states it."""

    period_end: str = Field(min_length=1)
    """The end of the reporting period, a FHIR date or dateTime, as the report states it."""

    scoring: Literal["proportion", "ratio", "cohort"] = "proportion"
    """How the populations nest and are scored: `proportion` nests each in the one before it, `ratio` keeps the
    numerator and denominator apart, `cohort` counts the initial population only."""


class FhirMeasureOutput(BlockModel):
    """The measure's populations, its score, and the MeasureReport that states them."""

    populations: dict[str, int]
    """How many patients each population the library defines holds, by population code."""

    score: float | None = None
    """The measure score: numerator over denominator after exclusions and exceptions; none for a cohort or an
    empty denominator."""

    members: dict[str, list[str]]
    """Which patients each population holds, as `Patient/id`."""

    report: dict[str, JsonValue]
    """A summary `MeasureReport`, as plain FHIR JSON, for a later step to write or hand on."""


class _CqlEngine(RunnerEngine):
    """Where a library is evaluated: a CQL runner process, never the worker's loop."""

    command: ClassVar[Sequence[str]] = (sys.executable, str(RUNNER))

    def check_config(self, config: BaseModel) -> list[Issue]:
        """Compile the library at apply, refusing a bad one with the compiler's own message."""
        if not isinstance(config, _CqlConfig):
            return []
        try:
            CQLEvaluator().compile(config.library)
        except Exception as error:
            return [Issue.of(PROGRAM_REFUSED, detail=CQL_REFUSED.render(detail=str(error).strip()))]
        return []

    async def evaluate(self, config: _CqlConfig, kind: str, **asked: Any) -> dict[str, Any]:
        """Compile the library in a runner and evaluate it over the data, refusing what the engine refuses."""
        program = json.dumps(
            {
                "kind": kind,
                "library": config.library,
                "parameters": config.parameters,
                "max_output": config.max_output,
                "too_large": CQL_OUTPUT_TOO_LARGE.render(limit=config.max_output),
                "definitions": asked.get("definitions"),
                "scoring": asked.get("scoring", "proportion"),
            }
        )
        try:
            answers = await self.offload(lambda: self.outputs(self.compile(program), config.data))
        except TransformError as error:
            raise BlockFailure(CQL_REFUSED, error_class=ErrorClass.REJECTED, detail=str(error)) from error
        answer = cast("dict[str, Any]", answers[0])
        failed = cast("list[str]", answer.get("errors") or [])
        if failed:
            more = f" (and {len(failed) - NAMED} more)" if len(failed) > NAMED else ""
            raise BlockFailure(
                CQL_DEFINITIONS_FAILED,
                error_class=ErrorClass.REJECTED,
                count=len(failed),
                detail="; ".join(failed[:NAMED]) + more,
                failures=failed,
            )
        return answer


class FhirCqlOperator(_CqlEngine, Operator[FhirCqlConfig, FhirCqlOutput]):
    """Evaluates a CQL library over the resources a step produced. It touches no server."""

    spec = OperatorSpec(id="fhir.cql", summary="Evaluate a CQL library over FHIR resources.", idempotent=True)
    config_model: ClassVar[type[BaseModel]] = FhirCqlConfig
    output_model: ClassVar[type[BaseModel]] = FhirCqlOutput

    async def execute(self, config: FhirCqlConfig, ctx: StepContext) -> FhirCqlOutput:
        """Evaluate the library, once per patient in the Patient context and once otherwise."""
        answer = await self.evaluate(config, "cql", definitions=config.definitions)
        return FhirCqlOutput.model_validate({key: answer[key] for key in ("context", "values", "patients")})


def _score(populations: dict[str, int], scoring: str) -> float | None:
    """Numerator over denominator, after the exclusions and exceptions the scoring takes off."""
    if scoring == "cohort":
        return None
    numerator = populations.get("numerator", 0) - populations.get("numerator-exclusion", 0)
    denominator = populations.get("denominator", 0) - populations.get("denominator-exclusion", 0)
    if scoring == "proportion":
        denominator -= populations.get("denominator-exception", 0)
    return round(numerator / denominator, 4) if denominator > 0 else None


def _report(config: FhirMeasureConfig, populations: dict[str, int], score: float | None) -> dict[str, JsonValue]:
    """A summary MeasureReport stating the populations and the score."""
    group: dict[str, JsonValue] = {
        "population": [
            {"code": {"coding": [{"system": MEASURE_POPULATION, "code": code}]}, "count": count}
            for code, count in populations.items()
        ]
    }
    if score is not None:
        group["measureScore"] = {"value": score}
    return {
        "resourceType": "MeasureReport",
        "status": "complete",
        "type": "summary",
        "measure": config.measure,
        "period": {"start": config.period_start, "end": config.period_end},
        "group": [group],
    }


class FhirMeasureOperator(_CqlEngine, Operator[FhirMeasureConfig, FhirMeasureOutput]):
    """Evaluates a measure over the resources a step produced, and answers a MeasureReport. It touches no server."""

    spec = OperatorSpec(id="fhir.measure", summary="Evaluate a measure and answer a MeasureReport.", idempotent=True)
    config_model: ClassVar[type[BaseModel]] = FhirMeasureConfig
    output_model: ClassVar[type[BaseModel]] = FhirMeasureOutput

    async def execute(self, config: FhirMeasureConfig, ctx: StepContext) -> FhirMeasureOutput:
        """Evaluate each patient's populations, count them, and score them."""
        answer = await self.evaluate(config, "measure", scoring=config.scoring)
        populations = cast("dict[str, int]", answer["populations"])
        if "initial-population" not in populations:
            raise BlockFailure(MEASURE_NO_POPULATIONS, error_class=ErrorClass.REJECTED)
        score = _score(populations, config.scoring)
        members = cast("dict[str, list[str]]", answer["members"])
        return FhirMeasureOutput(
            populations=populations,
            score=score,
            members={code: members.get(code, []) for code in populations},
            report=_report(config, populations, score),
        )
