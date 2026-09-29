"""Motor v3 ortak AI cagri katmani — K17 tekrar sozlesmesi.

plan_algoritma_entegrasyonu.md K17: uretimde her LLM asamasi TEK GECISTIR.
Golden fixture'lardaki uc tekrar olcum artefaktidir, uretime TASINMAZ.
Yalniz kilitli teknik tekrar kurallari uygulanir:

  * parse / batch / SAGLAYICI hatasinda TOPLAM en fazla `MAX_ATTEMPTS`
    (= 2) deneme; ikinci hatadan sonra enjekte edilen `error_cls`, son hata
    `__cause__` olacak sekilde yukseltilir,
  * BUTCE TAVANI hatasi (`BudgetExceeded`) ASLA tekrar EDILMEZ — dogrudan
    yeniden yukseltilir (tekrar denemek tavani asan harcamayi surdururdu),
  * eksik ID'ler icin YALNIZ eksik kumesine `TARGETED_RETRIES` (= 1) hedefli
    tekrar,
  * fazladan / bilinmeyen ID YAPISAL HATADIR (sessizce yok sayilmaz),
  * tekrardan sonra hala eksik ID varsa HATA firlatilir: eksik kelimeye
    `UNMATCHED` yazip checkpoint OLUSTURULMAZ.

Bu katman saglayici istemcisini KURMAZ; cagiran taraf enjekte eder. Boylece
testler ucretli cagri yapmadan butun tekrar topolojisini kosabilir.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from app.core.telemetry.ai_cost_budget import BudgetExceeded

MAX_ATTEMPTS = 2          # parse/batch hatasi icin TOPLAM deneme
TARGETED_RETRIES = 1      # eksik ID kumesi icin hedefli tekrar

# ── hata sinifi -> bekleme (plan_engine_paralellik.md §2) ───────────────────
# DENEME SAYISI ARTMAZ (hala MAX_ATTEMPTS); yalniz denemeler ARASINA bekleme
# girer. Paralellikte beklemesiz tekrar, 429 firtinasini buyutur.
RATE_LIMIT_WAIT_SECONDS = 2.0      # Retry-After yoksa taban bekleme
RATE_LIMIT_WAIT_MAX_SECONDS = 30.0  # Retry-After bunu asarsa kirpilir
_RATE_LIMIT_MARKS = ("429", "resource_exhausted", "resourceexhausted",
                     "rate limit", "ratelimit", "quota", "503", "unavailable",
                     "timeout", "deadline")
# Tekrar denemenin ANLAMSIZ oldugu hatalar: istek/kimlik hatalidir.
_FATAL_MARKS = ("api key not valid", "permission denied", "permissiondenied",
                "unauthenticated", "invalid argument", "invalidargument",
                "400 bad request", "401", "403")


def _sleep(seconds: float) -> None:
    """Testlerde monkeypatch edilebilsin diye ayri fonksiyon."""
    import time

    time.sleep(seconds)


def _retry_after_seconds(exc: BaseException) -> Optional[float]:
    """Saglayici `Retry-After` verdiyse saniye cinsinden doner."""
    for attr in ("retry_after", "retry_delay"):
        value = getattr(exc, attr, None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        raw = headers.get("Retry-After") if headers else None
        return float(raw) if raw is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def classify_provider_error(exc: BaseException) -> str:
    """`fatal` (tekrar yok) | `rate_limit` (bekle, tekrar) | `transient`."""
    text = f"{type(exc).__name__} {exc}".lower()
    if any(mark in text for mark in _FATAL_MARKS):
        return "fatal"
    if any(mark in text for mark in _RATE_LIMIT_MARKS):
        return "rate_limit"
    return "transient"


def _wait_before_retry(exc: BaseException) -> None:
    """Fatal hatada YUKSELTIR; rate-limit/timeout'ta bekler; digerinde beklemez."""
    import random

    kind = classify_provider_error(exc)
    if kind == "fatal":
        raise exc
    if kind != "rate_limit":
        return                       # parse/gecici: bugunku davranis, bekleme yok
    delay = _retry_after_seconds(exc)
    if delay is None:
        delay = RATE_LIMIT_WAIT_SECONDS * (1.0 + random.random() * 0.5)  # jitter
    _sleep(min(float(delay), RATE_LIMIT_WAIT_MAX_SECONDS))


class AiStageError(RuntimeError):
    """AI asamasi sozlesmeyi karsilamadi — yarim sonuc YAZILMAZ."""


def _scoped(ai: Any, stage: str, model: str, thinking_level: str) -> Any:
    if hasattr(ai, "for_stage"):
        return ai.for_stage(stage, model=model, thinking_level=thinking_level)
    return ai


