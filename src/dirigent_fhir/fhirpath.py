"""The ``fhirpath`` engines: a FHIRPath expression as the transform, map and filter verbs' program.

The input is any JSON value: nothing here requires a ``resourceType``. An expression is evaluated by the engine's FHIRPath evaluator in a runner process of its own,
over dirigent's runner protocol, so a step's timeout ends a long evaluation by killing that
process. A value reaches an expression only as a variable: the step's ``variables`` are bound
as FHIRPath environment variables, read as ``%name``, and a ``${...}`` in the expression is
refused by dirigent before the block sees it.

A bad expression, or one reading a variable the step does not bind, is refused at apply on the
worker, where the expression is parsed but never evaluated, and again when the step compiles it.
"""

import json
import sys
from collections.abc import Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final

from pydantic import BaseModel, Field, JsonValue

from dirigent_common import Issue, Size
from dirigent_fhir.fhirpath_runner import BUILT_IN, constants, parse
from dirigent_fhir.messages import (
    FHIRPATH_FILTER_ANSWER,
    FHIRPATH_OUTPUT_TOO_LARGE,
    FHIRPATH_REFUSED,
    FHIRPATH_RESERVED_VARIABLE,
    FHIRPATH_UNBOUND_VARIABLE,
)
from dirigent_plugin import (
    Filterer,
    Mapper,
    ProgramConfig,
    RunnerEngine,
    StepContext,
    Transformer,
    TransformError,
    TransformOutput,
)
from dirigent_plugin.messages import PROGRAM_REFUSED

#: The media type a FHIRPath expression field publishes, which the UI edits as a program and
#: dirigent refuses a ``${...}`` reference in.
FHIRPATH_MEDIA_TYPE: Final = "application/fhirpath"

#: The script a FHIRPath runner process runs, started by path so it imports the evaluator and
#: nothing of dirigent.
RUNNER = Path(__file__).with_name("fhirpath_runner.py")

#: A variable's name: what FHIRPath reads after ``%`` without delimiters.
VARIABLE_NAME: Final = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"

#: The step's config while it runs, which the expression's variables and bound are read from.
_CONFIG: ContextVar["FhirPathConfig"] = ContextVar("dirigent_fhir_fhirpath_config")


class FhirPathConfig(ProgramConfig):
    """The config of a FHIRPath engine, whichever verb is running it."""

    program: Annotated[str, Field(min_length=1, json_schema_extra={"contentMediaType": FHIRPATH_MEDIA_TYPE})]
    """The FHIRPath expression this step evaluates, with `%resource` the value it is evaluated over."""

    variables: dict[Annotated[str, Field(pattern=VARIABLE_NAME)], JsonValue] = Field(
        default_factory=dict[str, JsonValue]
    )
    """Values the expression reads as `%name`, each written inline or as a `${...}` reference."""

    max_output: Size = Field(default=16 * 1024 * 1024, ge=1)
    """The most one evaluation's answer may run to as JSON, such as `16MB`."""


class FhirPathEngine(RunnerEngine):
    """Where the three FHIRPath engines' expressions run: in a runner process, never on the worker's loop."""

    command: ClassVar[Sequence[str]] = (sys.executable, str(RUNNER))

    def check_program(self, program: str) -> None:
        """Parse the expression, refusing a bad one with the parser's own message."""
        _checked(program, {})

    def check_config(self, config: BaseModel) -> list[Issue]:
        """Parse the expression at apply, and refuse a variable it reads that the step does not bind."""
        if not isinstance(config, FhirPathConfig):
            return []
        try:
            _checked(config.program, config.variables)
        except TransformError as error:
            return [Issue.of(PROGRAM_REFUSED, detail=str(error))]
        return []

    def source(self, program: str) -> str:
        """Hand the runner the expression with the step's variables and its bound."""
        config = _CONFIG.get()
        return json.dumps(
            {
                "expression": program,
                "variables": config.variables,
                "max_output": config.max_output,
                "too_large": FHIRPATH_OUTPUT_TOO_LARGE.render(limit=config.max_output),
            }
        )

    def compile(self, program: str) -> object:
        """Check the expression here, then compile it in the runner this step holds."""
        _checked(program, _CONFIG.get().variables)
        return super().compile(program)

    async def execute(self, config: Any, ctx: StepContext) -> Any:
        """Run the verb with the step's config in reach of the runner's source."""
        token = _CONFIG.set(config)
        try:
            return await super().execute(config, ctx)  # type: ignore[misc]
        finally:
            _CONFIG.reset(token)


