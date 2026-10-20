"""RSA aday havuzu -> tekrar temizliği -> KW kapsamı -> seçim -> tek tamamlama
(plan_ads_15kw_standardi §2). DB, ağ ve ücretli AI çağrısı yok: _FakeAI prompt'a
bakarak cevap verir (primary / completion / claim rewrite / kısaltma).
"""
import json

import pytest

from app.generators.ads.prompt_templates import (
    ADS_RSA_CANDIDATES_PROMPT,
    ADS_RSA_GENERATION_PROMPT,
)
from app.generators.ads.rsa_generator import RSAGenerator
from app.generators.ads.validators import build_standard_report, is_cta
from app.schemas.ads import AdGroupFullSchema, DescriptionSchema, HeadlineSchema, KeywordGroupSchema

LETTERS = "ABCDEFGHIJKLMNOPQRST"
# 20 jenerik, KW içermez; 0/2/3/5. indeksler CTA ("... Teklif Alın"): erken testler 3 CTA şartını kendiliğinden sağlar
GENERIC = [f"Aday Başlık {c}" + (" Teklif Alın" if i in (0, 2, 3, 5) else "") for i, c in enumerate(LETTERS)]
KW_DESCS = [
    "Ucuz laptop seçeneklerini hemen keşfedin.",
    "Ucuz laptop modellerini karşılaştırın.",
    "Ucuz laptop fırsatlarını inceleyin.",
    "Ucuz laptop için uygun fiyat bulun.",
    "Ucuz laptop alırken doğru seçimi yapın.",
]
EMPTY = json.dumps({"headlines": [], "descriptions": [], "negative_keywords": []})


def _group(*keywords):
    kws = list(keywords) or ["ucuz laptop"]
    return KeywordGroupSchema(
        name="Laptop", theme="laptop",
        keyword_ids=list(range(1, len(kws) + 1)), keywords=kws,
    )


def _rsa(headlines, descriptions, neg_prefix="neg", position="any"):
    return json.dumps({
        "headlines": [{"text": t, "type": "benefit", "position": position} for t in headlines],
        "descriptions": [{"text": t, "type": "value_prop", "position": position} for t in descriptions],
        "negative_keywords": [
            {"keyword": f"{neg_prefix}{i}", "match_type": "phrase", "category": "c", "reason": "r"}
            for i in range(10)
        ],
    })


class _FakeAI:
    """complete_json: '18-20 aday başlık' -> primary, aksi halde completion.
    complete: claim rewrite / açıklama kısaltma / başlık yeniden yazım."""

    def __init__(self, primary, completion=EMPTY, rewrite_text="", shorten_text=""):
        self.primary, self.completion = primary, completion
        self.rewrite_text, self.shorten_text = rewrite_text, shorten_text
        self.calls = {"primary": 0, "completion": 0, "rewrite": 0, "shorten": 0, "regen": 0}
        self.json_prompts = {"primary": [], "completion": []}

    def complete_json(self, prompt, max_tokens=None, response_schema=None):
        kind = "primary" if "18-20 aday başlık" in prompt else "completion"
        self.calls[kind] += 1
        self.json_prompts[kind].append(prompt)
        answer = self.primary if kind == "primary" else self.completion
        if isinstance(answer, Exception):
            raise answer
        return answer

    def complete(self, prompt, max_tokens=None):
        if "TESPİT EDİLEN DESTEKSİZ İDDİALAR" in prompt:
            self.calls["rewrite"] += 1
            return self.rewrite_text
        if "Google Ads açıklamasını" in prompt:
            self.calls["shorten"] += 1
            return self.shorten_text
        self.calls["regen"] += 1
        return ""


def _gen(ai, regen=True, fallback=False):
    return RSAGenerator(ai_service=ai, enable_ai_regeneration=regen, enable_fallback=fallback)


def _texts(items):
    return [i.text for i in items]


# ==================== SIRA: kesme yok, iddia sonrası seçim ====================

def test_late_candidates_fill_in_after_grounding_eliminations():
    claims = {1: "100.000+ Müşteri", 4: "Yüzde 80 İndirim Fırsatı", 8: "10 Yıllık Deneyim"}
    cands = [claims.get(i, GENERIC[i]) for i in range(20)]
    ai = _FakeAI(_rsa(cands, KW_DESCS))   # rewrite "" -> ihlalli asset elenir
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    texts = _texts(result.headlines)
    assert len(texts) == 15
    assert not set(claims.values()) & set(texts)
    # erken kesme olsaydı 16.-18. adaylar (indeks 15-17) hiç görülmezdi
    assert {GENERIC[15], GENERIC[16], GENERIC[17]} <= set(texts)
    assert GENERIC[18] not in texts and GENERIC[19] not in texts
    assert ai.calls["rewrite"] == 3
    assert ai.calls["completion"] == 0          # 15 başlık + 4 KW'li açıklama: çağrı yok
    assert (result.missing_headlines, result.missing_kw_descriptions) == (0, 0)


