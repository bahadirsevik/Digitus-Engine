"""Çoklu-bağlam ensemble (deterministik post-processing) — Codex kilidi.

GEREKÇE: v3 zinciri (bağımsızlık talimatı + batch 30→15→10) flip
kapılarının üçünü de geçirdi ama aday-kümesi Jaccard'ı kaldı. Kalan
sorun `2↔1` oynaklığı (sıralama sinyali), dahil/hariç kararı değil.
Çözüm: aynı kelimeyi İKİ TAMAMLAYICI BAĞLAMDA değerlendirip birleştirmek.

SÖZLEŞME (kilitli):
- Prompt v3a, batch 10, temperature 0, uzun şema — DEĞİŞMEZ.
- Yalnız deterministik post-processing eklenir; yeni PROMPT_VERSION YOK.
- Ayrı `ENSEMBLE_CONTRACT_VERSION` manifest'te saklanır.
- Sıralama sinyali: iki görünümün fit ORTALAMASI.
- ELEME: yalnızca İKİ görünüm de `fit=0` derse (güvenlik ilkesi korunur).
- Eşitlik bozucu: relevance → ham skor → keyword_id.
- Ham kararlar + birleşik karar + `context_disagreement` saklanır.
- Tek görünüm unresolved → çözülmüş görünüm kullanılır.
- İki görünüm de unresolved → kelime aday KALIR (elenmez) ama yüksek
  sıralama ALMAZ: `uncertain=True` ile geçenlerin SONUNA yerleşir.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

ENSEMBLE_CONTRACT_VERSION = "ENS-2026-07-27-v1"
CHANNELS = ("ads", "seo", "social")
CHANNEL_KEYS = {"ads": "ADS", "seo": "SEO", "social": "SOCIAL"}


def complementary_plans(universe: Sequence[Dict[str, Any]], batch_size: int,
                        seed: int) -> tuple:
    """İki TAMAMLAYICI deterministik plan.

    Görünüm A: kanonik (keyword_id artan, sıralı kesme).
    Görünüm B: A'nın sütun-karışımı — her kelime aynı KONUM indeksinde
    kalır ama batch ARKADAŞLARI tamamen değişir. Böylece iki görünüm
    birbirinin bağlam-körlüğünü tamamlar.
    """
    ordered = sorted(universe, key=lambda k: k["id"])
    plan_a = [ordered[i:i + batch_size]
              for i in range(0, len(ordered), batch_size)]
    rng = random.Random(seed)
    max_len = max(len(b) for b in plan_a)
    plan_b: List[List[Dict[str, Any]]] = [[] for _ in plan_a]
    for p in range(max_len):
        column = [b[p] for b in plan_a if len(b) > p]
        rng.shuffle(column)
        idx = 0
        for bi, b in enumerate(plan_a):
            if len(b) > p:
                plan_b[bi].append(column[idx])
                idx += 1
    return plan_a, plan_b


# ── Sticky (hash tabanlı) batch planı ────────────────────────────────────
# Codex: sıraya göre kesme, evrene keyword eklenince SONRAKİ TÜM batch
# sınırlarını kaydırır — yeni koşuda alakasız kelimelerin komşuları (ve
# dolayısıyla kararları) değişir. Hash tabanlı atamada bir kelimenin
# kovası YALNIZ kendi id'sine ve salt'a bağlıdır; evren değişimi diğer
# kelimeleri KAYDIRMAZ.
#
# Codex #2: kova kimliği EVREN BÜYÜKLÜĞÜNDEN BAĞIMSIZ olmalıdır.
# Önceki sürüm kova sayısını evrenden (kuantize) türetiyordu; kuantum
# sınırı aşıldığında TÜM atamalar yeniden dağılıyordu — yani "sticky"
# değildi. Artık SABİT sayıda sanal kova vardır; bir kelimenin kovası
# evren büyüse de küçülse de DEĞİŞMEZ. Kova içi bölme ise batch üst
# sınırını (`batch_size`) garanti eder.
#
# Kova sayısı OFFLINE ÖLÇÜMLE seçildi (evren 671, batch<=10, %5/%10 ekleme
# ve silme; ortak kelimeler arasında yeniden-bölünme oranı):
#   32 kova → %36-68 | 64 → %18-32 | 96 → %5-11 | 128 → %1.6-3.0
# 128'de ortalama kova (~5) batch sınırının altında kaldığı için bölünme
# neredeyse hiç olmaz; churn yalnız kendi kovasını etkiler. Bedeli daha
# çok/küçük istektir (671 evren: 132 batch, sıralı kesmede 68) — mutlak
# maliyet koşu başına ~$0.3 seviyesinde kalır. NOT: evren büyüdükçe kova
# başına düşen kelime artar ve bölünme tekrar devreye girer; bu tasarımın
# bilinen sınırıdır (çok daha büyük evrenlerde kova sayısı yeniden
# ölçülmelidir — sabit tutulursa yapışkanlık zayıflar).
VIRTUAL_BUCKETS = 128
PLAN_SALTS = ("scr-view-a", "scr-view-b")
MAX_STICKY_BATCH = 10


class BatchPlanError(ValueError):
    """Batch planı sözleşmeyi (üst sınır/tekillik) ihlal ediyor."""


def virtual_bucket(keyword_id: int, salt: str,
                   buckets: int = VIRTUAL_BUCKETS) -> int:
    """Kelimenin sanal kovası — yalnız (salt, id) fonksiyonu."""
    digest = hashlib.sha256(f"{salt}:{keyword_id}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % buckets


def _balanced_chunks(items: List[Any], limit: int) -> List[List[Any]]:
    """`items`'ı en fazla `limit` boyutlu, DENGELİ parçalara böler."""
    if not items:
        return []
    k = (len(items) + limit - 1) // limit
    base, extra = divmod(len(items), k)
    out, idx = [], 0
    for i in range(k):
        size = base + (1 if i < extra else 0)
        out.append(items[idx:idx + size])
        idx += size
    return out