def _parse(raw: Any, result_key: Optional[str]) -> Optional[List[Any]]:
    """Ham cevaptan oge listesi; kurtarilamazsa None.

    `result_key=None` ise cevabin KENDISI ust duzey bir DIZIDIR (SOCIAL V4/V5
    semalari boyledir); aksi halde `{result_key: [...]}` sarmali beklenir.

    `parse_ai_json_object` hicbir sey kurtaramadiginda `AIJsonParseError`
    FIRLATIR (sessiz eksik-veri uretmemek icin). Burada bu istisna NORMAL
    kirpilma sayilir ve `None`a cevrilir ki cagiran taraf kendi tekrar
    sozlesmesini (MAX_ATTEMPTS) uygulayabilsin; aksi halde kirpik bir cevap
    ilk denemede, ham istisna turuyle disari sizardi.
    """
    from app.core.channel.ai_json import (
        AIJsonParseError, parse_ai_json_list, parse_ai_json_object,
    )

    if result_key is None:
        try:
            items = parse_ai_json_list(raw, result_keys=())
        except AIJsonParseError:
            return None
        return items if isinstance(items, list) else None
    try:
        parsed = parse_ai_json_object(raw)
    except AIJsonParseError:
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get(result_key)
    return value if isinstance(value, list) else None


def _complete(scoped: Any, prompt: str, max_tokens: int,
              schema: Mapping[str, Any]) -> Any:
    """Tek saglayici cagrisi.

    DIKKAT: butce tavani korumasi BURADA OLAMAZ — buradaki bir
    `except BudgetExceeded: raise` ETKISIZDIR, cunku cagiranin genis
    `except Exception` blogu istisnayi yine yakalar ve tekrar dener.
    Koruma cagiranin except ZINCIRINDE, genel bloktan ONCE durur.
    """
    kwargs = {
        "max_tokens": max_tokens,
        "response_schema": dict(schema),
    }
    # Gemini 3.8 migration contract: sampling alanlari gonderilmez. Genel
    # AIService varsayilani (0.3) legacy modeller/icerik uretimi icin korunur;
    # yalniz model-pinned V3 motor wrapper'inda acikca None ile ezilir.
    from app.generators.ai_service import StageScopedAIService

    if (isinstance(scoped, StageScopedAIService)
            and getattr(scoped, "model", None) == "gemini-3.8-flash"):
        kwargs["temperature"] = None
    return scoped.complete_json(prompt, **kwargs)


def run_single(ai: Any, *, stage: str, model: str, thinking_level: str,
               prompt: str, schema: Mapping[str, Any], max_tokens: int,
               result_key: Optional[str],
               error_cls: type = AiStageError) -> List[Any]:
    """ID'siz asama (A1 / A2B): liste dondurur, kirpik cevapta tekrar dener."""
    scoped = _scoped(ai, stage, model, thinking_level)
    last_error: Optional[BaseException] = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = _complete(scoped, prompt, max_tokens, schema)
        except BudgetExceeded:            # TAVAN: tekrar YOK, sarmalama YOK
            raise
        except Exception as exc:          # saglayici/transport hatasi
            last_error = exc
            if attempt < MAX_ATTEMPTS:    # son denemeden sonra beklemek anlamsiz
                _wait_before_retry(exc)
            continue
        items = _parse(raw, result_key)
        if items is not None:
            return items
    message = (f"{stage}: cevap {MAX_ATTEMPTS} denemede de kirpik/bozuk "
               f"('{result_key}' listesi yok) — DEVAM EDILMEDI")
    if last_error is not None:
        raise error_cls(f"{stage}: saglayici {MAX_ATTEMPTS} denemede de hata "
                        "verdi — DEVAM EDILMEDI") from last_error
    raise error_cls(message)


