import pytest

from app.speech import spell_phone_numbers_text
from app.tools import readback_unanswered


@pytest.mark.parametrize(
    "text,expected",
    [
        ("I have 91827 36450. Is that right?", "I have nine one eight two seven, three six four five zero. Is that right?"),
        ("Your number is 9182736450?", "Your number is nine one eight two seven, three six four five zero?"),
        ("+91 98765-43210, correct?", "nine eight seven six five, four three two one zero, correct?"),
        # not phone numbers: left alone
        ("Room 112 at 10:30 AM", "Room 112 at 10:30 AM"),
        ("Reference 12345", "Reference 12345"),
    ],
)
def test_spell_phone_numbers(text, expected):
    assert spell_phone_numbers_text(text) == expected


def test_guard_still_sees_digit_readback():
    args = {"phone_number": "9182736450"}
    assert readback_unanswered("book_appointment", args, "I have 91827 36450. Is that right?")


@pytest.mark.parametrize("text", ["No, that's all. Thank you. Bye.", "Goodbye!", "no thanks", "That's it, thanks", "I'm done"])
def test_goodbyes(text):
    from app.tools import caller_said_goodbye
    assert caller_said_goodbye(text)


@pytest.mark.parametrize("text", ["Okay.", "Thanks.", "Alright", "Yes, that's right.", "Hmm"])
def test_not_goodbyes(text):
    from app.tools import caller_said_goodbye
    assert not caller_said_goodbye(text)