def test_selection_prefers_headlines_covering_new_keywords():
    kws = ["ucuz laptop", "oyuncu laptop", "ofis laptop"]
    cands = GENERIC[:15] + ["Oyuncu Laptop Modelleri", "Ofis Laptop Seçenekleri",
                            "Ucuz Laptop Fırsatları"] + GENERIC[15:17]
    ai = _FakeAI(_rsa(cands, KW_DESCS))
    result = _gen(ai).generate_rsa(_group(*kws), brand_name="Marka")

    texts = _texts(result.headlines)
    assert len(texts) == 15
    assert {"Oyuncu Laptop Modelleri", "Ofis Laptop Seçenekleri", "Ucuz Laptop Fırsatları"} <= set(texts)
    # kalan 12 yer aday sırasıyla dolar; çıktı aday sırasındadır
    assert texts[:12] == GENERIC[:12]
    assert texts[12:] == ["Oyuncu Laptop Modelleri", "Ofis Laptop Seçenekleri", "Ucuz Laptop Fırsatları"]


def test_descriptions_prefer_keywords_not_covered_by_headlines():
    kws = ["ucuz laptop", "oyuncu laptop", "ofis laptop"]
    headlines = ["Ucuz Laptop Fırsatı"] + GENERIC[:14]
    descs = KW_DESCS[:4] + ["Oyuncu laptop modellerini inceleyin.", "Ofis laptop seçeneklerine göz atın."]
    ai = _FakeAI(_rsa(headlines, descs))
    result = _gen(ai).generate_rsa(_group(*kws), brand_name="Marka")

    assert _texts(result.descriptions) == [
        KW_DESCS[0], KW_DESCS[1],
        "Oyuncu laptop modellerini inceleyin.", "Ofis laptop seçeneklerine göz atın.",
    ]
    assert ai.calls["completion"] == 0


def test_duplicates_removed_case_and_turkish_dotted_i():
    headlines = ["İstanbul Kurye Hizmeti", "istanbul kurye hizmeti", "ISTANBUL  KURYE HİZMETİ",
                 "Hemen Sipariş Ver", "Fiyat Teklifi Al"]
    descs = ["İstanbul kurye hizmetini inceleyin.", "ISTANBUL KURYE HİZMETİNİ İNCELEYİN.",
             "İstanbul kurye için hemen arayın."]
    ai = _FakeAI(_rsa(headlines, descs))
    result = _gen(ai, regen=False).generate_rsa(_group("istanbul kurye"), brand_name="Marka")

    assert _texts(result.headlines) == ["İstanbul Kurye Hizmeti", "Hemen Sipariş Ver", "Fiyat Teklifi Al"]
    assert _texts(result.descriptions) == ["İstanbul kurye hizmetini inceleyin.",
                                          "İstanbul kurye için hemen arayın."]


# ==================== AÇIKLAMA: SON metinde KW kontrolü ====================

def test_description_truncated_without_keyword_does_not_count():
    long_desc = ("Güvenilir ve hızlı teslimatla alışveriş keyfini yaşayın, kampanyaları "
                 "kaçırmayın ve ucuz laptop")
    assert len(long_desc) > 90
    ai = _FakeAI(_rsa(GENERIC[:15], KW_DESCS[:3] + [long_desc]))
    result = _gen(ai, regen=False).generate_rsa(_group(), brand_name="Marka")

    last = result.descriptions[-1].text
    assert last.endswith("...") and "ucuz laptop" not in last
    # KW'siz temiz açıklama dolgu olarak girer ama standarda sayılmaz
    assert len(result.descriptions) == 4
    assert result.missing_kw_descriptions == 1


def test_keyword_less_descriptions_only_fill_after_keyword_ones():
    descs = ["Hemen keşfedin ve karşılaştırın.", "Fırsatları kaçırmayın.",
             KW_DESCS[0], KW_DESCS[1], KW_DESCS[2]]
    ai = _FakeAI(_rsa(GENERIC[:15], descs))
    result = _gen(ai, regen=False).generate_rsa(_group(), brand_name="Marka")

    assert _texts(result.descriptions) == ["Hemen keşfedin ve karşılaştırın.",
                                          KW_DESCS[0], KW_DESCS[1], KW_DESCS[2]]
    assert result.missing_kw_descriptions == 1


def test_claim_rewrite_that_drops_keyword_does_not_count():
    claim_desc = "Ucuz laptop modellerinde 10 yıllık deneyim sizi bekliyor."
    ai = _FakeAI(_rsa(GENERIC[:15], KW_DESCS[:3] + [claim_desc]),
                 rewrite_text="Güvenilir alışveriş deneyimi yaşayın.")
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    assert "Güvenilir alışveriş deneyimi yaşayın." in _texts(result.descriptions)
    assert result.missing_kw_descriptions == 1
    assert ai.calls["completion"] == 1          # KW'li açıklama 3 < 4


# ==================== TAMAMLAMA ====================

