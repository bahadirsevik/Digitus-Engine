# -*- coding: utf-8 -*-
"""Corpus screening freeze testleri (plan_ai §4a + Codex freeze şartları).

Şart 1: sabitler tek kaynak (contract modülü) — ölçüm scripti kopya taşımaz.
Şart 2: RC-v1'deki her kod <= MAX_REASON_CODE_LEN.
Şart 3: GERÇEK Pydantic şemasıyla 30 öğelik en büyük payload'un güvenli
        token TAHMİNİ MAX_OUTPUT_TOKENS'ı aşmamalı — şemaya alan eklenirse
        bu test kırılır.
Ek: prompt şablonu SHA-kilitli (sürüm-anahtarlı append-only registry).
"""
import hashlib
import json
import math
import re

import pytest

from app.core.screening import contract
from app.core.screening.contract import (
    BATCH_SIZE,
    CHARS_PER_TOKEN_SAFE_UPPER,
    DEEPSEEK_THINKING,
    GEMINI_THINKING_LEVEL,
    MAX_OUTPUT_TOKENS,
    MAX_REASON_CODE_LEN,
    PERMUTATION_SEEDS,
    PROMPT_VERSION,
    RC_V1,
    RESPONSE_SCHEMA,
    SAFETY_MULTIPLIER,
    TEMPERATURE,
    ScreeningBatchOutput,
    ScreeningItem,
    ScreeningReasonCodes,
    build_prompt,
    validate_reason_code_membership,
    worst_case_batch_payload,
)

# Sürüm-anahtarlı APPEND-ONLY freeze hash registry (v2 prompt kalıbıyla
# aynı disiplin): bilinçli değişiklik = YENİ PROMPT_VERSION + yeni kayıt;
# mevcut kayıt DÜZENLENMEZ. prompt VE provider response_schema birlikte
# kilitlidir (iç-inceleme bulgusu: şema da model çıktısını biçimlendirir).
FREEZE_HASH_REGISTRY = {
    # v1: puan açıklamaları keyword satır biçimiyle çakışıyordu (ücretli koşu
    # YAPILMADAN düzeltildi) — kayıt append-only sözleşmesi gereği KALIR
    "SCR-2026-07-27-v1": {
        "prompt":
            "378c12a150f197798f05907b24486a57c87900b3d8dacaa2a0e605bbe4d9234c",
        "response_schema":
            "7610810f540112ab30190e9f727e83f3cce1e6628292b20e244edc106b9b61d4",
    },
    "SCR-2026-07-27-v2": {
        "prompt":
            "b59a2683e31f2612b2a95ddc90bb856acfbf0957d240bd2d55baf5b2dc5a7c92",
        "response_schema":
            "7610810f540112ab30190e9f727e83f3cce1e6628292b20e244edc106b9b61d4",
    },
    # v3a: TEK DEĞİŞKEN bağımsızlık talimatı; metin SONUÇLARA BAKILMADAN
    # kilitlendi (holdout disiplini). Şema aynı — yalnız prompt değişti.
    "SCR-2026-07-27-v3a": {
        "prompt":
            "0126edc80a2388723440300b01179a78779b70a184a286053854fed6ebdcdab9",
        "response_schema":
            "7610810f540112ab30190e9f727e83f3cce1e6628292b20e244edc106b9b61d4",
    },
}

# İKİ keyword: '\n'.join ayracı da hash kapsamına girer (iç-inceleme
# bulgusu: tek keyword'le ayraç hiç çalışmıyordu, sessizce değişebilirdi)
FIXED_CONTEXT = {
    "product_definition": "Sabit ürün tanımı.",
    "content_strategy": "Sabit içerik stratejisi beyanı.",
    "target_audience": "Sabit hedef kitle.",
    "social_mode": "hype",
    "keywords": [
        {"id": 1, "keyword": "sabit kelime"},
        {"id": 2, "keyword": "ikinci sabit kelime"},
    ],
}