def _checked(program: str, variables: dict[str, JsonValue]) -> None:
    """Parse an expression and hold it to the variables the step binds."""
    try:
        read = constants(parse(program))
    except RecursionError as error:
        raise TransformError(FHIRPATH_REFUSED.render(detail="it nests deeper than the parser can follow")) from error
    except Exception as error:
        raise TransformError(FHIRPATH_REFUSED.render(detail=str(error).strip())) from error
    reserved = sorted(BUILT_IN & set(variables))
    if reserved:
        raise TransformError(FHIRPATH_RESERVED_VARIABLE.render(name=reserved[0]))
    unbound = sorted(read - BUILT_IN - set(variables))
    if unbound:
        raise TransformError(FHIRPATH_UNBOUND_VARIABLE.render(name=unbound[0]))


def _one_value(produced: list[JsonValue]) -> JsonValue:
    """A collection as one value: nothing as null, one item as itself, several as their list."""
    if not produced:
        return None
    return produced[0] if len(produced) == 1 else produced


class FhirPathTransformer(FhirPathEngine, Transformer):
    """Evaluates a FHIRPath expression over one whole value, any JSON such as a resource or a Bundle.

    A FHIRPath answer is a collection. An empty one is the step's `null`, one item is the value
    itself, and several are their list, so `name.family.first()` answers a string and
    `name.given` a list when there are several. The evaluator opens no file and no socket:
    `resolve()` and terminology functions have nothing to reach.
    """

    kind = "fhirpath"
    summary = "Reshape a value with a FHIRPath expression."
    config_model: ClassVar[type[BaseModel]] = FhirPathConfig
    output_model: ClassVar[type[BaseModel]] = TransformOutput

    def apply(self, compiled: object, value: JsonValue) -> JsonValue:
        """Evaluate over one value, and read the collection as one result."""
        return _one_value(self.outputs(compiled, value))


class FhirPathMapper(FhirPathEngine, Mapper):
    """Replaces every element of a list with what a FHIRPath expression evaluates to over it.

    Each element, any JSON, is evaluated on its own, as `%resource`, and replaced the way `transform.fhirpath`
    reads a collection: nothing is `null`, one item is itself, and several are their list. The
    list keeps its length, which is the map frame's promise.
    """

    kind = "fhirpath"
    summary = "Replace every element of a list with what a FHIRPath expression makes of it."
    config_model: ClassVar[type[BaseModel]] = FhirPathConfig
    output_model: ClassVar[type[BaseModel]] = TransformOutput

    def apply(self, compiled: object, value: JsonValue) -> JsonValue:
        """Evaluate over one element, and take the collection as its replacement."""
        return _one_value(self.outputs(compiled, value))


class FhirPathFilterer(FhirPathEngine, Filterer):
    """Keeps the elements of a list a FHIRPath expression answers true for.

    Each element, any JSON, is evaluated on its own, as `%resource`, the way FHIRPath's own `where()` reads
    its criteria: an empty answer keeps nothing, `true` keeps the element, `false` drops it, and
    anything else, a string or several items, is a refusal rather than a truthiness the step
    would have to guess at.
    """

    kind = "fhirpath"
    summary = "Keep the elements of a list a FHIRPath expression answers true for."
    config_model: ClassVar[type[BaseModel]] = FhirPathConfig
    output_model: ClassVar[type[BaseModel]] = TransformOutput

    def keep(self, compiled: object, value: JsonValue) -> bool:
        """Evaluate over one element, and read its answer as the verdict."""
        produced = self.outputs(compiled, value)
        if not produced:
            return False
        if len(produced) == 1 and isinstance(produced[0], bool):
            return produced[0]
        raise TransformError(FHIRPATH_FILTER_ANSWER.render(answer=json.dumps(produced)))


__all__ = [
    "FHIRPATH_MEDIA_TYPE",
    "FhirPathConfig",
    "FhirPathFilterer",
    "FhirPathMapper",
    "FhirPathTransformer",
]