def sticky_plan(universe: Sequence[Dict[str, Any]], batch_size: int,
                salt: str, buckets: int = VIRTUAL_BUCKETS
                ) -> List[List[Dict[str, Any]]]:
    """Deterministik, evren-değişimine DAYANIKLI batch planı.

    Sözleşme: (a) her batch <= `batch_size` (üretimde <= 10);
    (b) kelimenin sanal kovası evren büyüklüğünden bağımsızdır;
    (c) evren değişince yalnız ETKİLENEN kovanın bölünmesi değişir.
    """
    if batch_size < 1:
        raise BatchPlanError(f"batch_size >= 1 olmalı: {batch_size}")
    if batch_size > MAX_STICKY_BATCH:
        raise BatchPlanError(
            f"sticky batch üst sınırı {MAX_STICKY_BATCH}; verilen {batch_size} "
            f"(v3c sözleşmesi: komşuluk etkisi batch<=10'da ölçüldü)"
        )
    grouped: List[List[Dict[str, Any]]] = [[] for _ in range(buckets)]
    for kw in universe:
        grouped[virtual_bucket(kw["id"], salt, buckets)].append(kw)
    plan: List[List[Dict[str, Any]]] = []
    for bucket in grouped:
        # Kova içi sıra da hash'ten (deterministik, id sırasına bağlı değil)
        bucket.sort(key=lambda kw: hashlib.sha256(
            f"{salt}:pos:{kw['id']}".encode("utf-8")).hexdigest())
        plan.extend(_balanced_chunks(bucket, batch_size))
    return plan


def sticky_plans(universe: Sequence[Dict[str, Any]], batch_size: int,
                 salts: Sequence[str] = PLAN_SALTS,
                 buckets: int = VIRTUAL_BUCKETS
                 ) -> List[List[List[Dict[str, Any]]]]:
    """İki (veya N) tamamlayıcı sticky plan — üretim sözleşmesi."""
    return [sticky_plan(universe, batch_size, salt, buckets) for salt in salts]


def validate_batch_plan(plan: Sequence[Sequence[Dict[str, Any]]],
                        universe: Sequence[Dict[str, Any]],
                        max_batch: int) -> None:
    """Açık plan sözleşmesi: üst sınır + tam kapsama + tekillik."""
    oversize = [len(b) for b in plan if len(b) > max_batch]
    if oversize:
        raise BatchPlanError(
            f"batch üst sınırı {max_batch} aşıldı: {sorted(oversize)[-3:]}")
    seen = [kw["id"] for batch in plan for kw in batch]
    if len(seen) != len(set(seen)):
        raise BatchPlanError("batch planında yinelenen keyword var")
    if set(seen) != {kw["id"] for kw in universe}:
        raise BatchPlanError("batch planı evreni birebir kapsamıyor")


