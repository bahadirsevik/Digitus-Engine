"""DKI validator regresyon testleri.

Codex/log bulgusu: 'Lowercase keyword token' deseni koşullu IGNORECASE ile
aranıyordu → geçerli '{KeyWord:...}' başlıkları da yakalanıp düz metne
çevriliyordu; HİÇBİR dinamik başlık üretimden sağ çıkamıyordu (run-15/16'daki
dki_converted sayaçlarının tamamı bu false-positive'di). Bu dosya geçerli
DKI'nin yaşadığını ve gerçek geçersiz biçimlerin hâlâ yakalandığını kilitler.
"""
from app.generators.ads.validators import DKIValidator


validator = DKIValidator()


class TestValidDkiSurvives:
    def test_titlecase_keyword_valid(self):
        text, is_valid, reason = validator.validate("{KeyWord:Canlı Borsa}")
        assert is_valid is True
        assert reason is None
        assert text == "{KeyWord:Canlı Borsa}"  # değiştirilmedi

    def test_sentencecase_keyword_valid(self):
        text, is_valid, reason = validator.validate("En İyi {Keyword:Analiz}")
        assert is_valid is True
        assert reason is None
        assert text == "En İyi {Keyword:Analiz}"

    def test_plain_headline_passthrough(self):
        text, is_valid, reason = validator.validate("Hisse Analiz Araçları")
        assert is_valid is False
        assert reason is None
        assert text == "Hisse Analiz Araçları"


class TestInvalidDkiStillConverted:
    def test_lowercase_token_converted(self):
        # İş kuralı: küçük harf 'keyword' token'ı geçersiz (yalnız
        # KeyWord/Keyword) — case-SENSITIVE yakalanır
        text, is_valid, reason = validator.validate("{keyword:analiz}")
        assert is_valid is False
        assert reason == "Lowercase keyword token"
        assert "{" not in text

    def test_missing_default_converted(self):
        text, is_valid, reason = validator.validate("{KeyWord}")
        assert is_valid is False
        assert reason is not None
        assert "{" not in text

    def test_space_after_colon_converted(self):
        text, is_valid, reason = validator.validate("{KeyWord: Analiz}")
        assert is_valid is False
        assert reason == "Space after colon"
        assert "{" not in text

    def test_multiple_colons_converted(self):
        text, is_valid, reason = validator.validate("{KeyWord:A:B}")
        assert is_valid is False
        assert "{" not in text

    def test_unknown_braces_cleaned(self):
        text, is_valid, reason = validator.validate("{KEYWORD:Analiz}")
        assert is_valid is False
        assert "{" not in text


class TestRegenerationBraceGuard:
    def test_truncated_dki_fragment_rejected(self):
        """Düşünme kesintisi ürünü dengesiz parantezli parça (set-7'de
        '{KeyWord:Popüler Hiss' yayına girmişti) kabul EDİLMEZ — eleme
        yoluna düşer."""
        from app.generators.ads.validators import HeadlineValidator

        class TruncatingAI:
            def complete(self, prompt, max_tokens=None):
                return "{KeyWord:Popüler Hiss"

        hv = HeadlineValidator()
        text, action, reason = hv.validate(
            "Popüler ve En Çok İşlem Gören Hisse Senetleri",  # >30 karakter
            keyword="popüler hisseler",
            ai_service=TruncatingAI(),
            enable_regeneration=True,
        )
        assert action == "eliminated"
        assert text is None
