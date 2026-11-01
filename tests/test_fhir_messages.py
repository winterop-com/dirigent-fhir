"""The pack's catalogue: every refusal it can make is minted under the ``fhir`` prefix."""

from pathlib import Path

import pytest

from dirigent_common import Message
from dirigent_fhir import messages
from dirigent_fhir.messages import FHIR

MESSAGES = [pytest.param(message, id=name) for name, message in FHIR.messages.items()]

#: The block reference, which publishes the codes a reader selects a refusal by.
PAGE = Path(__file__).resolve().parents[1] / "docs" / "blocks.md"


def test_the_catalogue_holds_every_refusal_the_pack_makes() -> None:
    assert FHIR.prefix == "fhir"
    assert len(FHIR.messages) == len(MESSAGES) > 10


@pytest.mark.parametrize("message", MESSAGES)
def test_a_message_is_coded_under_the_pack_prefix(message: Message) -> None:
    assert message.code.startswith("fhir.")


def test_no_two_messages_share_a_code() -> None:
    codes = [message.code for message in FHIR.messages.values()]
    assert len(set(codes)) == len(codes)


def test_every_message_is_exported_under_its_own_constant() -> None:
    """A raise site names the constant, so a message no constant holds is unreachable."""
    held = {message.code for message in vars(messages).values() if isinstance(message, Message)}
    assert held == {message.code for message in FHIR.messages.values()}


@pytest.mark.parametrize("message", MESSAGES)
def test_a_message_renders_every_param_its_template_names(message: Message) -> None:
    rendered = message.render(**{field: field for field in message.fields})
    assert "{" not in rendered
    for field in message.fields:
        assert field in rendered


@pytest.mark.parametrize("message", MESSAGES)
def test_the_page_publishes_the_message_under_its_code(message: Message) -> None:
    assert f"| `{message.code}` | `{message.text}` |" in PAGE.read_text()