def test_completion_called_once_and_keeps_clean_candidates():
    primary = _rsa(GENERIC[:10], KW_DESCS[:2], neg_prefix="primneg")
    comp_h = [f"Yeni Başlık {c}" for c in "ABCDEFGH"]
    comp_d = ["Ucuz laptop için yeni fırsatlar.", "Ucuz laptop seçimini kolaylaştırın.",
              "Ucuz laptop kampanyalarını görün.", "Ucuz laptop avantajlarını yakalayın."]
    ai = _FakeAI(primary, completion=_rsa(comp_h, comp_d, neg_prefix="compneg"))
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    assert ai.calls["completion"] == 1
    texts = _texts(result.headlines)
    assert texts[:10] == GENERIC[:10]                       # temiz adaylar korundu
    assert texts[10:] == [f"Yeni Başlık {c}" for c in "ABCDE"]
    assert _texts(result.descriptions) == KW_DESCS[:2] + comp_d[:2]
    assert all(n.keyword.startswith("primneg") for n in result.negative_keywords)

    prompt = ai.json_prompts["completion"][0]
    assert "# TAMAMLAMA TURU" in prompt
    assert "- Aday Başlık A" in prompt and f"- {KW_DESCS[0]}" in prompt   # tekrar etme listesi
    assert "8 aday başlık üret" in prompt                     # 15-10 eksik + 3 yedek
    assert "4 aday açıklama üret" in prompt                   # 4-2 eksik + 2 yedek
    assert "ucuz laptop" in prompt.split("Henüz hiçbir başlıkta geçmeyen")[1].split("\n")[0]


def test_completion_not_called_when_counts_full_even_if_coverage_incomplete():
    kws = ["ucuz laptop", "oyuncu laptop", "ofis laptop"]
    ai = _FakeAI(_rsa(GENERIC[:15], KW_DESCS[:4]))      # hiçbir başlık KW taşımıyor
    result = _gen(ai).generate_rsa(_group(*kws), brand_name="Marka")

    assert ai.calls["completion"] == 0
    assert (result.missing_headlines, result.missing_kw_descriptions, result.missing_cta) == (0, 0, 0)


def test_completion_not_called_when_regeneration_disabled():
    ai = _FakeAI(_rsa(GENERIC[:10], KW_DESCS[:2]))
    result = _gen(ai, regen=False).generate_rsa(_group(), brand_name="Marka")

    assert ai.calls["completion"] == 0
    assert len(result.headlines) == 10
    assert (result.missing_headlines, result.missing_kw_descriptions, result.missing_cta) == (5, 2, 0)


def test_shortfall_reported_without_generic_padding():
    ai = _FakeAI(_rsa(GENERIC[:6], KW_DESCS[:3]),
                 completion=_rsa(["Yeni Başlık A", "Yeni Başlık B"], ["Ucuz laptop için yeni fırsatlar."]))
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    assert ai.calls["completion"] == 1                  # en fazla bir
    assert len(result.headlines) == 8                   # 6 + 2: şablonla 15'e doldurulmadı
    assert not any(t in ("Hemen İncele", "Marka Güvencesi", "Kaliteli Ürün Seçenekleri")
                   for t in _texts(result.headlines))
    assert len(result.descriptions) == 4
    assert (result.missing_headlines, result.missing_kw_descriptions) == (7, 0)


def test_primary_unusable_completion_acts_as_full_generation():
    comp = _rsa(GENERIC[:18], KW_DESCS, neg_prefix="compneg")
    ai = _FakeAI("bozuk json {{{", completion=comp)
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    assert (ai.calls["primary"], ai.calls["completion"]) == (1, 1)
    prompt = ai.json_prompts["completion"][0]
    assert "# TAMAMLAMA TURU" not in prompt             # temiz aday yok: tam üretim
    assert "18 aday başlık üret" in prompt and "6 aday açıklama üret" in prompt
    assert len(result.headlines) == 15
    assert len(result.descriptions) == 4
    assert len(result.negative_keywords) >= 10          # negatifler completion'dan
    assert all(n.keyword.startswith("compneg") for n in result.negative_keywords)


def test_primary_exception_propagates_without_completion():
    ai = _FakeAI(RuntimeError("API down"))
    with pytest.raises(RuntimeError):
        _gen(ai).generate_rsa(_group(), brand_name="Marka")
    assert ai.calls["completion"] == 0


def test_completion_failure_keeps_clean_candidates_when_minimum_met():
    ai = _FakeAI(_rsa(GENERIC[:10], KW_DESCS[:3]), completion=RuntimeError("API down"))
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    assert ai.calls["completion"] == 1
    assert _texts(result.headlines) == GENERIC[:10]
    assert result.missing_headlines == 5


def test_completion_failure_below_minimum_raises_like_old_retry():
    ai = _FakeAI("bozuk json {{{", completion=RuntimeError("API down"))
    with pytest.raises(RuntimeError):
        _gen(ai).generate_rsa(_group(), brand_name="Marka")


# ==================== SERT MİNİMUM FALLBACK ====================

def test_hard_minimum_deterministic_fallback_still_works():
    ai = _FakeAI("bozuk json {{{", completion="bozuk json {{{")
    result = _gen(ai, fallback=True).generate_rsa(_group(), brand_name="Marka")

    assert ai.calls["completion"] == 1
    assert len(result.headlines) >= RSAGenerator.MIN_HEADLINES
    assert len(result.descriptions) >= RSAGenerator.MIN_DESCRIPTIONS
    assert len(result.headlines) < 15 and result.missing_headlines == 15 - len(result.headlines)