@dataclass
class EnsembleResult:
    keyword_id: int
    keyword: str
    mean_fit: Dict[str, Optional[float]]      # kanal → ortalama (sıralama)
    passing: Dict[str, bool]                  # kanal → aday mı (eleme kuralı)
    raw_views: Dict[str, Dict[str, Any]]      # görünüm → ham karar
    context_disagreement: Dict[str, bool]     # kanal → görünümler ayrıştı mı
    uncertain: bool = False                   # iki görünüm de unresolved
    unresolved_views: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "keyword_id": self.keyword_id,
            "keyword": self.keyword,
            "mean_fit": self.mean_fit,
            "passing": self.passing,
            "context_disagreement": self.context_disagreement,
            "uncertain": self.uncertain,
            "unresolved_views": self.unresolved_views,
            "raw_views": self.raw_views,
        }


def merge_multi_views(views: Dict[str, Sequence[Any]]) -> List[EnsembleResult]:
    """N GÖRÜNÜMÜ deterministik kurallarla birleştirir (2 veya 3 görünüm).

    - Sıralama sinyali: ÇÖZÜLMÜŞ görünümlerin fit ORTALAMASI.
      (3 görünümde 0, ⅓, ⅔, 1, 1⅓, 1⅔, 2 seviyeleri oluşur — `1↔2`
      ayrımının sıralama otoritesi doğal olarak yumuşar.)
    - ELEME: yalnızca TÜM çözülmüş görünümler `0` derse.
    - Tümü unresolved → aday KALIR, `uncertain=True`, mean=None
      (geçenlerin sonunda sıralanır).
    """
    if len(views) < 2:
        raise ValueError("ensemble en az iki görünüm ister")
    by_label = {label: {r.keyword_id: r for r in res}
                for label, res in views.items()}
    id_sets = [set(m) for m in by_label.values()]
    if any(s != id_sets[0] for s in id_sets[1:]):
        raise ValueError("ensemble görünümleri aynı evreni kapsamalı")

    merged: List[EnsembleResult] = []
    for kid in id_sets[0]:
        rows = {label: m[kid] for label, m in by_label.items()}
        unresolved_views = [label for label, r in rows.items()
                            if getattr(r, "unresolved", False)]
        resolved = [label for label in rows if label not in unresolved_views]
        all_unresolved = not resolved

        mean_fit: Dict[str, Optional[float]] = {}
        passing: Dict[str, bool] = {}
        disagreement: Dict[str, bool] = {}
        for ch in CHANNELS:
            if all_unresolved:
                mean_fit[ch] = None
                passing[ch] = True
                disagreement[ch] = False
                continue
            fits = [getattr(rows[label], f"{ch}_fit") for label in resolved]
            mean_fit[ch] = round(sum(fits) / len(fits), 6)
            passing[ch] = not all(f == 0 for f in fits)
            disagreement[ch] = len(set(fits)) > 1

        first = next(iter(rows.values()))
        merged.append(EnsembleResult(
            keyword_id=kid,
            keyword=first.keyword,
            mean_fit=mean_fit,
            passing=passing,
            raw_views={
                label: {ch: getattr(r, f"{ch}_fit") for ch in CHANNELS}
                for label, r in rows.items()
            },
            context_disagreement=disagreement,
            uncertain=all_unresolved,
            unresolved_views=sorted(unresolved_views),
        ))
    merged.sort(key=lambda r: r.keyword_id)
    return merged


def merge_views(view_a: Sequence[Any], view_b: Sequence[Any],
                *, label_a: str = "A", label_b: str = "B"
                ) -> List[EnsembleResult]:
    """İki görünüm için ince sarmalayıcı (geriye uyumlu)."""
    return merge_multi_views({label_a: view_a, label_b: view_b})


RANK_RULES = ("mean", "max", "any2")