def run_batch(ai: Any, *, stage: str, model: str, thinking_level: str,
              rows: Sequence[Mapping[str, Any]],
              build_prompt: Callable[[Sequence[Mapping[str, Any]]], str],
              schema: Mapping[str, Any], max_tokens: int,
              result_key: Optional[str] = "results",
              id_field: str = "keyword_id",
              response_id_field: str = "id",
              response_parser: Optional[
                  Callable[[Any, Sequence[int]], Mapping[str, Any]]
              ] = None,
              error_cls: type = AiStageError) -> Dict[int, Dict[str, Any]]:
    """ID bazli asama (A2 / A2C / A3) — eksiksiz kume donmezse HATA.

    Donen sozluk: beklenen HER ID icin bir cevap ogesi. Cagiran taraf bunu
    dogruladiktan sonra tek seferde kaydeder; bu fonksiyon DB'ye dokunmaz.
    """
    expected = [int(row[id_field]) for row in rows]
    collected: Dict[int, Dict[str, Any]] = {}

    def ask(subset: Sequence[Mapping[str, Any]]) -> None:
        scoped = _scoped(ai, stage, model, thinking_level)
        items: Optional[List[Any]] = None
        last_error: Optional[BaseException] = None
        last_validation: Optional[str] = None
        wanted = {int(row[id_field]) for row in subset}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw = _complete(scoped, build_prompt(subset), max_tokens,
                                schema)
            except BudgetExceeded:        # TAVAN: tekrar YOK, sarmalama YOK
                raise
            except Exception as exc:      # saglayici/transport hatasi
                last_error = exc
                if attempt < MAX_ATTEMPTS:
                    _wait_before_retry(exc)
                continue
            if response_parser is None:
                items = _parse(raw, result_key)
            else:
                parsed = response_parser(raw, sorted(wanted))
                structural = {
                    name: parsed.get(name)
                    for name in ("parse_error", "unknown_ids", "duplicate_ids",
                                 "out_of_range_ids", "malformed_ids")
                    if parsed.get(name)
                }
                if structural:
                    last_validation = repr(structural)
                    items = None
                    continue
                canonical = parsed.get("by_id")
                if not isinstance(canonical, Mapping):
                    last_validation = "by_id sozlugu yok"
                    items = None
                    continue
                items = []
                for raw_id, payload in canonical.items():
                    if not isinstance(payload, Mapping):
                        last_validation = f"{raw_id}: kanonik payload sozluk degil"
                        items = None
                        break
                    item = dict(payload)
                    item.setdefault(response_id_field, int(raw_id))
                    items.append(item)
            if items is not None:
                break
        if items is None:
            if last_validation is not None:
                raise error_cls(
                    f"{stage}: cevap {MAX_ATTEMPTS} denemede de yapisal "
                    f"dogrulamadan gecmedi ({last_validation}) — DEVAM EDILMEDI")
            if last_error is not None:
                raise error_cls(
                    f"{stage}: saglayici {MAX_ATTEMPTS} denemede de hata "
                    "verdi — DEVAM EDILMEDI") from last_error
            raise error_cls(
                f"{stage}: cevap {MAX_ATTEMPTS} denemede de kirpik/bozuk "
                f"('{result_key}' listesi yok) — DEVAM EDILMEDI")

        seen_here: set = set()
        for item in items:
            if not isinstance(item, Mapping) or response_id_field not in item:
                continue
            try:
                ident = int(item[response_id_field])
            except (TypeError, ValueError):
                raise error_cls(
                    f"{stage}: cevapta sayisal olmayan id: "
                    f"{item.get(response_id_field)!r}")
            if ident not in wanted:
                # Fazladan / bilinmeyen ID: sessizce yok SAYILMAZ.
                raise error_cls(
                    f"{stage}: cevapta istenmeyen id {ident} — yapisal hata")
            if ident in seen_here:
                # Ayni cevapta TEKRAR EDEN id: sessizce "sonuncu kazanir"
                # YAPILMAZ; hangi kaydin dogru oldugu bilinemez.
                raise error_cls(
                    f"{stage}: cevapta tekrar eden id {ident} — yapisal hata")
            seen_here.add(ident)
            collected[ident] = dict(item)

    ask(rows)
    missing = [ident for ident in expected if ident not in collected]
    for _ in range(TARGETED_RETRIES):
        if not missing:
            break
        # YALNIZ eksik kumesi tekrar sorulur (hedefli tekrar).
        ask([row for row in rows if int(row[id_field]) in set(missing)])
        missing = [ident for ident in expected if ident not in collected]

    if missing:
        raise error_cls(
            f"{stage}: hedefli tekrara ragmen {len(missing)} ID eksik "
            f"(ornek: {missing[:5]}) — UNMATCHED yazilmaz, checkpoint "
            "olusturulmaz")
    return collected