def test_empty_usp_is_not_replaced_with_generic_text():
    # Boş USP modele "—" olarak gider; jenerik "Kaliteli ürün ve hizmet" metne sızıyordu
    ai = _FakeAI("bozuk json {{{", completion="bozuk json {{{")
    result = _gen(ai, fallback=True).generate_rsa(_group(), brand_name="Marka", brand_usp="")

    prompt = ai.json_prompts["primary"][0]
    assert "Kaliteli ürün ve hizmet" not in prompt
    assert "USP (Unique Selling Proposition): —" in prompt
    texts = [d.text for d in result.descriptions]          # fallback açıklamaları
    assert texts and all("Kaliteli ürün ve hizmet" not in t for t in texts)
    assert all(not t.startswith(("—", ".")) for t in texts)


def test_given_usp_still_reaches_the_prompt():
    ai = _FakeAI(EMPTY)
    _gen(ai, fallback=True).generate_rsa(_group(), brand_name="Marka", brand_usp="Ücretsiz kurulum")
    assert "USP (Unique Selling Proposition): Ücretsiz kurulum" in ai.json_prompts["primary"][0]


def test_below_hard_minimum_without_fallback_raises():
    ai = _FakeAI("bozuk json {{{", completion="bozuk json {{{")
    with pytest.raises(ValueError):
        _gen(ai, fallback=False).generate_rsa(_group(), brand_name="Marka")


def test_fallback_keeps_clean_headlines_and_only_tops_up_descriptions():
    # 15 temiz başlık + 1 açıklama; tamamlama çağrısı işe yarar bir şey vermiyor.
    # Fallback yalnız açıklamayı sert minimuma (2) tamamlar; 15 başlık silinmez.
    ai = _FakeAI(_rsa(GENERIC[:15], KW_DESCS[:1]), completion=EMPTY)
    result = _gen(ai, fallback=True).generate_rsa(_group(), brand_name="Marka")

    assert ai.calls["completion"] == 1
    assert _texts(result.headlines) == GENERIC[:15]          # birebir, aynı sıra
    assert "Hemen İncele" not in _texts(result.headlines)    # fallback başlığı eklenmedi
    assert _texts(result.descriptions) == [
        KW_DESCS[0], "Marka ile ucuz laptop seçeneklerini keşfedin. Hemen inceleyin.",
    ]                                                        # temiz önde, fallback minimuma kadar
    assert result.missing_headlines == 0
    assert result.missing_kw_descriptions == 2               # iki açıklama da KW'li, 4'e 2 eksik
    assert all(n.keyword.startswith("neg") for n in result.negative_keywords)  # mevcut negatifler korundu


def test_fallback_keeps_clean_descriptions_when_headlines_below_minimum():
    ai = _FakeAI(_rsa(GENERIC[:2], KW_DESCS[:4]), completion=EMPTY)
    result = _gen(ai, fallback=True).generate_rsa(_group(), brand_name="Marka")

    assert _texts(result.descriptions) == KW_DESCS[:4]       # açıklamalara dokunulmadı
    assert _texts(result.headlines) == GENERIC[:2] + ["ucuz laptop"]   # 3'e kadar, temizlerin arkasına
    assert (result.missing_headlines, result.missing_kw_descriptions) == (12, 0)


def test_fallback_without_any_clean_candidates_builds_hard_minimum_only():
    ai = _FakeAI("bozuk json {{{", completion="bozuk json {{{")
    result = _gen(ai, fallback=True).generate_rsa(_group(), brand_name="Marka")

    assert len(result.headlines) == RSAGenerator.MIN_HEADLINES
    assert len(result.descriptions) == RSAGenerator.MIN_DESCRIPTIONS


# ==================== KONUM: AI önerisi tutulmaz, hepsi "any" ====================

def _assert_all_any(result):
    assert result.headlines and result.descriptions
    assert {a.position_preference for a in (*result.headlines, *result.descriptions)} == {"any"}


def test_positions_forced_to_any_on_primary_path():
    # Kanıt: bir grupta 15 başlığın hepsi position_1 gelmişti
    ai = _FakeAI(_rsa(GENERIC[:18], KW_DESCS, position="position_1"))
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    assert len(result.headlines) == 15 and len(result.descriptions) == 4
    assert ai.calls["completion"] == 0
    _assert_all_any(result)


def test_positions_forced_to_any_on_completion_path():
    comp = _rsa([f"Yeni Başlık {c}" for c in "ABCDEFGH"], KW_DESCS[2:4], position="position_2")
    ai = _FakeAI(_rsa(GENERIC[:10], KW_DESCS[:2], position="position_1"), completion=comp)
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    assert ai.calls["completion"] == 1
    assert "Yeni Başlık A" in _texts(result.headlines)     # completion adayları da girdi
    _assert_all_any(result)


def test_positions_forced_to_any_after_claim_rewrite():
    cands = ["100.000+ Müşteri"] + GENERIC[:17]
    ai = _FakeAI(_rsa(cands, KW_DESCS[:4], position="position_3"), rewrite_text="Güvenle İncele Keşfet")
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    assert ai.calls["rewrite"] == 1
    assert "Güvenle İncele Keşfet" in _texts(result.headlines)   # rewrite yolu çalıştı
    _assert_all_any(result)


def test_positions_forced_to_any_on_deterministic_fallback():
    ai = _FakeAI("bozuk json {{{", completion="bozuk json {{{")
    result = _gen(ai, fallback=True).generate_rsa(_group(), brand_name="Marka")

    # fallback şablonu position_1/position_3 taşır (adaylarda); çıktıda hiçbiri kalmaz
    assert "ucuz laptop" in _texts(result.headlines)
    _assert_all_any(result)


