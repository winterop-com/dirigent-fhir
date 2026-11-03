"""The CQL runner: the process a library and a measure are evaluated in, spoken to over its pipes.

It speaks dirigent's runner protocol. A compiled program is a JSON object: the ``kind``, ``cql`` or
``measure``, the ``library`` source, its ``parameters``, the ``definitions`` a ``cql`` program
answers, a measure's ``scoring``, and the most bytes an answer may run to as ``max_output`` with
the refusal of a larger one as ``too_large``. A ``run`` evaluates the program over one value, the
resources as a Bundle, and answers with one output.

This module is run by path, never imported, so it loads the standard library and the engine and
nothing of dirigent.
"""

import json
import sys
from decimal import Decimal
from typing import Any, cast

from dhis2w_fhir_engine import BundleDataSource, CQLEvaluator, CQLLibrary, MeasureEvaluator
from dhis2w_fhir_engine.evaluation.cql.types import CQLCode, CQLConcept, CQLInterval, CQLRatio, CQLTuple
from dhis2w_fhir_engine.evaluation.exceptions import CQLError
from dhis2w_fhir_engine.evaluation.fhirpath import unwrap_primitives
from dhis2w_fhir_engine.evaluation.types import FHIRDate, FHIRDateTime, FHIRTime, Quantity
from dhis2w_fhir_engine.ingest import ResourceInput

#: The population codes a proportion, ratio or cohort measure reports, in report order.
POPULATIONS: tuple[str, ...] = (
    "initial-population",
    "denominator",
    "denominator-exclusion",
    "denominator-exception",
    "numerator",
    "numerator-exclusion",
)


def plain(value: Any) -> Any:
    """A CQL value as JSON: a decimal a number, a date its FHIR spelling, a code, interval or tuple an object."""
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
    if isinstance(value, CQLCode):
        return {key: item for key, item in value.model_dump().items() if item is not None}
    if isinstance(value, CQLConcept | CQLRatio):
        return plain(value.model_dump())
    if isinstance(value, CQLInterval):
        return {
            "low": plain(value.low),
            "high": plain(value.high),
            "lowClosed": value.low_closed,
            "highClosed": value.high_closed,
        }
    if isinstance(value, CQLTuple):
        return plain(value.elements)
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in cast("dict[object, Any]", value).items()}
    if isinstance(value, list | tuple | set):
        return [plain(item) for item in list(cast("list[object]", value))]
    return str(value)


def _patients(source: BundleDataSource) -> list[dict[str, Any]]:
    """The patients a Bundle carries, the subjects a library in the Patient context is evaluated for."""
    return list(source.resources.get("Patient", []))


def _source(value: Any) -> BundleDataSource:
    """The resources handed in as the engine's data source: a Bundle, or a list wrapped as one."""
    bundle = value
    if isinstance(value, list):
        entries = [{"resource": item} for item in cast("list[object]", value)]
        bundle = {"resourceType": "Bundle", "type": "collection", "entry": entries}
    source = BundleDataSource()
    source.load_bundle(bundle)
    return source


class _Parameterised(CQLEvaluator):
    """A CQL evaluator that hands every definition it evaluates the same parameter values."""

    def __init__(self, parameters: dict[str, Any], data_source: BundleDataSource) -> None:
        """Hold the parameters every evaluation reads, over one data source."""
        super().__init__(data_source=data_source)
        self.parameters = parameters

    def evaluate_definition(
        self,
        definition_name: str,
        resource: ResourceInput | None = None,
        parameters: dict[str, Any] | None = None,
        library: CQLLibrary | None = None,
    ) -> Any:
        """Evaluate one definition with the held parameters, any given here taking precedence."""
        return super().evaluate_definition(
            definition_name, resource=resource, parameters={**self.parameters, **(parameters or {})}, library=library
        )


