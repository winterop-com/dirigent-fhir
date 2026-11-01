"""The block reference is kept by hand in this pack, so it is held to what the catalog publishes.

dirigent renders its own page from the installed catalog and fails the build when the file and
the catalog disagree. The pack has no renderer of its own -- ``dirigent-core`` is not a
dependency here -- so this is the half a pack can check without one: every field a block
publishes says what it is, and what it says is on the page in the spelling a table cell renders
it in. A field added to a block, or a docstring rewritten, therefore fails here rather than
drifting out of the page quietly.
"""

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel

from dirigent_common import HumaneJsonSchema, as_markdown
from dirigent_fhir import FhirPlugin
from dirigent_plugin import AnyOperator, AnySensor

#: The block reference, which lives beside the package rather than inside it.
PAGE = Path(__file__).resolve().parents[1] / "docs" / "blocks.md"

#: Every block the pack contributes, operators first, the way the page lists them.
CONTRIBUTION = FhirPlugin().contribute()
BLOCKS: list[AnyOperator | AnySensor] = [*CONTRIBUTION.operators, *CONTRIBUTION.sensors]


def _fields(model: type[BaseModel]) -> dict[str, Any]:
    """Every field one model publishes into the catalog, keyed by name."""
    schema = model.model_json_schema(mode="serialization", schema_generator=HumaneJsonSchema)
    return cast("dict[str, Any]", schema.get("properties", {}))


def _published() -> list[Any]:
    """Each block's config and output fields, one parameter case each."""
    cases: list[Any] = []
    for block in BLOCKS:
        for table, model in (("config", block.config_model), ("output", block.output_model)):
            for name, field in _fields(model).items():
                description = cast("dict[str, Any]", field).get("description")
                cases.append(pytest.param(description, id=f"{block.spec.id}-{table}-{name}"))
    return cases


PUBLISHED = _published()


def _in_a_table_cell(description: str) -> str:
    """Render a description the way the page's table renders it: its first paragraph, on one line.

    The rest of a docstring is the rationale a reader of the code wants and a table cell has no
    room for, which is why the page carries the first paragraph only.
    """
    paragraph = description.strip().split("\n\n", 1)[0]
    return as_markdown(" ".join(paragraph.split())).replace("|", "\\|")


def test_there_are_published_fields_to_check() -> None:
    assert len(PUBLISHED) > 30


@pytest.mark.parametrize("description", PUBLISHED)
def test_every_published_field_says_what_it_is(description: str | None) -> None:
    """A field with no docstring reaches the catalog, the UI's step form and the page undescribed."""
    assert description, "the field has no docstring"


@pytest.mark.parametrize("description", PUBLISHED)
def test_every_published_field_is_described_on_the_blocks_page(description: str | None) -> None:
    assert description
    assert _in_a_table_cell(description) in PAGE.read_text(), "the page does not carry what the docstring says"