# ── paralel yurutucu (plan_engine_paralellik.md §2) ──────────────────────────
#
# Sozlesme:
#   * Isler YALNIZ saglayici cagrisi + dogrulama yapar; DB'ye DOKUNMAZ.
#     SQLAlchemy Session worker thread'ine ASLA gecmez.
#   * Sonuclar cagirana GONDERIM SIRASINDA verilir (deterministik); yazim ve
#     checkpoint cagiranin (ana thread) sorumlulugudur.
#   * `concurrency <= 1` iken kod bugunku SERI yolu birebir kosar.
#   * Ilk olumcul hatada: yeni is baslatilmaz, baslamamis isler iptal edilir,
#     UCUSTAKI isler BEKLENIR (drain) ve basarili sonuclari cagirana verilir;
#     ancak bundan SONRA hata yukseltilir. Ucreti odenmis sonuc COPE ATILMAZ.
#   * Global Redis slotu (tum V3 kosularinin paylastigi) is BASINA alinir:
#     ilk cagri + bekleme + tekrar + hedefli tekrar ayni slotta gecer.

def _global_slot_acquirer():
    """Tum V3 kosularinin paylastigi Redis inflight limiti.

    Redis yoksa `None` doner ve cagiran SERI yola duser — sinirsiz paralel
    cagri YAPILMAZ (limitsiz hizlanma yerine bugunku davranis).
    """
    from app.config import settings

    limit = int(getattr(settings, "ENGINE_AI_GLOBAL_INFLIGHT", 0) or 0)
    if limit < 1:
        return None
    try:
        import redis as redis_lib

        from app.core.screening.inflight import RedisInflightLimiter

        client = redis_lib.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        # `from_url` BAGLANMAZ (tembel istemci): servis kapaliysa hata ancak
        # worker icinde ilk slot isteginde cikar ve kosu duserdi. Baslangicta
        # KISA timeout'lu ping ile gercekten erisilebilir oldugu dogrulanir.
        client.ping()
        limiter = RedisInflightLimiter(client, limit=limit,
                                       key="engine_v3:inflight:leases")
    except Exception:  # noqa: BLE001 — Redis yok/bozuk: seri yola dusulur
        return None
    return limiter.slot


def engine_concurrency() -> int:
    """Tek kosunun thread sayisi; 1 = bugunku seri yol."""
    from app.config import settings

    return max(1, int(getattr(settings, "ENGINE_AI_CONCURRENCY", 1) or 1))


def run_jobs(jobs: Sequence[Callable[[], Any]],
             persist: Callable[[Any], None], *,
             concurrency: Optional[int] = None) -> None:
    """Isleri kosar ve her sonucu ANA THREAD'de `persist` ile yazdirir.

    * Sonuclar GONDERIM SIRASINDA yazilir (deterministik).
    * `persist` her zaman cagiranin thread'inde calisir -> DB oturumu guvenli.
    * Seri yolda (concurrency<=1) her is bittiginde HEMEN yazilir; bugunku
      checkpoint tanesi birebir korunur.
    * Paralelde ilk olumcul hatada: yeni is baslatilmaz, baslamamislar iptal
      edilir, UCUSTAKILER beklenir ve basarili sonuclari YAZILIR (drain);
      ancak ondan sonra ilk hata yukseltilir.
    """
    jobs = list(jobs)
    if not jobs:
        return
    width = engine_concurrency() if concurrency is None else int(concurrency)

    if width <= 1:
        for job in jobs:
            persist(job())
        return

    acquire = _global_slot_acquirer()
    if acquire is None:                      # Redis yok -> seri (fail-safe)
        for job in jobs:
            persist(job())
        return

    from collections import deque
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    def _guarded(job: Callable[[], Any]) -> Any:
        with acquire():
            return job()

    queue = deque(enumerate(jobs))
    done: Dict[int, Any] = {}
    next_index = 0
    first_error: Optional[BaseException] = None

    with ThreadPoolExecutor(max_workers=width) as pool:
        inflight: Dict[Any, int] = {}
        while queue or inflight:
            # ROLLING GONDERIM: ayni anda en fazla `width` is UCUSTA olur;
            # ilk olumcul hatadan SONRA yeni is HIC gonderilmez.
            while queue and len(inflight) < width and first_error is None:
                index, job = queue.popleft()
                inflight[pool.submit(_guarded, job)] = index
            if not inflight:
                break                        # hata var, kuyruk bosaltilmadi
            finished, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
            for future in finished:
                index = inflight.pop(future)
                try:
                    done[index] = future.result()
                except Exception as exc:     # BaseException DEGIL: Ctrl-C /
                    if first_error is None:  # SystemExit normal hata sayilmaz
                        first_error = exc
            while next_index in done:        # ANA THREAD, GONDERIM SIRASI
                persist(done.pop(next_index))
                next_index += 1

    # DRAIN: hatadan sonra tamamlanan isler ucreti odenmis sonuclardir —
    # bosluk (patlayan is) olsa bile sirayla YAZILIR, cope atilmaz.
    for index in sorted(done):
        persist(done[index])
    if first_error is not None:
        raise first_error