def ensemble_ordering(merged: Sequence[EnsembleResult], channel: str, *,
                      raw_rank: Optional[Dict[int, int]] = None,
                      rank_rule: str = "mean") -> List[int]:
    """Sıralama: (geçiyor mu, -sıralama sinyali, ham skor rank, keyword_id).

    TIE-BREAK SÖZLEŞMESİ (Codex #6): `raw_rank → keyword_id`. `relevance`
    parametresi KALDIRILDI — Dijital ölçümünde relevance-first tie-break
    erişimi ADS 5→1 ve SEO 5→1'e düşürmüştü (ölçüm: ensemble_dijital.json,
    `tiebreak_variants`). Sözleşmenin yanlışlıkla geri gelmesini imkânsız
    kılmak için parametre kabul EDİLMEZ.

    `rank_rule` YALNIZCA sıralama sinyalini seçer; ELEME KURALI HER ÜÇÜNDE
    DE AYNIDIR (yalnız tüm çözülmüş görünümler 0 derse elenir):
      - "mean": çözülmüş görünümlerin ortalaması (kararlılık odaklı).
      - "max": en iyimser görünüm (erişim odaklı — bir görünüm "2" derse
        kelime öne çıkar; ortalama bunu 1.5'e indirip kesiğin altına
        itebiliyor).
      - "any2": önce "herhangi bir görünüm 2 dedi mi", sonra ortalama.

    Belirsiz (tüm görünümler unresolved) kelimeler GEÇENLERİN SONUNDA yer
    alır: elenmezler ama yüksek öncelik almazlar.
    """
    if rank_rule not in RANK_RULES:
        raise ValueError(f"bilinmeyen rank_rule: {rank_rule}")
    rank = raw_rank or {}

    def signal(r: EnsembleResult):
        mf = r.mean_fit[channel]
        if mf is None:
            return None
        if rank_rule == "mean":
            return mf
        fits = [v[channel] for label, v in r.raw_views.items()
                if label not in r.unresolved_views]
        if rank_rule == "max":
            return max(fits) if fits else mf
        return (1 if any(f == 2 for f in fits) else 0, mf)

    def key(r: EnsembleResult):
        sig = signal(r)
        # Belirsizler: geçenlerin altında, elenenlerin üstünde
        if sig is None:
            primary = (0.5,) if rank_rule != "any2" else (0, 0.5)
        elif rank_rule == "any2":
            primary = (-sig[0], -sig[1])
        else:
            primary = (-sig,)
        return (
            0 if r.passing[channel] else 1,   # geçenler önce
            *primary,
            rank.get(r.keyword_id, 10 ** 9),
            r.keyword_id,
        )

    return [r.keyword_id for r in sorted(merged, key=key)]


def ensemble_flip_rate(a: Sequence[EnsembleResult],
                       b: Sequence[EnsembleResult]) -> Dict[str, Any]:
    """İki ENSEMBLE arasındaki karar değişimi.

    Kararlılık iki AYRI ensemble arasında ölçülür (Codex): bir ensemble'ın
    kendi iki girdisini karşılaştırmak bağlam duyarlılığını ölçer, ürün
    kararının kararlılığını DEĞİL.
    """
    a_by = {r.keyword_id: r for r in a}
    b_by = {r.keyword_id: r for r in b}
    common = set(a_by) & set(b_by)
    per_channel = {}
    any_pass_flip = 0
    for ch in CHANNELS:
        pass_flips = sum(1 for k in common
                         if a_by[k].passing[ch] != b_by[k].passing[ch])
        mean_flips = sum(1 for k in common
                         if a_by[k].mean_fit[ch] != b_by[k].mean_fit[ch])
        per_channel[CHANNEL_KEYS[ch]] = {
            "passing_flips": pass_flips,
            "passing_flip_rate": round(pass_flips / len(common), 4) if common else None,
            "mean_fit_flips": mean_flips,
            "mean_fit_flip_rate": round(mean_flips / len(common), 4) if common else None,
        }
    for k in common:
        if any(a_by[k].passing[ch] != b_by[k].passing[ch] for ch in CHANNELS):
            any_pass_flip += 1
    return {
        "compared": len(common),
        "per_channel": per_channel,
        "any_channel_passing_flip_rate": (
            round(any_pass_flip / len(common), 4) if common else None
        ),
    }


def ensemble_stats(merged: Sequence[EnsembleResult]) -> Dict[str, Any]:
    total = len(merged)
    out: Dict[str, Any] = {
        "universe": total,
        "uncertain": sum(1 for r in merged if r.uncertain),
        "single_view_unresolved": sum(
            1 for r in merged if len(r.unresolved_views) == 1
        ),
        "ensemble_contract_version": ENSEMBLE_CONTRACT_VERSION,
    }
    for ch in CHANNELS:
        key = CHANNEL_KEYS[ch]
        out[key] = {
            "passing": sum(1 for r in merged if r.passing[ch]),
            "eliminated_both_zero": sum(1 for r in merged if not r.passing[ch]),
            "context_disagreement": sum(
                1 for r in merged if r.context_disagreement[ch]
            ),
            "mean_fit_distribution": {
                str(v): sum(1 for r in merged if r.mean_fit[ch] == v)
                for v in (0.0, 0.5, 1.0, 1.5, 2.0)
            },
        }
    return out