# ==================== TEKRAR: DKI ve düz ikizi ====================

def _h(text):
    return HeadlineSchema(text=text, type="keyword")


def test_dedup_treats_dki_headline_and_plain_twin_as_duplicates():
    dki, plain = "{KeyWord:Google Ads Ajansı}", "Google Ads Ajansı"
    assert [h.text for h in RSAGenerator._dedup([_h(dki), _h(plain)])] == [dki]     # ilk gelen kalır
    assert [h.text for h in RSAGenerator._dedup([_h(plain), _h(dki)])] == [plain]
    # noktalama / büyük İ / boşluk farkı da aynı metin sayılır
    twins = [_h("İstanbul Kurye!"), _h("istanbul  kurye"), _h("{Keyword:ISTANBUL KURYE}")]
    assert [h.text for h in RSAGenerator._dedup(twins)] == ["İstanbul Kurye!"]
    assert len(RSAGenerator._dedup([_h("Google Ads"), _h("Google Ads Ajansı")])) == 2   # alt küme tekrar değil
    desc = [DescriptionSchema(text="Hemen arayın.", type="cta"), DescriptionSchema(text="hemen arayın", type="cta")]
    assert len(RSAGenerator._dedup(desc)) == 1


def test_dki_twin_collapses_to_one_headline_end_to_end():
    dki = "{KeyWord:Google Ads Ajansı}"
    ai = _FakeAI(_rsa([dki, "Google Ads Ajansı"] + GENERIC[:13], KW_DESCS[:4]))
    result = _gen(ai, regen=False).generate_rsa(_group(), brand_name="Marka")

    assert _texts(result.headlines) == [dki] + GENERIC[:13]
    assert result.headlines[0].is_dki is True


# ==================== CTA (plan §10): is_cta + seçim + tamamlama ====================

@pytest.mark.parametrize("text,expected", [
    ("SEO Ajansı İçin Teklif Alın", True),
    ("Hemen Teklif Alın", True),
    ("Reklam Ajansı Teklifi Alın", True),
    ("Bize Ulaşın", True),
    ("Hemen İletişime Geçin", True),
    ("Dijital Çözümleri İnceleyin", True),
    ("Ajans Hizmetlerini Keşfedin", True),
    ("B2B Çözümlerini İnceleyin", True),
    ("Çözümlerimizi Keşfedin", True),
    ("Meta Reklamlarını Keşfedin", True),
    ("Detaylı Bilgi İçin Ulaşın", True),
    ("Digitus Ajans ile Tanışın", True),
    ("Medya Planı İçin Başvurun", True),
    ("Hizmetleri Şimdi İnceleyin", True),
    ("Hizmetleri Hemen İnceleyin", True),
    ("Detaylı Bilgi Edinin", True),
    ("SEO Danışmanlığı Alın", True),
    ("Çözümleri Şimdi Keşfedin", True),
    ("Ads Danışmanlığı İçin Başvur", True),
    ("Hemen Arayın", True),
    ("Teklif Alın", True),
    ("Hemen Ara", True),
    ("{KeyWord:SEO Ajansı} İçin Teklif Alın!", True),
    ("Hemen Göz Atın", True),
    ("İletişime Geçin", True),
    ("Hizmetleri İnceleyin", True),
    # diş kliniği (sektörden bağımsızlık)
    ("Gülüş Tasarımı İçin Danışın", True), ("Şeffaf Plakları Keşfedin", True),
    ("Tedavi Planını İnceleyin", True), ("Şeffaf Plak İçin Randevu Al", True),
    ("Randevunuzu Oluşturun", True), ("Kliniğimizi Keşfedin", True), ("Çekim İçin Danışın", True),
    ("Tedavi Detaylarını Görün", True), ("Digitus Ajans ile Tanışın", True),
    # CTA != teslim kalitesi: bozuk Türkçe emirler de CTA sayılır (dil kalitesi ayrı konu)
    ("Instagram Reklam Verme İncele", True),
    ("Instagram Reklam Verme İnceleyin", True),
    ("TikTok Reklam Verme Başlat", False),
    ("TikTok Reklam Verme Başlatın", False),
    ("Facebook Reklam Vermek İçin", False),
    ("Facebook Reklam Vermek İçin Başvurun", True),
    ("Sosyal Medya Danışmanlık Al", True),
    ("Sosyal Medya Danışmanlık Alın", True),
    ("Sosyal Medya Hizmetleri Al", True),
    ("Dijital Görünürlük Kazanın", False),
    ("Digitus ile Büyüyün", False),
    ("Arama Motorunda Yükselin", False),
    ("Web Ajansı Çözümlerini Görün", True),
    ("Google Ads Ajansı Seçin", True),
    ("Dijital Reklam Ajansı Seçin", True),
    ("Teklif Alma Süreci", False),
    ("Organik Görünürlük Kazanın", False),
    ("Performansınızı Artırın", False),
    ("Ara Dönem Fırsatı", False),
    ("Ajansın Farkı", False),
    ("Atın Gücü", False),
    ("", False),
])
def test_is_cta(text, expected):
    assert is_cta(text) is expected


def _model_kws(n):
    return [f"model {c}" for c in "abcdefghijklmno"[:n]]