class Program:
    """One compiled library, what it is asked for, and the most its answer may run to."""

    def __init__(self, compiled: dict[str, Any]) -> None:
        """Compile the library once, so a bad one is refused at compile."""
        self.kind: str = compiled["kind"]
        self.source: str = compiled["library"]
        self.parameters: dict[str, Any] = compiled["parameters"]
        self.definitions: list[str] | None = compiled["definitions"]
        self.scoring: str = compiled["scoring"]
        self.max_output: int = compiled["max_output"]
        self.too_large: str = compiled["too_large"]
        self.library = CQLEvaluator().compile(self.source)
        missing = sorted(set(self.definitions or []) - set(self.library.definitions))
        if missing:
            raise CQLError(f"the library defines no {missing[0]}")

    def run(self, value: Any) -> dict[str, Any]:
        """Evaluate over the resources handed in, and answer one JSON object."""
        source = _source(value)
        answer = self._cql(source) if self.kind == "cql" else self._measure(source)
        if len(json.dumps(answer).encode()) > self.max_output:
            raise ValueError(self.too_large)
        return answer

    def _values(self, evaluator: CQLEvaluator, resource: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
        """Every definition asked for, evaluated in one context, and the ones that failed."""
        values: dict[str, Any] = {}
        failed: list[str] = []
        for name, result in evaluator.evaluate_all_definitions(
            resource=resource, parameters=self.parameters, library=self.library
        ).items():
            if self.definitions is not None and name not in self.definitions:
                continue
            if isinstance(result, CQLError):
                failed.append(str(result))
            else:
                values[name] = plain(result)
        return values, failed

    def _cql(self, source: BundleDataSource) -> dict[str, Any]:
        """A library's definitions: once per patient in the Patient context, once otherwise."""
        evaluator = CQLEvaluator(data_source=source)
        if "Patient" not in self.library.contexts:
            values, failed = self._values(evaluator, None)
            return {"context": "Unfiltered", "values": values, "patients": [], "errors": failed}
        patients: list[dict[str, Any]] = []
        errors: list[str] = []
        for patient in _patients(source):
            values, failed = self._values(evaluator, patient)
            patients.append({"patient": f"Patient/{patient.get('id')}", "values": values})
            errors.extend(f"Patient/{patient.get('id')}: {said}" for said in failed)
        return {"context": "Patient", "values": {}, "patients": patients, "errors": errors}

    def _measure(self, source: BundleDataSource) -> dict[str, Any]:
        """Each patient's membership by the engine, counted by the population rules of the scoring."""
        evaluator = MeasureEvaluator(cql_evaluator=_Parameterised(self.parameters, source))
        evaluator.load_measure(self.source)
        counts = dict.fromkeys(POPULATIONS, 0)
        members: dict[str, list[str]] = {code: [] for code in POPULATIONS}
        declared: set[str] = set()
        errors: list[str] = []
        for patient in _patients(source):
            result = evaluator.evaluate_patient(patient, source)
            errors.extend(error.describe() for error in result.errors)
            said = result.populations_for("default")
            declared.update(said)
            for code in _membership(said, self.scoring):
                counts[code] += 1
                members[code].append(f"Patient/{patient.get('id')}")
        reported = [code for code in POPULATIONS if code in declared]
        return {"populations": {code: counts[code] for code in reported}, "members": members, "errors": errors}


def _membership(said: dict[str, bool], scoring: str) -> list[str]:
    """The populations one patient belongs to: what the library said, nested the way the scoring nests them."""
    initial = said.get("initial-population", False)
    if scoring == "cohort":
        return ["initial-population"] if initial else []
    if scoring == "ratio":
        denominator = initial and said.get("denominator", initial)
        numerator = initial and said.get("numerator", False)
        member = {
            "initial-population": initial,
            "denominator": denominator,
            "denominator-exclusion": denominator and said.get("denominator-exclusion", False),
            "numerator": numerator,
            "numerator-exclusion": numerator and said.get("numerator-exclusion", False),
        }
        return [code for code, kept in member.items() if kept]
    denominator = initial and said.get("denominator", initial)
    excluded = denominator and said.get("denominator-exclusion", False)
    numerator = denominator and not excluded and said.get("numerator", False)
    member = {
        "initial-population": initial,
        "denominator": denominator,
        "denominator-exclusion": excluded,
        "denominator-exception": denominator
        and not excluded
        and not numerator
        and said.get("denominator-exception", False),
        "numerator": numerator,
        "numerator-exclusion": numerator and said.get("numerator-exclusion", False),
    }
    return [code for code, kept in member.items() if kept]


def answer(programs: dict[str, Program], request: dict[str, Any]) -> dict[str, Any]:
    """Compile a library under its id, run the one an id names, or release it."""
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
                return {"error": f"no library is compiled under {request['id']}"}
            return {"outputs": [program.run(request["value"])]}
    except RecursionError:
        return {"error": "the library nests deeper than the evaluator can follow"}
    except Exception as error:
        return {"error": str(error).strip() or type(error).__name__}
    return {"error": f"{kind} is not a request kind: compile, run, or forget"}


def main() -> None:
    """Answer one request at a time until the pipe closes, holding the libraries compiled so far."""
    out = sys.stdout.buffer
    programs: dict[str, Program] = {}
    for line in sys.stdin.buffer:
        reply = answer(programs, json.loads(line, parse_float=Decimal))
        out.write(json.dumps(reply, default=plain).encode() + b"\n")
        out.flush()


if __name__ == "__main__":
    main()
