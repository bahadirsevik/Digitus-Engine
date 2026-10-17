"""
RSA Generator for Google Ads.

Generates complete RSA (Responsive Search Ads) components:
- Headlines (3-15, max 30 chars)
- Descriptions (2-4, max 90 chars)
- Negative Keywords (10+)
"""
import json
import re
from typing import List, Dict, Optional, Tuple, Any
from loguru import logger

from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_object
from app.core.constants import ADS_DESCRIPTIONS_PER_GROUP, ADS_HEADLINES_PER_GROUP
from app.generators.ai_service import AIService, scoped
from app.generators.ads.prompt_templates import (
    ADS_RSA_CANDIDATES_PROMPT,
    CLAIM_REWRITE_PROMPT,
)
from app.generators.ads.validators import (
    HeadlineValidator,
    DescriptionValidator,
    DKIValidator,
    find_ungrounded_claims,
    keyword_coverage,
    keyword_in_text,
    kw_tokens,
    negative_conflict,
    partition_negatives,
    _negative_tokens,
)
from app.schemas.ads import (
    KeywordGroupSchema,
    HeadlineSchema,
    DescriptionSchema,
    NegativeKeywordSchema,
    AdGroupFullSchema,
)


class RSAGenerator:
    """
    Generates RSA components for a single ad group.
    
    Includes validation:
    - 3-phase headline validation
    - Sentence-aware description truncation
    - DKI format validation
    """
    
    MIN_HEADLINES = 3
    MAX_HEADLINES = ADS_HEADLINES_PER_GROUP
    MIN_DESCRIPTIONS = 2
    MAX_DESCRIPTIONS = ADS_DESCRIPTIONS_PER_GROUP
    MIN_NEGATIVE_KEYWORDS = 10
    # Tamamlama çağrısında eksik sayının üstüne istenen yedek aday
    HEADLINE_SPARE = 3
    DESCRIPTION_SPARE = 2
    HEADLINE_FILL_MAX_ITERATIONS = 12
    DESCRIPTION_FILL_MAX_ITERATIONS = 8
    RSA_MAX_TOKENS = 8000
    MAX_AI_DESCRIPTION_SHORTEN_PER_GROUP = 2
    RSA_RESPONSE_SCHEMA: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "headlines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "type": {"type": "string"},
                        "position": {"type": "string"},
                    },
                    "required": ["text", "type", "position"]
                }
            },
            "descriptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "type": {"type": "string"},
                        "position": {"type": "string"},
                    },
                    "required": ["text", "type", "position"]
                }
            },
            "negative_keywords": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string"},
                        "match_type": {"type": "string"},
                        "category": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["keyword", "match_type", "category", "reason"]
                }
            }
        },
        "required": ["headlines", "descriptions", "negative_keywords"]
    }
    
    def __init__(
        self, 
        ai_service: AIService,
        enable_ai_regeneration: bool = True,
        enable_fallback: bool = True
    ):
        self.ai_service = scoped(ai_service, "ads_rsa")
        self.enable_ai_regeneration = enable_ai_regeneration
        self.enable_fallback = enable_fallback
        
        # Initialize validators
        self.headline_validator = HeadlineValidator()
        self.description_validator = DescriptionValidator()
        self.dki_validator = DKIValidator()

        # Grounding bağlamı (Faz F) — generate_rsa çağrısında set edilir
        self._product_facts = "—"
        self._grounding_facts = ""
    
    def generate_rsa(
        self,
        group: KeywordGroupSchema,
        brand_name: str = "",
        brand_usp: str = "",
        product_facts: str = "",
        grounding_facts: str = "",
    ) -> AdGroupFullSchema:
        """
        Generate complete RSA set for an ad group.

        Args:
            group: Keyword group to generate RSA for
            brand_name: Brand name for personalization
            brand_usp: Unique selling proposition
            product_facts: Confirmed profil ürün bilgisi — prompt'ta gösterilir
            grounding_facts: Claim whitelist kaynağı (confirmed product_facts +
                kullanıcının İSTEKTE verdiği USP; generic fallback USP GİRMEZ).
                Boşsa da grounding uygulanır — hiçbir sayısal iddia geçemez.

        Returns:
            AdGroupFullSchema with validated headlines, descriptions, negatives
        """
        logger.info(f"Generating RSA for group: '{group.name}' ({len(group.keywords)} keywords)")
        self._product_facts = (product_facts or "").strip() or "—"
        self._grounding_facts = (grounding_facts or "").strip()
        
        # Get primary keyword for validation context
        primary_keyword = group.keywords[0] if group.keywords else ""
        
        # Primary attempt: aday havuzu (doğrulama + iddia kontrolü SONRASI, kesme yok)
        h_pool, d_pool, negatives, headline_stats = self._generate_and_validate_components(
            group=group,
            brand_name=brand_name,
            brand_usp=brand_usp,
            primary_keyword=primary_keyword,
            stage="primary",
        )
        h_pool, d_pool = self._dedup(h_pool), self._dedup(d_pool)

        # Tek tamamlama çağrısı (eski tam strict retry'ın yerine). Yalnız KW
        # kapsam eksikliği tetiklemez (K4); temiz adaylar korunur.
        if self.enable_ai_regeneration and self._needs_completion(h_pool, d_pool, group.keywords):
            logger.warning(
                f"Standard not met for group '{group.name}' "
                f"(headlines={len(h_pool)}, descriptions={len(d_pool)}), one completion call"
            )
            try:
                c_h, c_d, c_negatives, c_stats = self._generate_and_validate_components(
                    group=group,
                    brand_name=brand_name,
                    brand_usp=brand_usp,
                    primary_keyword=primary_keyword,
                    stage="completion",
                    kept=(h_pool, d_pool),
                )
            except Exception as e:
                # Sert minimum zaten sağlanıyorsa temiz adaylar KAYBOLMAZ; değilse
                # eski retry gibi hata yukarı çıkar.
                if self._below_minimum(h_pool, d_pool):
                    raise
                logger.warning(f"Completion call failed for group '{group.name}', keeping clean candidates: {e}")
            else:
                h_pool = self._dedup(h_pool + c_h)
                d_pool = self._dedup(d_pool + c_d)
                negatives = negatives or c_negatives
                for k, v in c_stats.items():
                    headline_stats[k] = headline_stats.get(k, 0) + v

        headlines, descriptions = self._select_assets(h_pool, d_pool, group.keywords)

        # Deterministic fallback (yalnız sert minimum 3/2 altı; 15'e ASLA doldurmaz)
        if self._below_minimum(headlines, descriptions):
            if self.enable_fallback:
                logger.warning(
                    f"Using deterministic fallback for group '{group.name}' "
                    f"(headlines={len(headlines)}, descriptions={len(descriptions)})"
                )
                fb_h, fb_d, fb_negatives, fb_stats = self._build_deterministic_fallback(
                    group=group,
                    brand_name=brand_name,
                    brand_usp=brand_usp,
                    primary_keyword=primary_keyword
                )
                # Fallback asset'leri de grounding'den geçer (Faz F, adım 6) —
                # display USP fact değildir, sayısal iddia taşıyabilir
                fb_h, fb_d = self._enforce_grounding(fb_h, fb_d, group, fb_stats)
                # Seçilen TEMİZ asset'ler korunur; fallback yalnız eksik tipi,
                # temizlerin ARKASINA ve sert minimuma kadar tamamlar.
                headlines = self._top_up(headlines, fb_h, self.MIN_HEADLINES)
                descriptions = self._top_up(descriptions, fb_d, self.MIN_DESCRIPTIONS)
                negatives = negatives or fb_negatives
                for k, v in fb_stats.items():
                    headline_stats[k] = headline_stats.get(k, 0) + v
            else:
                raise ValueError(
                    f"Group '{group.name}': only {len(headlines)} valid headlines and "
                    f"{len(descriptions)} valid descriptions after retry. Group will not be saved."
                )

        # Guardrails after fallback
        if self._below_minimum(headlines, descriptions):
            raise ValueError(
                f"Group '{group.name}': fallback still below minimum "
                f"(headlines={len(headlines)}, descriptions={len(descriptions)})."
            )

        # Negatif ↔ hedef çakışması (plan 2.1): grubun KENDİ hedef kelimelerini
        # engelleyen negatifler atılır (birincil/retry/fallback hepsi buradan
        # geçer). Eksik kalan sayı için yalnız güvenli varsayılanlar eklenir;
        # güvenli aday yoksa DAHA AZ negatif döner (sayı doldurulmaz).
        negatives, dropped_negatives = partition_negatives(
            negatives, group.keywords, group.name
        )
        if len(negatives) < self.MIN_NEGATIVE_KEYWORDS:
            logger.warning(f"Only {len(negatives)} negative keywords, adding defaults")
            negatives = self._add_default_negatives(
                negatives, targets=group.keywords,
                excluded=[d["negative"] for d in dropped_negatives],
            )
        # Son emniyet: top-up dahil çıkan listede çakışan kalmasın
        negatives, late_drops = partition_negatives(
            negatives, group.keywords, group.name
        )
        dropped_negatives.extend(late_drops)
        for d in dropped_negatives:
            logger.warning(
                f"Negative dropped (blocks group target) | group={d['group']} "
                f"negative='{d['negative']}' match_type={d['match_type']} "
                f"target='{d['target']}' rule={d['rule']}"
            )

        # 15 başlık / 4 KW'li açıklama hedefi; eksik jenerik dolgu ile kapatılmaz.
        # KW kapsam eksikliği tek başına eksik sayılmaz (K4).
        missing_headlines = max(0, self.MAX_HEADLINES - len(headlines))
        missing_kw_descriptions = max(0, self.MAX_DESCRIPTIONS - sum(
            self._contains_keyword(d.text, group.keywords) for d in descriptions
        ))
        if missing_headlines or missing_kw_descriptions:
            logger.warning(
                f"Standard shortfall | group={group.name} "
                f"missing_headlines={missing_headlines} "
                f"missing_kw_descriptions={missing_kw_descriptions}"
            )

        # AI konum önerisi tutulmaz (bir grupta 15 başlığın hepsi position_1 gelmişti):
        # primary/tamamlama/claim rewrite/fallback hepsi buradan geçer -> tek normalizasyon.
        for asset in (*headlines, *descriptions):
            asset.position_preference = "any"

        return AdGroupFullSchema(
            name=group.name,
            theme=group.theme,
            keyword_ids=group.keyword_ids,
            keywords=group.keywords,
            fill_keyword_ids=group.fill_keyword_ids,
            headlines=headlines,
            descriptions=descriptions,
            negative_keywords=negatives,
            headlines_generated=headline_stats["total"],
            headlines_eliminated=headline_stats["eliminated"],
            headlines_shortened=headline_stats["shortened"],
            headlines_regenerated=headline_stats["regenerated"],
            dki_converted_count=headline_stats["dki_converted"],
            dropped_negatives=dropped_negatives,
            missing_headlines=missing_headlines,
            missing_kw_descriptions=missing_kw_descriptions,
        )

    def _below_minimum(
        self,
        headlines: List[HeadlineSchema],
        descriptions: List[DescriptionSchema]
    ) -> bool:
        return len(headlines) < self.MIN_HEADLINES or len(descriptions) < self.MIN_DESCRIPTIONS

    @staticmethod
    def _contains_keyword(text: str, keywords: List[str]) -> bool:
        return any(keyword_in_text(kw, text) for kw in keywords)

    def _needs_completion(self, h_pool, d_pool, keywords: List[str]) -> bool:
        """15 başlık veya 4 KW'li açıklama dolmadı mı? (sert minimum bunun içinde)"""
        kw_descriptions = sum(self._contains_keyword(d.text, keywords) for d in d_pool)
        return len(h_pool) < self.MAX_HEADLINES or kw_descriptions < self.MAX_DESCRIPTIONS

    def _top_up(self, clean: list, fallback: list, minimum: int) -> list:
        """`minimum`un altındaki tipi fallback asset'leriyle, temizlerin ARKASINA ve
        yalnız minimuma kadar tamamlar (son metinde tekrar yok). Yeterliyse dokunmaz."""
        if len(clean) >= minimum:
            return clean
        return self._dedup(clean + fallback)[:minimum]

    @staticmethod
    def _dedup(items: list) -> list:
        """SON metinlerde (iddia düzeltmesi sonrası) tekrar temizliği; ilk gelen kalır.
        Anahtar DKI açılmış, Türkçe katlanmış, noktalamasız: `{KeyWord:X}` == "X"."""
        seen, out = set(), []
        for item in items:
            key = tuple(kw_tokens(item.text))
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out

    @staticmethod
    def _greedy_pick(texts: List[str], keywords: List[str], limit: int) -> List[int]:
        """Her adımda henüz kapsanmamış en çok KW'yi kapsayan metni al (eşitlikte
        öndeki; kapsam artışı bitince aday sırasıyla dolar). `limit`e kadar indeks."""
        uncovered, left, chosen = list(keywords), list(range(len(texts))), []
        while left and len(chosen) < limit:
            best = max(left, key=lambda i: sum(keyword_in_text(k, texts[i]) for k in uncovered))
            chosen.append(best)
            left.remove(best)
            uncovered = [k for k in uncovered if not keyword_in_text(k, texts[best])]
        return chosen

    def _select_assets(
        self, h_pool: List[HeadlineSchema], d_pool: List[DescriptionSchema], keywords: List[str]
    ) -> Tuple[List[HeadlineSchema], List[DescriptionSchema]]:
        """Havuzdan 15 başlık / 4 açıklama seç (deterministik; çıktı aday sırasında).

        Açıklama: önce KW içerenler (başlıklarda karşılanmamış KW'yi taşıyan öncelikli);
        KW'li aday 4'ten azsa KW'siz temiz açıklamalar dolgu olur (standarda sayılmaz).
        """
        headlines = [h_pool[i] for i in sorted(
            self._greedy_pick([h.text for h in h_pool], keywords, self.MAX_HEADLINES))]
        uncovered = [k for k in keywords if not any(keyword_in_text(k, h.text) for h in headlines)]
        kw_idx = [i for i, d in enumerate(d_pool) if self._contains_keyword(d.text, keywords)]
        picked = [kw_idx[j] for j in self._greedy_pick(
            [d_pool[i].text for i in kw_idx], uncovered, self.MAX_DESCRIPTIONS)]
        picked += [i for i in range(len(d_pool)) if i not in kw_idx][:self.MAX_DESCRIPTIONS - len(picked)]
        return headlines, [d_pool[i] for i in sorted(picked)]

    def _generate_and_validate_components(
        self,
        group: KeywordGroupSchema,
        brand_name: str,
        brand_usp: str,
        primary_keyword: str,
        stage: str,
        kept: Optional[Tuple[List[HeadlineSchema], List[DescriptionSchema]]] = None,
    ) -> Tuple[List[HeadlineSchema], List[DescriptionSchema], List[NegativeKeywordSchema], Dict[str, int]]:
        raw_response = self._generate_raw_rsa(
            group=group,
            brand_name=brand_name,
            brand_usp=brand_usp,
            kept=kept,
        )
        headlines_raw, descriptions_raw, negatives_raw = self._parse_rsa_response(
            response=raw_response,
            group_name=group.name,
            stage=stage
        )
        headlines, headline_stats = self._validate_headlines(headlines_raw, primary_keyword)
        descriptions, _ = self._validate_descriptions(
            descriptions_raw=descriptions_raw,
            ai_service=self.ai_service if self.enable_ai_regeneration else None,
            enable_ai_shortening=self.enable_ai_regeneration
        )
        negatives = self._parse_negatives(negatives_raw)
        # Grounding (Faz F): desteksiz iddialı asset rewrite (1 kez) veya elenir
        headlines, descriptions = self._enforce_grounding(
            headlines, descriptions, group, headline_stats
        )
        return headlines, descriptions, negatives, headline_stats

    def _enforce_grounding(
        self,
        headlines: List[HeadlineSchema],
        descriptions: List[DescriptionSchema],
        group: KeywordGroupSchema,
        headline_stats: Dict[str, int],
    ) -> Tuple[List[HeadlineSchema], List[DescriptionSchema]]:
        """Claim grounding zinciri (Faz F3, kesin sıra 2-4).

        TÜM headline+description'lar taranır; ihlalli asset için en fazla
        1 kez CLAIM_REWRITE denenir; çözülmeyen asset ELENİR. Deterministik
        fallback asset'leri de bu yoldan geçer (çağıran uygular).
        """
        exempt = group.keywords or []
        primary_keyword = exempt[0] if exempt else group.name

        kept_headlines: List[HeadlineSchema] = []
        for h in headlines:
            claims = find_ungrounded_claims(h.text, self._grounding_facts, exempt)
            if not claims:
                kept_headlines.append(h)
                continue
            rewritten = self._rewrite_claim_asset(
                h.text, claims, "headline", primary_keyword,
                self.headline_validator.MAX_LENGTH, exempt,
            )
            if rewritten:
                logger.info(
                    f"Headline claim-rewritten: '{h.text}' -> '{rewritten}' "
                    f"(claims={claims})"
                )
                kept_headlines.append(HeadlineSchema(
                    text=rewritten, type=h.type,
                    position_preference=h.position_preference, is_dki=False,
                ))
            else:
                logger.warning(
                    f"Headline eliminated (ungrounded claim): '{h.text}' "
                    f"claims={claims}"
                )
                headline_stats["eliminated"] = headline_stats.get("eliminated", 0) + 1

        kept_descriptions: List[DescriptionSchema] = []
        for d in descriptions:
            claims = find_ungrounded_claims(d.text, self._grounding_facts, exempt)
            if not claims:
                kept_descriptions.append(d)
                continue
            rewritten = self._rewrite_claim_asset(
                d.text, claims, "description", primary_keyword,
                self.description_validator.MAX_LENGTH, exempt,
            )
            if rewritten:
                logger.info(
                    f"Description claim-rewritten: '{d.text}' -> '{rewritten}' "
                    f"(claims={claims})"
                )
                kept_descriptions.append(DescriptionSchema(
                    text=rewritten, type=d.type,
                    position_preference=d.position_preference,
                ))
            else:
                logger.warning(
                    f"Description eliminated (ungrounded claim): '{d.text}' "
                    f"claims={claims}"
                )

        return kept_headlines, kept_descriptions

    def _rewrite_claim_asset(
        self,
        original: str,
        claims: List[str],
        asset_type: str,
        keyword: str,
        max_length: int,
        exempt_keywords: Optional[List[str]] = None,
    ) -> Optional[str]:
        """İhlalli asset'i 1 kez yeniden yazdırır; sonuç yine ihlalliyse None."""
        if not self.enable_ai_regeneration:
            return None
        try:
            prompt = CLAIM_REWRITE_PROMPT.format(
                asset_type=asset_type,
                original=original,
                claims="\n".join(f"- {c}" for c in claims),
                grounding_facts=self._grounding_facts or "— (marka bilgisi yok: hiçbir iddia kullanma)",
                keyword=keyword,
                max_length=max_length,
            )
            result = (scoped(self.ai_service, "claim_rewrite").complete(prompt, max_tokens=800) or "").strip().strip('"\'')
        except Exception as e:
            logger.warning(f"Claim rewrite error: {e}")
            return None
        if not result or len(result) > max_length:
            return None
        # Rewrite sonrası ikinci kontrol — keyword muafiyeti KORUNUR (codex):
        # keyword'deki doğal sayı ("2026") rewrite'ta yanlış eleme üretmesin
        if find_ungrounded_claims(result, self._grounding_facts, exempt_keywords):
            return None
        return result
    
    def _generate_raw_rsa(
        self,
        group: KeywordGroupSchema,
        brand_name: str,
        brand_usp: str,
        kept: Optional[Tuple[List[HeadlineSchema], List[DescriptionSchema]]] = None,
    ) -> str:
        """Generate raw RSA candidates via AI (kept=None: primary; else completion)."""
        brand_name = (brand_name or "Marka").strip() or "Marka"
        # USP yoksa "—": jenerik metin modele USP diye verilince reklam metnine sızıyordu
        brand_usp = (brand_usp or "").strip() or "—"

        prompt = self._build_candidates_prompt(group, brand_name, brand_usp, kept)
        response = self.ai_service.complete_json(
            prompt,
            max_tokens=self.RSA_MAX_TOKENS,
            response_schema=self.RSA_RESPONSE_SCHEMA
        )
        return response

    def _build_candidates_prompt(
        self,
        group: KeywordGroupSchema,
        brand_name: str,
        brand_usp: str,
        kept: Optional[Tuple[List[HeadlineSchema], List[DescriptionSchema]]] = None,
    ) -> str:
        """Primary: 18-20 başlık / 5-6 açıklama adayı. Completion (kept verilir): yalnız
        EKSİK sayı + yedek; mevcut temiz metinler "tekrar etme" olarak listelenir."""
        keywords = group.keywords
        if kept is None:
            h_instr = "18-20 aday başlık üret (sistem içlerinden en iyi 15'ini seçer)"
            d_instr = "5-6 aday açıklama üret (sistem içlerinden en iyi 4'ünü seçer)"
            block = ""
        else:
            kept_h, kept_d = kept
            need_h = max(0, self.MAX_HEADLINES - len(kept_h))
            need_d = max(0, self.MAX_DESCRIPTIONS - sum(
                self._contains_keyword(d.text, keywords) for d in kept_d))
            h_instr = (f"{need_h + self.HEADLINE_SPARE} aday başlık üret" if need_h
                       else "başlık ÜRETME ('headlines' dizisini boş bırak)")
            d_instr = (f"{need_d + self.DESCRIPTION_SPARE} aday açıklama üret "
                       "(her birinde, cümleye doğal yerleşen en az bir anahtar kelime BÜTÜN geçmeli; "
                       "diğerlerini zorla doldurma)" if need_d
                       else "açıklama ÜRETME ('descriptions' dizisini boş bırak)")
            lines = []
            if kept_h or kept_d:
                lines += [
                    "# TAMAMLAMA TURU",
                    "Bu grup için daha önce üretilen temiz metinler aşağıdadır. Bunları ve anlamca "
                    "tekrarlarını ÜRETME; marka adı (en fazla 3 başlık) ve çeşitlilik kuralları "
                    "bu mevcut başlıklarla BİRLİKTE geçerlidir.",
                ]
                if kept_h:
                    lines += ["Mevcut başlıklar:"] + [f"- {h.text}" for h in kept_h]
                if kept_d:
                    lines += ["Mevcut açıklamalar:"] + [f"- {d.text}" for d in kept_d]
                if need_h:
                    coverage = keyword_coverage(keywords, [h.text for h in kept_h], [])
                    missing = [k for k in keywords if k.strip() and coverage[k] != "headline"
                               and len(k) <= self.headline_validator.MAX_LENGTH]
                    if missing:
                        lines.append(
                            "Henüz hiçbir başlıkta geçmeyen ve 30 karaktere sığan anahtar kelimeler "
                            "(doğal okunduğu yerde başlıklarda BÜTÜN kullan; doğal başlık olmuyorsa "
                            "zorlama, anlamı doğal Türkçeyle anlat): " + ", ".join(missing)
                        )
            block = "\n".join([""] + lines) if lines else ""

        long_keywords = [k for k in keywords if len(k) > self.headline_validator.MAX_LENGTH]
        return ADS_RSA_CANDIDATES_PROMPT.format(
            group_name=group.name,
            group_theme=group.theme,
            keywords=", ".join(keywords),
            brand_name=brand_name,
            brand_usp=brand_usp,
            product_facts=self._product_facts,
            headline_instruction=h_instr,
            description_instruction=d_instr,
            long_keywords=", ".join(long_keywords) or "—",
            completion_block=block,
        )
    
    def _parse_rsa_response(
        self,
        response: str,
        group_name: str,
        stage: str
    ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """Parse AI response into raw components."""
        try:
            # Kurtarma zinciri (fence/trailing virgul/dengesiz parantez);
            # parse hatasi bos tuple doner -> tamamlama cagrisi tam uretim gibi calisir
            data = parse_ai_json_object(
                response, ("headlines", "descriptions", "negative_keywords")
            )
            if not isinstance(data.get("headlines"), list):
                raise ValueError("RSA response missing 'headlines' array")
            if not isinstance(data.get("descriptions"), list):
                raise ValueError("RSA response missing 'descriptions' array")
            if not isinstance(data.get("negative_keywords"), list):
                raise ValueError("RSA response missing 'negative_keywords' array")
            
            headlines = data.get("headlines", [])
            descriptions = data.get("descriptions", [])
            negatives = data.get("negative_keywords", [])
            
            return headlines, descriptions, negatives
            
        except (json.JSONDecodeError, AIJsonParseError) as e:
            snippet = response[:200].replace("\n", " ")
            logger.error(
                f"RSA parse failed | group_name={group_name} | stage={stage} | "
                f"exception_type={type(e).__name__} | snippet={snippet}"
            )
            return [], [], []
        except Exception as e:
            snippet = response[:200].replace("\n", " ")
            logger.error(
                f"RSA validation failed | group_name={group_name} | stage={stage} | "
                f"exception_type={type(e).__name__} | snippet={snippet}"
            )
            return [], [], []
    
    def _validate_headlines(
        self, 
        headlines_raw: List[Dict],
        primary_keyword: str
    ) -> Tuple[List[HeadlineSchema], Dict[str, int]]:
        """
        Validate headlines with 3-phase validation.
        
        Returns:
            Tuple of (validated headlines, stats dict)
        """
        validated = []
        stats = {
            "total": len(headlines_raw),
            "kept": 0,
            "shortened": 0,
            "regenerated": 0,
            "eliminated": 0,
            "dki_converted": 0,
        }
        
        for i, h in enumerate(headlines_raw):
            text = h.get("text", "")
            h_type = h.get("type", "keyword")
            position = h.get("position", "any")
            
            if not text:
                stats["eliminated"] += 1
                continue
            
            # Step 1: DKI validation (if dynamic type)
            is_dki = False
            if h_type == "dynamic" or "{" in text:
                text, is_dki, dki_reason = self.dki_validator.validate(text)
                if dki_reason:
                    stats["dki_converted"] += 1
                    h_type = "keyword"  # Downgrade type if DKI was invalid
            
            # Step 2: Character limit validation
            validated_text, action, reason = self.headline_validator.validate(
                headline=text,
                keyword=primary_keyword,
                ai_service=self.ai_service if self.enable_ai_regeneration else None,
                enable_regeneration=self.enable_ai_regeneration
            )
            
            # Track stats
            stats[action] = stats.get(action, 0) + 1
            
            if validated_text:
                validated.append(HeadlineSchema(
                    text=validated_text,
                    type=h_type,
                    position_preference=position,
                    is_dki=is_dki
                ))
        
        # Kesme YOK: iddia kontrolü ve tekrar temizliği sonrası seçim yapılır (_select_assets)
        logger.info(f"Headlines: {stats['total']} total, {len(validated)} valid, "
                   f"{stats['eliminated']} eliminated, {stats['dki_converted']} DKI fixed")
        
        return validated, stats
    
    def _validate_descriptions(
        self, 
        descriptions_raw: List[Dict],
        ai_service: Optional[AIService] = None,
        enable_ai_shortening: bool = True
    ) -> Tuple[List[DescriptionSchema], Dict[str, int]]:
        """
        Validate descriptions with sentence-aware and optional AI shortening.
        """
        validated = []
        stats = {
            "total": len(descriptions_raw),
            "kept": 0,
            "sentence_trim": 0,
            "ai_shortened": 0,
            "truncated": 0,
        }
        ai_attempts_remaining = self.MAX_AI_DESCRIPTION_SHORTEN_PER_GROUP
        
        for d in descriptions_raw:
            text = d.get("text", "")
            d_type = d.get("type", "value_prop")
            position = d.get("position", "any")
            
            if not text:
                continue
            
            allow_ai_this_item = bool(
                ai_service and enable_ai_shortening and ai_attempts_remaining > 0
            )
            validated_text, action, reason = self.description_validator.validate(
                text,
                ai_service=ai_service if allow_ai_this_item else None,
                enable_ai_shortening=allow_ai_this_item
            )
            ai_attempted = (
                action == "ai_shortened" or
                (action == "truncated" and reason == "AI shortening failed, word-boundary fallback")
            )
            if ai_attempted and ai_attempts_remaining > 0:
                ai_attempts_remaining -= 1

            stats[action] = stats.get(action, 0) + 1

            if not validated_text:
                continue
            
            validated.append(DescriptionSchema(
                text=validated_text,
                type=d_type,
                position_preference=position
            ))
        
        # Kesme YOK: seçim _select_assets'te
        logger.info(
            f"Descriptions: {stats['total']} total, {len(validated)} valid, "
            f"{stats['ai_shortened']} ai_shortened, {stats['truncated']} truncated"
        )
        
        return validated, stats
    
    def _parse_negatives(
        self, 
        negatives_raw: List[Dict]
    ) -> List[NegativeKeywordSchema]:
        """Parse negative keywords."""
        negatives = []
        
        for n in negatives_raw:
            keyword = n.get("keyword", "")
            if not keyword:
                continue
            
            negatives.append(NegativeKeywordSchema(
                keyword=keyword,
                match_type=n.get("match_type", "phrase"),
                category=n.get("category"),
                reason=n.get("reason")
            ))
        
        return negatives

    def _build_deterministic_fallback(
        self,
        group: KeywordGroupSchema,
        brand_name: str,
        brand_usp: str,
        primary_keyword: str
    ) -> Tuple[List[HeadlineSchema], List[DescriptionSchema], List[NegativeKeywordSchema], Dict[str, int]]:
        brand_name = (brand_name or "Marka").strip() or "Marka"
        brand_usp = (brand_usp or "").strip()
        keywords = [k.strip() for k in group.keywords if k and k.strip()]
        first_keyword = keywords[0] if keywords else primary_keyword or group.name
        second_keyword = keywords[1] if len(keywords) > 1 else first_keyword

        headline_candidates = [
            {"text": first_keyword, "type": "keyword", "position": "position_1"},
            {"text": f"{brand_name} {second_keyword}", "type": "keyword", "position": "any"},
            {"text": "Hemen İncele", "type": "cta", "position": "position_3"},
            {"text": f"{brand_name} Güvencesi", "type": "trust", "position": "any"},
            {"text": "Kaliteli Ürün Seçenekleri", "type": "benefit", "position": "any"},
        ]
        description_candidates = [
            {
                "text": f"{brand_name} ile {first_keyword} seçeneklerini keşfedin. Hemen inceleyin.",
                "type": "value_prop",
                "position": "any"
            },
            {
                "text": f"{brand_usp + '. ' if brand_usp else ''}Güvenli alışveriş ve hızlı teslimat fırsatını kaçırmayın.",
                "type": "cta",
                "position": "any"
            }
        ]

        headlines, headline_stats = self._validate_headlines(headline_candidates, first_keyword)
        descriptions, _ = self._validate_descriptions(description_candidates)
        negatives = self._add_default_negatives([], targets=keywords)

        # Hard guarantees for minimum outputs.
        headline_fill_iterations = 0
        while len(headlines) < self.MIN_HEADLINES and headline_fill_iterations < self.HEADLINE_FILL_MAX_ITERATIONS:
            headline_fill_iterations += 1
            idx = len(headlines) + 1
            fallback_text = f"{brand_name} Ürünleri {idx}"
            validated_text, _, _ = self.headline_validator.validate(
                fallback_text,
                first_keyword,
                ai_service=None,
                enable_regeneration=False
            )
            if not validated_text:
                validated_text = fallback_text[:self.headline_validator.MAX_LENGTH].strip()
            if not validated_text:
                validated_text = (brand_name or "Marka")[:self.headline_validator.MAX_LENGTH].strip()
            if not validated_text:
                continue
            headlines.append(HeadlineSchema(
                text=validated_text,
                type="keyword",
                position_preference="any",
                is_dki=False
            ))

        if len(headlines) < self.MIN_HEADLINES:
            raise ValueError(
                f"Deterministic fallback could not build minimum headlines "
                f"(built={len(headlines)}, required={self.MIN_HEADLINES})."
            )

        description_fill_templates = [
            f"{brand_name} ürünlerini hemen inceleyin ve size uygun seçeneği bulun.",
            f"{brand_usp}. Hemen keşfedin ve hızlı teslimat fırsatından yararlanın.",
            f"{first_keyword} seçeneklerini karşılaştırın, sizin için doğru ürünü şimdi seçin.",
            f"{brand_name} kalitesiyle güvenli alışveriş deneyimini hemen başlatın."
        ]
        description_fill_iterations = 0
        while len(descriptions) < self.MIN_DESCRIPTIONS and description_fill_iterations < self.DESCRIPTION_FILL_MAX_ITERATIONS:
            text = description_fill_templates[description_fill_iterations % len(description_fill_templates)]
            description_fill_iterations += 1
            validated_text, _, _ = self.description_validator.validate(text)
            if not validated_text:
                validated_text = text[:self.description_validator.MAX_LENGTH].strip()
            if not validated_text:
                continue
            descriptions.append(DescriptionSchema(
                text=validated_text,
                type="value_prop",
                position_preference="any"
            ))

        if len(descriptions) < self.MIN_DESCRIPTIONS:
            raise ValueError(
                f"Deterministic fallback could not build minimum descriptions "
                f"(built={len(descriptions)}, required={self.MIN_DESCRIPTIONS})."
            )

        return headlines, descriptions, negatives, headline_stats
    
    def _add_default_negatives(
        self, 
        existing: List[NegativeKeywordSchema],
        targets: Optional[List[str]] = None,
        excluded: Optional[List[str]] = None,
    ) -> List[NegativeKeywordSchema]:
        """Add default negative keywords if below minimum.

        Yalnız grubun hedef kelimeleriyle çakışmayan varsayılanlar eklenir
        (güvenli aday yoksa hiçbiri eklenmez). Tekilleştirme normalize
        biçim üzerinden; `excluded` (daha önce atılan negatifler) geri
        eklenmez.
        """
        defaults = [
            ("nedir", "phrase", "bilgi_amacli", "Bilgi amaçlı arama"),
            ("nasıl", "phrase", "bilgi_amacli", "Bilgi amaçlı arama"),
            ("ne demek", "phrase", "bilgi_amacli", "Bilgi amaçlı arama"),
            ("ücretsiz", "phrase", "ucretsiz", "Ödeme niyeti yok"),
            ("bedava", "phrase", "ucretsiz", "Ödeme niyeti yok"),
            ("free", "phrase", "ucretsiz", "Ödeme niyeti yok"),
            ("ucuz", "phrase", "dusuk_kalite", "Düşük kalite algısı"),
            ("ikinci el", "phrase", "ikinci_el", "Yeni ürün satışı"),
            ("şikayet", "phrase", "sikayet", "Negatif arama"),
            ("tamir", "phrase", "diy", "Hizmet değil bilgi arıyor"),
        ]
        
        seen = {tuple(_negative_tokens(n.keyword)) for n in existing}
        seen.update(tuple(_negative_tokens(x)) for x in (excluded or []))

        result = list(existing)
        for keyword, match_type, category, reason in defaults:
            key = tuple(_negative_tokens(keyword))
            if key in seen:
                continue
            if negative_conflict(keyword, match_type, targets):
                continue
            seen.add(key)
            result.append(NegativeKeywordSchema(
                keyword=keyword,
                match_type=match_type,
                category=category,
                reason=reason
            ))

        return result