MODEL_DESCS = ["Model A seçeneklerini inceleyin.", "Model B modellerini karşılaştırın.",
               "Model C fırsatlarını görün.", "Model D için hızlı teslimat."]


def test_full_headlines_but_short_cta_triggers_completion_and_cta_replaces_non_cta():
    plain = [f"Düz Başlık {c}" for c in "ABCDEFGHIJKLMN"]       # 14 CTA'sız
    primary = _rsa(plain + ["Hemen Arayın"], KW_DESCS[:4])
    comp_h = ["Ucuz Laptop İçin Teklif Alın", "Hemen Teklif Alın", "Bize Ulaşın", "Hemen Başlayın"]
    ai = _FakeAI(primary, completion=_rsa(comp_h, []))
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    assert ai.calls["completion"] == 1                  # 15 başlık dolu olsa da CTA 1 < 3
    prompt = ai.json_prompts["completion"][0]
    assert "başlık ÜRETME" not in prompt
    assert "4 aday başlık üret; bunların EN AZ 4 tanesi CTA" in prompt     # eksik 2 + 2 yedek
    assert "Randevu Alın" in prompt and "uydurma" in prompt
    texts = _texts(result.headlines)
    assert len(texts) == 15
    assert sum(is_cta(t) for t in texts) >= 3 and result.missing_cta == 0
    assert "Ucuz Laptop İçin Teklif Alın" in texts           # KW'li CTA önde tutuldu
    assert len([t for t in texts if t in plain]) < 14   # CTA adayları sıradan başlıkların yerine geçti


def test_reaching_three_cta_costs_a_little_coverage():
    kws = _model_kws(15)
    covering = [f"Model {c} Fırsatı" for c in "ABCDEFGHIJKLMNO"]
    covering[0] = "Model A Teklif Alın"                  # tek CTA, bir KW kapsıyor
    primary = _rsa(covering, MODEL_DESCS)
    ai = _FakeAI(primary, completion=_rsa(["Hemen Arayın", "Bize Ulaşın", "Hemen Başlayın"], []))
    result = _gen(ai).generate_rsa(_group(*kws), brand_name="Marka")

    texts = _texts(result.headlines)
    assert ai.calls["completion"] == 1 and len(texts) == 15
    assert sum(is_cta(t) for t in texts) >= 3 and result.missing_cta == 0
    covered = sum(any(k in t.lower() for t in texts) for k in kws)
    assert covered == 13                                 # saf kapsam 15'ti; 2 CTA yeri için 2 KW feda


def test_completion_without_cta_reports_missing_cta_and_group_still_saved():
    ai = _FakeAI(_rsa([f"Düz Başlık {c}" for c in "ABCDEFGHIJKLMNO"], KW_DESCS[:4]), completion=EMPTY)
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    assert ai.calls["completion"] == 1
    assert len(result.headlines) == 15 and result.missing_cta == 3
    r = build_standard_report(["ucuz laptop"], [1], None, _texts(result.headlines), _texts(result.descriptions))
    assert (r.cta_count, r.missing_cta, r.below_standard) == (0, 3, True)


def test_keyword_gap_alone_does_not_trigger_completion_when_cta_is_met():
    kws = ["ucuz laptop", "oyuncu laptop", "ofis laptop"]
    ai = _FakeAI(_rsa(GENERIC[:15], KW_DESCS[:4]))
    result = _gen(ai).generate_rsa(_group(*kws), brand_name="Marka")
    assert ai.calls["completion"] == 0 and result.missing_cta == 0


def test_completion_that_does_not_improve_selection_is_rejected():
    # 15 başlık, 3 CTA, KW'li açıklama eksik -> çağrı açılır; ama dönen başlıklar seçimi iyileştirmez
    primary = _rsa(GENERIC[:15], KW_DESCS[:2])
    comp = _rsa(["Yeni Başlık A", "Yeni Başlık B", "Hemen Arayın"], KW_DESCS[2:4])
    ai = _FakeAI(primary, completion=comp)
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")

    assert ai.calls["completion"] == 1
    assert _texts(result.headlines) == GENERIC[:15]       # önceki seçim aynen
    assert _texts(result.descriptions) == KW_DESCS[:4]    # açıklama tamamlaması yine alındı


def test_selection_key_orders_cta_before_coverage():
    gen = _gen(_FakeAI(EMPTY))
    kws = _model_kws(4)
    h = lambda texts: [HeadlineSchema(text=t, type="keyword") for t in texts]   # noqa: E731
    four_kw_one_cta = h(["Model A Teklif Alın", "Model B Fırsatı", "Model C Fırsatı", "Model D Fırsatı"])
    three_kw_three_cta = h(["Model A Teklif Alın", "Model B Hemen Arayın", "Model C Bize Ulaşın", "Hızlı Teslimat"])
    assert gen._selection_key(four_kw_one_cta, kws) == (False, 4, 4)
    assert gen._selection_key(three_kw_three_cta, kws) == (True, 3, 4)
    assert gen._selection_key(three_kw_three_cta, kws) > gen._selection_key(four_kw_one_cta, kws)


