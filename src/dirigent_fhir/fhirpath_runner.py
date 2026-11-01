"""The FHIRPath runner: the process an expression is evaluated in, spoken to over its pipes.

It speaks dirigent's runner protocol, one JSON object per line each way: ``compile`` holds an
expression under an id, ``run`` evaluates the expression an id names over one value, and
``forget`` releases one. A compiled program is a JSON object carrying the ``expression``, the
``variables`` bound as ``%name``, the most bytes an answer may run to as ``max_output``, and the
refusal of a larger one as ``too_large``.

An expression runs here rather than on the worker so that a step's timeout ends it: the process
is killed, which is the one way to stop a computation in Python.

This module is run by path, never imported, so it loads the standard library and the engine's
FHIRPath evaluator and nothing else.
"""

import json
import sys
from decimal import Decimal
from typing import Any, cast

from antlr4 import CommonTokenStream, InputStream  # type: ignore[import-untyped]
from dhis2w_fhir_engine.evaluation import EvaluationContext, FHIRPathError
from dhis2w_fhir_engine.evaluation.fhirpath import FHIRPathEvaluator, unwrap_primitives
from dhis2w_fhir_engine.evaluation.fhirpath.evaluator import FHIRPathErrorListener
from dhis2w_fhir_engine.evaluation.parsing import require_end_of_input
from dhis2w_fhir_engine.evaluation.types import FHIRDate, FHIRDateTime, FHIRTime, Quantity
from dhis2w_fhir_engine.generated.fhirpath.fhirpathLexer import fhirpathLexer
from dhis2w_fhir_engine.generated.fhirpath.fhirpathParser import fhirpathParser

#: The environment variables FHIRPath and the engine define themselves, which a step's own may not shadow.
BUILT_IN: frozenset[str] = frozenset(
    {"resource", "rootResource", "context", "ucum", "sct", "loinc", "vs-administrative-gender"}
)


def parse(expression: str) -> Any:
    """Parse an expression into the engine's tree, raising FHIRPathError with the parser's message."""
    lexer = fhirpathLexer(InputStream(expression))
    lexer.removeErrorListeners()
    lexer.addErrorListener(FHIRPathErrorListener())
    parser = fhirpathParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(FHIRPathErrorListener())
    tree = parser.expression()
    require_end_of_input(parser, FHIRPathError)
    return tree


def constants(tree: Any) -> set[str]:
    """Every environment variable an expression reads, by the name it is written under."""
    found: set[str] = set()
    pending = [tree]
    while pending:
        node = pending.pop()
        if isinstance(node, fhirpathParser.ExternalConstantContext):
            held: Any = node.identifier() or node.STRING()
            text = str(held.getText())
            found.add(text[1:-1] if text[:1] in ("`", "'") else text)
        pending.extend(node.getChildren() if hasattr(node, "getChildren") else [])
    return found


class Program:
    """One compiled expression, the variables it reads, and the most its answer may run to."""

    def __init__(self, compiled: dict[str, Any]) -> None:
        """Hold the expression and parse it once, so a bad one is refused at compile."""
        self.expression: str = compiled["expression"]
        self.variables: dict[str, Any] = compiled["variables"]
        self.max_output: int = compiled["max_output"]
        self.too_large: str = compiled["too_large"]
        parse(self.expression)
        self.evaluator = FHIRPathEvaluator()

    def _context(self, value: Any) -> EvaluationContext:
        """A context whose %resource is the value and whose constants are the step's variables."""
        resource: dict[str, Any] | None = cast("dict[str, Any]", value) if isinstance(value, dict) else None
        context = EvaluationContext(resource=resource)
        for name, bound in self.variables.items():
            context.set_constant(name, bound)
        return context

    def run(self, value: Any) -> list[Any]:
        """Evaluate over one value and answer the collection as JSON values."""
        produced = [plain(item) for item in self.evaluator.evaluate(self.expression, value, self._context(value))]
        if len(json.dumps(produced).encode()) > self.max_output:
            raise ValueError(self.too_large)
        return produced


def plain(value: Any) -> Any:
    """A FHIRPath value as JSON: a primitive unwrapped, a decimal a number, a date its FHIR spelling."""
    value = unwrap_primitives(value)
    if isinstance(value, bool) or value is None or isinstance(value, str | int | float):
        return value
    if isinstance(value, Decimal):
        exponent = value.as_tuple().exponent
        return int(value) if isinstance(exponent, int) and exponent >= 0 else float(value)
    if isinstance(value, FHIRDate | FHIRDateTime | FHIRTime):
        return str(value)
    if isinstance(value, Quantity):
        return {"value": plain(value.value), "unit": value.unit}
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in cast("dict[object, Any]", value).items()}
    if isinstance(value, list):
        return [plain(item) for item in cast("list[object]", value)]
    return str(value)


def answer(programs: dict[str, Program], request: dict[str, Any]) -> dict[str, Any]:
    """Compile an expression under its id, run the one an id names, or release it."""
    kind = request["kind"]
    try:
        if kind == "compile":
            programs[request["id"]] = Program(json.loads(request["program"], parse_float=Decimal))
            return {"ok": True}
        if kind == "forget":
            programs.pop(request["id"], None)
            return {"ok": True}
        if kind == "run":
            program = programs.get(request["id"])
            if program is None:
                return {"error": f"no expression is compiled under {request['id']}"}
            return {"outputs": program.run(request["value"])}
    except RecursionError:
        return {"error": "the expression nests deeper than the evaluator can follow"}
    except Exception as error:
        return {"error": str(error).strip() or type(error).__name__}
    return {"error": f"{kind} is not a request kind: compile, run, or forget"}


def main() -> None:
    """Answer one request at a time until the pipe closes, holding the expressions compiled so far."""
    out = sys.stdout.buffer
    programs: dict[str, Program] = {}
    for line in sys.stdin.buffer:
        reply = answer(programs, json.loads(line, parse_float=Decimal))
        out.write(json.dumps(reply, default=plain).encode() + b"\n")
        out.flush()


if __name__ == "__main__":
    main()
