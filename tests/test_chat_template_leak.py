"""Служебная разметка чат-шаблона не должна доезжать до лида."""
from app.modules.conversation.delivery import _split_bubbles, _strip_chat_template

LEAKED = (
    "Sistem lengkap kayak yang Kakak mau itu nanti di kursus 4 bulan.<|im_end|>\n"
    "<|im_start|>assistant\n"
    "Demo Event-nya Sabtu, 8 Agustus 2026 jam 09:00-12:00 di Menara Sudirman.<|im_end|>"
)


def test_markup_is_stripped_with_its_role_word() -> None:
    out = _strip_chat_template(LEAKED)
    assert "<|" not in out and "|>" not in out
    # именно роль, а не только маркер: голое "assistant" в начале пузыря читается хуже разметки
    assert "assistant" not in out
    assert "Menara Sudirman" in out and "4 bulan" in out


def test_llama_and_mistral_markers_too() -> None:
    assert _strip_chat_template("halo<|eot_id|>") == "halo"
    assert _strip_chat_template("[INST] halo [/INST]").strip() == "halo"


def test_ordinary_reply_is_untouched() -> None:
    plain = "Biayanya Rp 1.882.955, bisa dicicil ya Kak.|||Mau aku daftarin?"
    assert _strip_chat_template(plain) == plain


def test_bubbles_never_carry_markup() -> None:
    bubbles = _split_bubbles(LEAKED.replace("<|im_start|>assistant\n", "|||"))
    assert bubbles and all("<|" not in b for b in bubbles)