def test_coverage_is_not_traded_for_a_fourth_cta():
    kws = _model_kws(12)
    ctas = ["Hemen Arayın", "Teklif Alın", "Bize Ulaşın", "Hemen Başlayın"]    # KW kapsamaz
    covering = [f"Model {c} Fırsatı" for c in "ABCDEFGHIJKL"]
    ai = _FakeAI(_rsa(ctas + covering, MODEL_DESCS))
    result = _gen(ai).generate_rsa(_group(*kws), brand_name="Marka")

    texts = _texts(result.headlines)
    assert ai.calls["completion"] == 0 and len(texts) == 15
    assert sum(any(k in t.lower() for t in texts) for k in kws) == 12     # kapsam tam
    assert sum(is_cta(t) for t in texts) == 3                              # 4. CTA yer bulamadı
    assert "Hemen Başlayın" not in texts


# ==================== ŞEMA + PROMPT ====================

def test_shortfall_fields_not_serialized():
    g = AdGroupFullSchema(name="a", theme="t", keyword_ids=[], keywords=[], headlines=[],
                          descriptions=[], negative_keywords=[],
                          missing_headlines=3, missing_kw_descriptions=1, missing_cta=2)
    assert (g.missing_headlines, g.missing_kw_descriptions, g.missing_cta) == (3, 1, 2)
    dumped = g.model_dump()
    assert not {"missing_headlines", "missing_kw_descriptions", "missing_cta"} & set(dumped)


def test_candidates_prompt_formats_and_lists_long_keywords():
    long_kw = "kurumsal e-ticaret sitesi kurulum hizmeti"
    assert len(long_kw) > 30
    gen = _gen(_FakeAI(EMPTY))
    prompt = gen._build_candidates_prompt(_group("ucuz laptop", long_kw), "Marka", "usp")
    assert "18-20 aday başlık" in prompt and "5-6 aday açıklama" in prompt
    assert f"yalnızca açıklamalarda kullan: {long_kw}" in prompt
    assert "{KeyWord:Laptoplar}" in prompt                       # DKI kuralı korunuyor
    assert "Hedef anahtar kelimeleri veya bunların parçalarını negatif" in prompt
    none_long = gen._build_candidates_prompt(_group("ucuz laptop"), "Marka", "usp")
    assert "yalnızca açıklamalarda kullan: —" in none_long


def test_candidates_prompt_asks_for_natural_turkish_not_forced_keywords():
    prompt = _gen(_FakeAI(EMPTY))._build_candidates_prompt(_group("ucuz laptop"), "Marka", "usp")
    assert "yazıldığı gibi" not in prompt                    # zorlayıcı eski ifade kalktı
    assert "AKICI, DOĞAL ve dilbilgisi doğru Türkçe" in prompt
    assert "yalnızca başlık AKICI, DOĞAL Türkçe olduğunda" in prompt
    assert "YAZMA; bu kabul edilen bir sonuçtur" in prompt   # atlamak kabul
    assert "dilbilgisini ASLA bozma" in prompt
    assert "TÜM anahtar kelimelerini" not in prompt and "bulunsun" not in prompt  # kelime başına baskı yok
    for bad in ("Instagram Reklam Verme İncele", "TikTok Reklam Verme Başlat", "Facebook Reklam Vermek İçin",
                "Sosyal Medya Hizmetleri Al", "Sosyal Medya Danışmanlık Al", "Web Sitesi Tasarimi",
                "B2B Odaklı Web Ajans", "SEO Ajansı İstanbul", "Facebook'ta Reklam Verin"):
        assert bad in prompt
    assert "TAŞIMA" in prompt                                  # bozuk KW açıklamaya taşınmaz
    assert "ÖNCELİKLE" not in prompt
    assert "EN AZ 5'i hizmete uygun CTA" in prompt and "SEO Ajansı İçin Teklif Alın" in prompt
    assert "yerleşen yoksa kelime kullanma" in prompt   # açıklamada da doğallık
    assert "zorla doldurma" in prompt                        # diğer KW'ler açıklamaya doldurulmaz
    assert "BU GRUBUN anahtar kelimelerinden ve temasından" in prompt   # tema uyumu
    assert "yalnızca açıklamalarda kullan" in prompt         # >30 karakter KW -> açıklama kuralı duruyor


def test_completion_block_asks_uncovered_keywords_only_where_natural():
    gen = _gen(_FakeAI(EMPTY))
    kept = ([HeadlineSchema(text="Aday Başlık A", type="benefit")],
            [DescriptionSchema(text="Hemen keşfedin.", type="cta")])
    prompt = gen._build_candidates_prompt(_group("ucuz laptop"), "Marka", "usp", kept)
    line = prompt.split("Henüz hiçbir başlıkta geçmeyen")[1].split("\n")[0]
    assert "YAZMA" in line and "kabul edilir" in line and "ucuz laptop" in line
    assert "cümleye doğal yerleşen en az bir anahtar kelime" in prompt   # açıklama talimatı


def test_source_prompt_is_untouched():
    # Mühürlü kaynak sabit (benchmark/idiomatic_prompts): aday prompt'u TÜRETİLİR
    assert "3-15 adet başlık üret" in ADS_RSA_GENERATION_PROMPT
    assert "{headline_instruction}" in ADS_RSA_CANDIDATES_PROMPT
    assert "3-15 adet başlık üret" not in ADS_RSA_CANDIDATES_PROMPT
    assert "ANAHTAR KELİME KULLANIMI" not in ADS_RSA_GENERATION_PROMPT   # yeni kural yalnız türetilende


