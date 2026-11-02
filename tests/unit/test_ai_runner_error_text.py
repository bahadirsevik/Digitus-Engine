"""Saglayici hatasinin metni kullaniciya ulasir (ws79 run 96: 'saglayici 2 denemede de
hata verdi' mesaji nedeni gizliyordu)."""
import pytest

from app.core.engine import ai_runner as AI


class _BoomAI:
    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt, **kwargs):
        self.calls += 1
        raise ConnectionError("getaddrinfo failed: generativelanguage.googleapis.com")


def test_run_single_message_carries_last_provider_error():
    ai = _BoomAI()
    with pytest.raises(AI.AiStageError) as exc:
        AI.run_single(ai, stage="family_a1", model="m", thinking_level="low",
                      prompt="p", schema={"type": "object"}, max_tokens=10,
                      result_key="families", max_attempts=2)
    msg = str(exc.value)
    assert msg.startswith("family_a1: saglayici 2 denemede de hata verdi — DEVAM EDILMEDI")
    assert "ConnectionError: getaddrinfo failed" in msg
    assert ai.calls == 2
    assert isinstance(exc.value.__cause__, ConnectionError)
