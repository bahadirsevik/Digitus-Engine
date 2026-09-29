"""Expansion turları için HARD AI çağrı bütçesi.

Tahmin (`_estimate_expansion_batches`) yalnızca pencere BOYUTU seçiminde
kullanılır; sınırı bu bütçe korur: her `complete_json` çağrısından ÖNCE
`try_consume()` çağrılır. Bütçe bitince her katman KENDİ fallback
semantiğini uygular (intent → kanal fallback intent'i; brand filter →
fail-safe brand-relevant; ADS/SOCIAL prefilter → karantina; SEO → intent
fallback ile geçer). AI kullanmayan deterministik adımlar (SEO fiyat
filtresi) bütçeden bağımsız HER ZAMAN çalışır.

İlk pipeline (expansion dışı) bütçesizdir (budget=None → sınırsız).
"""
import threading


class AiBudgetExhausted(RuntimeError):
    """Expansion AI bütçesi tükendi — katman kendi fallback'ine düşmeli."""


class AiCallBudget:
    """Thread-safe azalan sayaç (intent katmanı paralel thread kullanıyor)."""

    def __init__(self, limit: int):
        self.limit = max(0, int(limit))
        self._consumed = 0
        self._lock = threading.Lock()

    def try_consume(self, calls: int = 1) -> bool:
        """calls kadar çağrı hakkı ayır; bütçe yetmiyorsa False (tüketmez)."""
        with self._lock:
            if self._consumed + calls > self.limit:
                return False
            self._consumed += calls
            return True

    @property
    def consumed(self) -> int:
        return self._consumed

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self._consumed)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0