def test_rc_v1_codes_length_and_shape():
    """Codex şart 2: her kod <= MAX_REASON_CODE_LEN, UPPER_SNAKE, benzersiz."""
    for channel, codes in RC_V1.items():
        assert codes, channel
        assert len(set(codes)) == len(codes), f"{channel}: duplicate kod"
        for code in codes:
            assert len(code) <= MAX_REASON_CODE_LEN, f"{channel}:{code}"
            assert re.match(r"^[A-Z][A-Z0-9_]*$", code), f"{channel}:{code}"


def test_worst_case_payload_fits_token_budget():
    """Codex şart 3: gerçek şemayla en büyük payload bütçeye sığmalı."""
    payload = worst_case_batch_payload()
    # Gerçek Pydantic şemasından üretildi ve BATCH_SIZE öğe taşıyor
    parsed = ScreeningBatchOutput.model_validate_json(payload)
    assert len(parsed.results) == BATCH_SIZE

    chars = len(payload)
    tok_safe = math.ceil(chars / CHARS_PER_TOKEN_SAFE_UPPER)
    required = math.ceil(tok_safe * SAFETY_MULTIPLIER)
    assert required <= MAX_OUTPUT_TOKENS, (
        f"şema büyümüş: gerekli {required} > MAX_OUTPUT_TOKENS "
        f"{MAX_OUTPUT_TOKENS} — sınır yeniden ölçülmeli "
        f"(scripts/measure_screening_output_budget.py)"
    )


def test_frozen_runtime_parameters_pinned():
    """İç-inceleme bulgusu: §4a'nın dondurduğu TÜM üretim parametreleri
    test-pinli olmalı — prompt SHA'sı bunları kapsamıyor, sessiz edit
    hiçbir kapıyı kırmadan freeze'i bozardı."""
    assert TEMPERATURE == 0.3
    assert PERMUTATION_SEEDS == (20260727, 42)
    assert GEMINI_THINKING_LEVEL == "low"
    assert DEEPSEEK_THINKING == {"type": "disabled"}