def test_outcome_imperatives_only_pool_reports_missing_cta():
    outcome = ["Dijital Görünürlük Kazanın", "Digitus ile Büyüyün", "Arama Motorunda Yükselin",
               "Organik Görünürlük Kazanın"] + [f"Düz Başlık {c}" for c in "ABCDEFGHIJK"]
    result = _gen(_FakeAI(_rsa(outcome, KW_DESCS[:4]))).generate_rsa(_group(), brand_name="Marka")
    assert result.missing_cta == 3


# ==================== keyword_headlines (KW başına değerlendirme) ====================

def _rsa_kh(headlines, descriptions, entries):
    data = json.loads(_rsa(headlines, descriptions))
    data["keyword_headlines"] = [
        {"keyword_id": i, "headline": h, "reason": r} for i, h, r in entries]
    return json.dumps(data)


def _dec(result):
    return {d["keyword"]: (d["status"], d["reason"]) for d in result.keyword_decisions}


def test_keyword_headline_valid_is_added_and_improves_coverage():
    kws = ["ucuz laptop", "oyuncu laptop"]
    ai = _FakeAI(_rsa_kh(GENERIC[:15], KW_DESCS[:4], [
        (1, "Ucuz Laptop Fırsatı", ""), (2, "Oyuncu Laptop Seç", "")]))
    result = _gen(ai).generate_rsa(_group(*kws), brand_name="Marka")
    texts = _texts(result.headlines)
    assert "Ucuz Laptop Fırsatı" in texts and "Oyuncu Laptop Seç" in texts
    assert _dec(result) == {"ucuz laptop": ("headline", ""), "oyuncu laptop": ("headline", "")}


def test_keyword_headline_without_the_keyword_is_dropped_as_invalid():
    ai = _FakeAI(_rsa_kh(GENERIC[:15], KW_DESCS[:4], [(1, "Harika Bilgisayar Fırsatı", "")]))
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")
    assert "Harika Bilgisayar Fırsatı" not in _texts(result.headlines)
    assert _dec(result)["ucuz laptop"][0] == "invalid"


def test_declined_keyword_is_informational_and_asked_again_next_generation():
    ai = _FakeAI(_rsa_kh(GENERIC[:15], KW_DESCS[:4], [
        (1, "", "doğal Türkçe başlık olmuyor"), (99, "Bilinmeyen Başlık", "")]))
    gen = _gen(ai)
    group = _group()
    result = gen.generate_rsa(group, brand_name="Marka")
    assert _dec(result) == {"ucuz laptop": ("declined", "doğal Türkçe başlık olmuyor")}
    assert "Bilinmeyen Başlık" not in _texts(result.headlines)       # bilinmeyen id yok sayıldı
    gen.generate_rsa(group, brand_name="Marka")                       # ikinci üretim: KW yine istenir
    assert "- 1: ucuz laptop" in ai.json_prompts["primary"][1]


def test_broken_turkish_imperatives_still_count_as_cta():
    # CTA tespiti dil kalitesini YARGILAMAZ: bozuk ama eylem çağıran başlıklar CTA sayılır
    broken = ["Instagram Reklam Verme İncele", "Sosyal Medya Hizmetleri Al", "Sosyal Medya Danışmanlık Al"]
    ai = _FakeAI(_rsa(broken + [f"Düz Başlık {c}" for c in "ABCDEFGHIJKL"], KW_DESCS[:4]), completion=EMPTY)
    result = _gen(ai).generate_rsa(_group(), brand_name="Marka")
    assert result.missing_cta == 0 and ai.calls["completion"] == 0


def test_response_without_keyword_headlines_still_works():
    result = _gen(_FakeAI(_rsa(GENERIC[:15], KW_DESCS[:4]))).generate_rsa(_group(), brand_name="Marka")
    assert len(result.headlines) == 15 and result.keyword_decisions == []


def test_keyword_decisions_not_serialized():
    g = AdGroupFullSchema(name="a", theme="t", keyword_ids=[], keywords=[], headlines=[],
                          descriptions=[], negative_keywords=[],
                          keyword_decisions=[{"keyword": "x", "status": "declined", "reason": "r"}])
    assert "keyword_decisions" not in g.model_dump()


def test_derived_prompt_lists_keyword_ids_and_keyword_headlines():
    prompt = _gen(_FakeAI(EMPTY))._build_candidates_prompt(_group("ucuz laptop", "oyuncu laptop"), "Marka", "usp")
    assert "keyword_headlines" in prompt and "- 1: ucuz laptop" in prompt and "- 2: oyuncu laptop" in prompt
    assert "keyword_headlines" not in ADS_RSA_GENERATION_PROMPT
    assert "keyword_headlines" in RSAGenerator.RSA_RESPONSE_SCHEMA["required"]
    kept = ([HeadlineSchema(text="Ucuz Laptop Fırsatı", type="benefit")], [])
    completion = _gen(_FakeAI(EMPTY))._build_candidates_prompt(_group("ucuz laptop", "oyuncu laptop"), "Marka", "usp", kept)
    assert "- 2: oyuncu laptop" in completion and "- 1: ucuz laptop" not in completion
