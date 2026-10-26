from esim_bot.channels.base import Button
from esim_bot.channels.telegram import _markup, parse_update


def message(text, chat_type="private"):
    return {"update_id": 1, "message": {"message_id": 5, "chat": {"id": 42, "type": chat_type}, "text": text}}


def test_parse_start_and_text():
    start = parse_update(message("/start"))
    assert start.kind == "start" and start.chat_id == "42"
    txt = parse_update(message("  Японд явна  "))
    assert txt.kind == "text" and txt.value == "Японд явна"


def test_parse_callback():
    update = {"callback_query": {"id": "cb1", "data": "c:JP", "message": {"chat": {"id": 42, "type": "private"}}}}
    incoming = parse_update(update)
    assert (incoming.kind, incoming.chat_id, incoming.value, incoming.callback_id) == ("button", "42", "c:JP", "cb1")


def test_ignores_groups_and_non_text():
    assert parse_update(message("hi", chat_type="group")) is None
    assert parse_update({"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "photo": []}}) is None


def test_markup():
    markup = _markup([[Button("Япон", data="c:JP")], [Button("Төлөх", url="https://s.qpay.mn/x")]])
    assert markup == {
        "inline_keyboard": [
            [{"text": "Япон", "callback_data": "c:JP"}],
            [{"text": "Төлөх", "url": "https://s.qpay.mn/x"}],
        ]
    }
    assert _markup(None) is None