def test_freeze_hashes_locked_version_keyed():
    """Prompt VE provider şeması birlikte kilitli (iç-inceleme bulgusu:
    RESPONSE_SCHEMA hiçbir kapı tarafından korunmuyordu — oysa Gemini
    structured-output'ta şema çıktıyı doğrudan biçimlendirir)."""
    assert PROMPT_VERSION in FREEZE_HASH_REGISTRY, (
        "PROMPT_VERSION registry'de yok — sözleşme değiştiyse YENİ sürümle "
        "yeni kayıt ekleyin, mevcut kaydı düzenlemeyin"
    )
    expected = FREEZE_HASH_REGISTRY[PROMPT_VERSION]
    prompt_sha = hashlib.sha256(
        build_prompt(**FIXED_CONTEXT).encode("utf-8")
    ).hexdigest()
    schema_sha = hashlib.sha256(
        json.dumps(RESPONSE_SCHEMA, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert prompt_sha == expected["prompt"]
    assert schema_sha == expected["response_schema"]
    # Registry bütünlüğü: hiçbir iki sürüm aynı hash çiftini taşımaz
    pairs = [tuple(sorted(v.items())) for v in FREEZE_HASH_REGISTRY.values()]
    assert len(pairs) == len(set(pairs))


def test_response_schema_matches_pydantic_fields():
    """İç-inceleme ikinci-derece riski: bütçe Pydantic'ten ölçülüyor ama
    sağlayıcı RESPONSE_SCHEMA'ya göre üretiyor — alan kümeleri ayrışırsa
    ölçülen tavan geçersizleşir. Çapraz kontrol."""
    item_schema = RESPONSE_SCHEMA["properties"]["results"]["items"]
    assert set(item_schema["properties"]) == set(
        ScreeningItem.model_fields
    )
    assert set(item_schema["required"]) == set(ScreeningItem.model_fields)
    rc_schema = item_schema["properties"]["reason_codes"]
    assert set(rc_schema["properties"]) == set(
        ScreeningReasonCodes.model_fields
    )
    # Repo sözleşmesi: provider şemasında enum YOK (parse'ta doğrulanır)
    assert "enum" not in json.dumps(RESPONSE_SCHEMA)


def test_prompt_contains_contract_blocks():
    prompt = build_prompt(**FIXED_CONTEXT)
    assert "ONAYLI İÇERİK STRATEJİSİ BEYANI" in prompt
    assert "Sabit içerik stratejisi beyanı." in prompt
    assert "ads_fit" in prompt and "seo_fit" in prompt and "social_fit" in prompt
    # Genel 'markayla ilgili mi' sorusu YASAK (plan_ai §5)
    assert "markayla ilgili mi" not in prompt.lower()
    # Tüm RC kodları prompt'ta listelenir
    for codes in RC_V1.values():
        for code in codes:
            assert code in prompt
    assert "emin değilsen 1 ver" in prompt
    # Puan açıklamaları keyword satır biçimiyle ÇAKIŞMAMALI: '- <id>: <kw>'
    # deseni YALNIZ anahtar kelime satırlarında görülmeli (v1'de puan
    # satırları da bu desendeydi — satır-bazlı ayrıştırma bozuluyordu)
    id_lines = re.findall(r"^- (\d+): (.+)$", prompt, re.M)
    assert [int(i) for i, _ in id_lines] == [1, 2]
    assert [t for _, t in id_lines] == ["sabit kelime", "ikinci sabit kelime"]


def test_reason_code_membership_parse_layer():
    ok = ScreeningItem(
        id=1, ads_fit=2, seo_fit=1, social_fit=0,
        reason_codes=ScreeningReasonCodes(
            ads="COMMERCIAL_FIT", seo="STRATEGY_NEAR",
            social="LOW_DISCUSSABILITY",
        ),
    )
    assert validate_reason_code_membership(ok) == []
    # Şema-geçerli ama enum-dışı kod: şema GEÇİRİR, parse katmanı yakalar
    rogue = ScreeningItem(
        id=2, ads_fit=1, seo_fit=1, social_fit=1,
        reason_codes=ScreeningReasonCodes(
            ads="MADE_UP_CODE", seo="STRATEGY_FIT", social="AMBIGUOUS",
        ),
    )
    assert validate_reason_code_membership(rogue) == ["ads:MADE_UP_CODE"]
    # Uzunluk/biçim ihlali şemada kesilir
    with pytest.raises(Exception):
        ScreeningReasonCodes(ads="X" * (MAX_REASON_CODE_LEN + 1),
                             seo="A", social="B")
    with pytest.raises(Exception):
        ScreeningReasonCodes(ads="küçük harf", seo="A", social="B")


def test_v3a_is_v2_plus_independence_block_only():
    """v3a TEK DEĞİŞKEN olmalı: v2 metnine yalnız bağımsızlık bloğu eklenir."""
    from app.core.screening.contract import (
        INDEPENDENCE_BLOCK,
        PROMPT_TEMPLATE,
        PROMPT_TEMPLATES,
    )

    v2 = PROMPT_TEMPLATES["SCR-2026-07-27-v2"]
    v3a = PROMPT_TEMPLATES["SCR-2026-07-27-v3a"]
    assert v2 == PROMPT_TEMPLATE  # üretim varsayılanı değişmedi
    assert INDEPENDENCE_BLOCK in v3a
    # Bloğu çıkarınca v2 ile BİREBİR aynı olmalı (başka değişiklik yok)
    assert v3a.replace(INDEPENDENCE_BLOCK + chr(10) * 2, "") == v2
    # Üretim varsayılanı hâlâ v2
    assert PROMPT_VERSION == "SCR-2026-07-27-v2"
