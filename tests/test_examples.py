"""The pack's example shelf conforms to its own contribution, checked by the kit."""

import re
from pathlib import Path
from typing import Any, Final, cast

import yaml

from dirigent_fhir import FhirPlugin
from dirigent_testing import check_pack_examples

#: The shelves root, taken from the hook the pack ships them through.
SHELVES = Path(str(FhirPlugin().examples()[0]))

#: The one shelf, every document on it built from the pack's own blocks.
EXAMPLES_DIR = SHELVES / "fhir"

#: The most a description's first sentence may run to, which is what a list row has room for.
FIRST_SENTENCE_AT_MOST: Final = 80


def test_the_shelves_the_hook_answers_with_are_the_ones_in_the_package() -> None:
    assert SHELVES.is_dir()
    assert SHELVES.name == "shelves" and SHELVES.parent.name == "dirigent_fhir"


def test_every_example_conforms_to_the_pack_catalog() -> None:
    assert list(EXAMPLES_DIR.glob("*.yaml"))
    issues = check_pack_examples(FhirPlugin().contribute(), EXAMPLES_DIR)
    assert issues == [], "\n".join(issues)


def test_every_description_opens_with_a_short_plain_sentence() -> None:
    for path in sorted(SHELVES.rglob("*.yaml")):
        description = cast("dict[str, Any]", yaml.safe_load(path.read_text())).get("description")
        assert description, f"{path.name} says nothing about what it does"
        paragraph = " ".join(str(description).strip().split("\n\n", 1)[0].split())
        sentence = re.split(r"(?<=[.!?])\s", paragraph, maxsplit=1)[0]
        assert len(sentence) <= FIRST_SENTENCE_AT_MOST, f"{path.name}: {sentence}"
        assert "`" not in sentence, f"{path.name}: {sentence}"


def test_every_example_is_listed_in_the_shelf_readme() -> None:
    readme = (EXAMPLES_DIR / "README.md").read_text()
    for path in EXAMPLES_DIR.glob("*.yaml"):
        assert f"[{path.name}]({path.name})" in readme, path.name


def test_no_example_reaches_a_public_server() -> None:
    for path in EXAMPLES_DIR.glob("*.yaml"):
        document = cast("dict[str, Any]", yaml.safe_load(path.read_text()))
        for connection in cast("dict[str, Any]", document.get("connections", {})).values():
            assert cast("str", connection["config"]["base_url"]).startswith("http://localhost:"), path.name
