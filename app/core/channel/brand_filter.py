"""Brand-level exclusion filter for channel assignment.

This filter uses the confirmed BrandProfile's ``exclude_themes`` to remove
semantically off-brand keywords before channel-specific pre-filters run.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.core.constants import BRAND_FILTER_BATCH_SIZE, BRAND_FILTER_MAX_TOKENS
from app.core.channel.ai_budget import AiBudgetExhausted, AiCallBudget
from app.core.telemetry.ai_cost_budget import BudgetError
from app.core.channel.ai_json import parse_ai_json_list
from app.database.models import BrandProfile, IntentAnalysis, Keyword, PreFilterResult, ScoringRun
from app.generators.ai_service import AIService, scoped

logger = logging.getLogger(__name__)

BRAND_EXCLUDED_REASON = "BRAND_EXCLUDED_THEME"


class BrandExclusionFilter:
    """Shared, channel-independent brand exclusion layer."""

    # Gemini'yi geçerli JSON'a zorlar → parse hatalarını kaynağında keser (A2).
    # Faz C: korunan/dışlanan eşleşme AYRI alanlarda raporlanır — tek alanla
    # "hangi taraf kazandı" bilgisi audit'e yazılamıyordu.
    RESPONSE_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "keyword_id": {"type": "integer"},
                        "is_brand_relevant": {"type": "boolean"},
                        "matched_protected_theme": {"type": "string"},
                        "matched_exclude_theme": {"type": "string"},
                    },
                    # Iki tema alani da ZORUNLU: "korunan tema var mi?" sorusu
                    # yanitlanmadan eleme kararina guvenilemez (Codex #1).
                    # Eksik alan sozlesme ihlalidir ve terminal eleme uretemez.
                    "required": [
                        "keyword_id",
                        "is_brand_relevant",
                        "matched_protected_theme",
                        "matched_exclude_theme",
                    ],
                },
            }
        },
        "required": ["results"],
    }

    PROFILE_FIELDS = (
        "products",
        "services",
        "target_audience",
        "use_cases",
        "problems_solved",
        "brand_terms",
        "protected_themes",
        "exclude_themes",
    )

    def __init__(self, db: Session, ai_service: AIService):
        self.db = db
        self.ai_service = scoped(ai_service, "brand_filter")
        self._ai_calls_used = 0
        self._budget: AiCallBudget | None = None
        # Kanonik tema listeleri: modelin beyan ettiği tema bunlara oturmalı
        self._exclude_themes: list[str] = []
        self._protected_themes: list[str] = []

    def filter_intent_passed(
        self,
        scoring_run_id: int,
        keyword_ids: list[int] | None = None,
        channels: list[str] | None = None,
        progress_callback=None,
        budget: AiCallBudget | None = None,
    ) -> dict[str, Any]:
        """Evaluate intent-passed candidates and write brand exclusions.

        Returns a compact summary. If there is no confirmed profile or no
        exclude_themes, this is a no-op. Erken dönüşler dahil TÜM dönüşler
        ai_calls_used taşır (expansion bütçe muhasebesi sözleşmesi).
        """
        self._ai_calls_used = 0
        self._budget = budget

        run = self.db.query(ScoringRun).filter(ScoringRun.id == scoring_run_id).first()
        if not run or not run.brand_profile_id:
            return {"status": "skipped", "reason": "no_brand_profile",
                    "excluded": 0, "ai_calls_used": 0}

        profile = (
            self.db.query(BrandProfile)
            .filter(
                BrandProfile.id == run.brand_profile_id,
                BrandProfile.status == "confirmed",
                BrandProfile.deleted_at.is_(None),
            )
            .first()
        )
        if not profile:
            return {"status": "skipped", "reason": "profile_not_confirmed",
                    "excluded": 0, "ai_calls_used": 0}

        profile_data = profile.profile_data if isinstance(profile.profile_data, dict) else {}
        exclude_themes = self._normalize_list(profile_data.get("exclude_themes"))
        # Faz C: korunacak temalar kanonik profil alanıdır; eski profillerde
        # yoksa boş liste ile geriye uyumlu çalışır.
        self._protected_themes = self._normalize_list(
            profile_data.get("protected_themes")
        )
        self._exclude_themes = exclude_themes
        if not exclude_themes:
            # Dışlanacak tema yoksa elenecek bir şey de yok — korunacak tema
            # tek başına eleme üretmez, yalnız elemeye karşı savunur.
            return {"status": "skipped", "reason": "no_exclude_themes",
                    "excluded": 0, "ai_calls_used": 0}

        candidates_by_keyword = self._load_intent_passed_candidates(
            scoring_run_id,
            keyword_ids=keyword_ids,
            channels=channels,
        )
        if not candidates_by_keyword:
            return {"status": "completed", "excluded": 0, "evaluated": 0,
                    "ai_calls_used": 0}

        keyword_rows = [
            {"id": keyword_id, "keyword": data["keyword"]}
            for keyword_id, data in candidates_by_keyword.items()
        ]

        result_map: dict[int, dict[str, Any]] = {}
        # Görünürlük metrikleri (A4)
        metrics = {
            "split_retries": 0,
            "failed_keyword_ids": set(),
            "recovered_ids": set(),
            "ghost_ids": 0,        # batch'e ait olmayan uydurma keyword_id
            "duplicate_ids": 0,    # aynı ID birden fazla kez döndü
        }
        failed_batches = 0
        for i in range(0, len(keyword_rows), BRAND_FILTER_BATCH_SIZE):
            batch = keyword_rows[i:i + BRAND_FILTER_BATCH_SIZE]
            retries_before = metrics["split_retries"]
            failed_before = len(metrics["failed_keyword_ids"])
            resolved = self._evaluate_with_recovery(
                batch, profile_data, exclude_themes, metrics
            )
            if progress_callback:
                progress_callback("BRAND", 1)
            for keyword_id_int, result in resolved.items():
                if keyword_id_int in candidates_by_keyword:
                    result_map[keyword_id_int] = result
            # Bu üst-seviye batch, split veya fail-safe gerektirdiyse "başarısız" say
            if (metrics["split_retries"] > retries_before
                    or len(metrics["failed_keyword_ids"]) > failed_before):
                failed_batches += 1

        excluded_rows = 0
        audit = {
            "protected_matches": 0,       # geçerli korunan tema eşleşmesi
            "protected_overrides": 0,     # model false dedi, koruma kazandı
            "protected_deterministic": 0,  # koruma keyword metninden geldi
            "theme_contract_violations": 0,  # listede olmayan/eksik tema beyanı
            "unbacked_exclusions": 0,     # kanonik temaya dayanmayan false
        }
        # Codex #3: audit YALNIZ elenen satırlara yazılamaz — "model eledi ama
        # koruma kurtardı" vakaları sonradan görünmez kalırdı. Değerlendirilen
        # HER keyword'ün kararı burada toplanır ve dönüş sözleşmesiyle task
        # result_data'ya (steps.brand_filtering) kalıcı olarak yazılır.
        decisions: list[dict[str, Any]] = []
        for keyword_id, result in sorted(result_map.items()):
            keyword_text = candidates_by_keyword[keyword_id]["keyword"]
            decision = self._resolve_decision(result, keyword_text, audit)
            decisions.append({
                "keyword_id": keyword_id,
                "keyword": keyword_text,
                "model_decision": decision["model_relevant"],
                "applied_decision": decision["applied_relevant"],
                "theme_winner": decision["winner"],
                "matched_protected_theme": decision["matched_protected_theme"],
                "matched_exclude_theme": decision["matched_exclude_theme"],
                "channels": list(candidates_by_keyword[keyword_id]["channels"]),
            })
            if decision["applied_relevant"]:
                continue
            for channel in candidates_by_keyword[keyword_id]["channels"]:
                self._upsert_brand_exclusion(
                    scoring_run_id=scoring_run_id,
                    keyword_id=keyword_id,
                    channel=channel,
                    reason=str(result.get("reason") or "Marka dışlama temasına uyuyor"),
                    decision=decision,
                )
                excluded_rows += 1

        # Çözülemeyen keyword'ler de audit'e girer (Codex #6): fail-safe ile
        # marka-uygun sayıldılar ama bu bir KARAR değil, cevapsızlıktır —
        # raporda görünmezse "değerlendirildi" sanılır.
        for keyword_id in sorted(set(candidates_by_keyword) - set(result_map)):
            decisions.append({
                "keyword_id": keyword_id,
                "keyword": candidates_by_keyword[keyword_id]["keyword"],
                "model_decision": None,
                "applied_decision": True,
                "theme_winner": "unresolved_fail_safe",
                "matched_protected_theme": None,
                "matched_exclude_theme": None,
                "channels": list(candidates_by_keyword[keyword_id]["channels"]),
            })

        self.db.commit()
        if audit["protected_overrides"] or audit["theme_contract_violations"]:
            logger.info(
                "Brand filter run %s: koruma önceliği %s kez elemeyi çevirdi; "
                "%s tema sözleşmesi ihlali; %s dayanaksız eleme korundu",
                scoring_run_id, audit["protected_overrides"],
                audit["theme_contract_violations"], audit["unbacked_exclusions"],
            )
        failed_keywords = len(metrics["failed_keyword_ids"])
        # recovered = split ile çözülenlerden fail-safe'e düşmeyenler
        recovered_keywords = len(metrics["recovered_ids"] - metrics["failed_keyword_ids"])
        if failed_keywords:
            logger.warning(
                "Brand exclusion filter run %s: %s keyword çözülemedi (fail-safe brand-relevant): %s",
                scoring_run_id, failed_keywords, sorted(metrics["failed_keyword_ids"]),
            )
        return {
            "status": "completed",
            "evaluated": len(keyword_rows),
            "excluded": excluded_rows,
            "failed_batches": failed_batches,
            "failed_keywords": failed_keywords,
            "recovered_keywords": recovered_keywords,
            "split_retries": metrics["split_retries"],
            "ghost_ids": metrics["ghost_ids"],
            "duplicate_ids": metrics["duplicate_ids"],
            # batches: gösterim metriği; bütçe muhasebesi ai_calls_used kullanır
            "batches": (len(keyword_rows) + BRAND_FILTER_BATCH_SIZE - 1) // BRAND_FILTER_BATCH_SIZE,
            "ai_calls_used": self._ai_calls_used,
            **audit,
            # Kalıcı per-keyword audit (task result_data'ya akar)
            "decisions": decisions,
        }

    @staticmethod
    def _coerce_keyword_id(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _evaluate_with_recovery(
        self,
        batch: list[dict[str, Any]],
        profile_data: dict[str, Any],
        exclude_themes: list[str],
        metrics: dict[str, Any],
        *,
        via_split: bool = False,
    ) -> dict[int, dict[str, Any]]:
        """Batch'i değerlendir; parse/eksik sonuçta böl ve yeniden dene (A3).

        Dönüş: {keyword_id: result}. Tekil kelime bile çözülemezse fail-safe
        olarak sonuçtan çıkarılır (brand-relevant kabul; dışlanmaz).
        """
        batch_ids = {kw["id"] for kw in batch}
        try:
            raw_results = self._evaluate_batch(batch, profile_data, exclude_themes)
        except AiBudgetExhausted:
            # HARD bütçe bitti — split retry YOK; tüm batch fail-safe
            # brand-relevant kabul edilir (mevcut hata semantiğiyle aynı yön:
            # dışlanmaz, sadece bilinmiyor olarak işaretlenir).
            metrics["failed_keyword_ids"].update(batch_ids)
            logger.warning(
                "Brand filter: bütçe tükendi, %s keyword fail-safe brand-relevant",
                len(batch),
            )
            return {}
        except BudgetError:
            # Hard-cap doldu: saglayiciya GIDILMEDI. Fallback
            # SECIMI degistirir -> hata YUTULMAZ (Codex is sirasi #1).
            raise
        except Exception as exc:
            if len(batch) == 1:
                metrics["failed_keyword_ids"].add(batch[0]["id"])
                logger.warning(
                    "Brand filter: keyword %s çözülemedi, fail-safe brand-relevant: %s",
                    batch[0]["id"], exc,
                )
                return {}
            return self._split_and_retry(batch, profile_data, exclude_themes, metrics)

        # ID sözleşmesi (Codex #6): her ID tam 1 kez gelmeli. Duplicate sessizce
        # "son değer kazanır"a düşerse hangi cevabın uygulandığı belirsizleşir;
        # batch'e ait olmayan (ghost) ID ise modelin uydurmasıdır.
        resolved: dict[int, dict[str, Any]] = {}
        seen_counts: dict[int, int] = {}
        ghosts = 0
        for r in raw_results:
            kid = self._coerce_keyword_id(r.get("keyword_id"))
            if kid is None or kid not in batch_ids:
                ghosts += 1
                continue
            seen_counts[kid] = seen_counts.get(kid, 0) + 1
            resolved[kid] = r
        if ghosts:
            # STRICT sözleşme (Codex #5): fazladan/uydurma ID de yanıt
            # ihlalidir. Gerçek ID'lerin hepsi gelmiş olsa bile yanıt
            # güvenilmez sayılır → tüm batch yeniden sorulur (split retry),
            # tekil kelimede de düzelmezse fail-safe (dışlanmaz).
            metrics["ghost_ids"] += ghosts
            resolved.clear()
        duplicates = {kid for kid, count in seen_counts.items() if count > 1}
        if duplicates:
            metrics["duplicate_ids"] += len(duplicates)
            # Çelişkili cevap güvenilmez: sonuçtan düşür → missing'e girer →
            # split retry, tekil kelimede de çözülemezse fail-safe (dışlanmaz)
            for kid in duplicates:
                resolved.pop(kid, None)
        if via_split:
            metrics["recovered_ids"].update(resolved.keys())

        missing = batch_ids - set(resolved.keys())
        if not missing:
            return resolved
        # Kısmi sonuç: eksik kelimeler için split retry
        if len(batch) == 1:
            metrics["failed_keyword_ids"].update(missing)
            return resolved
        missing_batch = [kw for kw in batch if kw["id"] in missing]
        resolved.update(
            self._split_and_retry(missing_batch, profile_data, exclude_themes, metrics)
        )
        return resolved

    def _split_and_retry(
        self,
        batch: list[dict[str, Any]],
        profile_data: dict[str, Any],
        exclude_themes: list[str],
        metrics: dict[str, Any],
    ) -> dict[int, dict[str, Any]]:
        """Batch'i ikiye böl (5→2→1) ve her yarıyı yeniden değerlendir."""
        metrics["split_retries"] += 1
        mid = len(batch) // 2
        resolved: dict[int, dict[str, Any]] = {}
        for sub in (batch[:mid], batch[mid:]):
            if sub:
                resolved.update(
                    self._evaluate_with_recovery(
                        sub, profile_data, exclude_themes, metrics, via_split=True
                    )
                )
        return resolved

    def _load_intent_passed_candidates(
        self,
        scoring_run_id: int,
        keyword_ids: list[int] | None = None,
        channels: list[str] | None = None,
    ) -> dict[int, dict[str, Any]]:
        query = (
            self.db.query(IntentAnalysis, Keyword)
            .join(Keyword, Keyword.id == IntentAnalysis.keyword_id)
            .filter(IntentAnalysis.scoring_run_id == scoring_run_id)
            .filter(IntentAnalysis.is_passed == True)
        )
        if keyword_ids:
            query = query.filter(IntentAnalysis.keyword_id.in_(keyword_ids))
        if channels:
            query = query.filter(IntentAnalysis.channel.in_(channels))
        rows = query.all()
        candidates: dict[int, dict[str, Any]] = {}
        channels_by_keyword: dict[int, set[str]] = defaultdict(set)
        for intent, keyword in rows:
            candidates.setdefault(keyword.id, {"keyword": keyword.keyword, "channels": []})
            channels_by_keyword[keyword.id].add(intent.channel)
        for keyword_id, channels in channels_by_keyword.items():
            candidates[keyword_id]["channels"] = sorted(channels)
        return candidates

    def _evaluate_batch(
        self,
        batch: list[dict[str, Any]],
        profile_data: dict[str, Any],
        exclude_themes: list[str],
    ) -> list[dict[str, Any]]:
        prompt = self._build_prompt(batch, profile_data, exclude_themes)
        # HARD bütçe (expansion) + gerçek çağrı sayacı
        if self._budget is not None and not self._budget.try_consume():
            raise AiBudgetExhausted("Brand filter: expansion AI bütçesi tükendi")
        self._ai_calls_used += 1
        raw = self.ai_service.complete_json(
            prompt=prompt,
            max_tokens=BRAND_FILTER_MAX_TOKENS,
            temperature=0.2,
            response_schema=self.RESPONSE_SCHEMA,
        )
        # Dayanıklı parser: truncated/kaçışsız-tırnak/fence durumlarında
        # kısmi kurtarma yapar (A1).
        return parse_ai_json_list(raw)

    def _build_prompt(
        self,
        batch: list[dict[str, Any]],
        profile_data: dict[str, Any],
        exclude_themes: list[str],
    ) -> str:
        profile_context = {
            field: profile_data.get(field) or ([] if field != "target_audience" else "")
            for field in self.PROFILE_FIELDS
        }
        profile_context["exclude_themes"] = exclude_themes
        profile_context["protected_themes"] = self._protected_themes
        keywords_json = json.dumps(batch, ensure_ascii=False)
        profile_json = json.dumps(profile_context, ensure_ascii=False)
        return f"""Sen marka uygunluğu filtresisin.

Görev: Her keyword için İKİ SORUYU AYRI AYRI yanıtla:
1) Keyword, protected_themes (korunacak temalar) listesindeki bir temaya giriyor mu?
2) Keyword, exclude_themes (dışlanacak temalar) listesindeki bir temaya giriyor mu?

Önemli:
- Keyword, tema ifadesini birebir içermese bile anlamca o temaya giriyorsa o temaya girmiş say.
- KORUMA ÜSTÜNDÜR: keyword hem korunacak hem dışlanacak temaya yakın görünüyorsa is_brand_relevant=true dön ve matched_protected_theme alanını doldur.
- Yalnız dışlanacak temaya giriyorsa is_brand_relevant=false dön ve matched_exclude_theme alanını doldur.
- Hiçbirine girmiyorsa is_brand_relevant=true dön ve iki tema alanını da "" bırak.
- Sektöre özel hard-code yapma; kararı sadece verilen marka profiline göre ver.
- Tema alanlarına YALNIZ yukarıdaki listelerde geçen tema metnini yaz; listede olmayan tema UYDURMA.

brand_profile={profile_json}
keywords={keywords_json}

SADECE geçerli minified JSON döndür. Markdown veya açıklama yazma.
Schema:
{{"results":[{{"keyword_id":1,"is_brand_relevant":false,"matched_protected_theme":"","matched_exclude_theme":"tekil hisse analizi"}}]}}

ZORUNLU:
- Girdideki her keyword_id çıktıda tam 1 kez olmalı.
- keyword_id integer, is_brand_relevant boolean olmalı.
- matched_protected_theme ve matched_exclude_theme alanlarını HER sonuçta yaz; eşleşme yoksa "" (boş) bırak. Alanı atlama.
- Tema alanları kısa olmalı; tırnak, apostrof veya yeni satır KULLANMA.
- Açıklama/reason yazma; yalnızca şemadaki alanları döndür.
"""

    def _resolve_decision(
        self, result: dict[str, Any], keyword_text: str, audit: dict[str, int]
    ) -> dict[str, Any]:
        """Model cevabını UYGULANACAK karara çevirir (plan Faz C sözleşmesi).

        Kurallar:
          - Dönen tema kanonik listeden gelmelidir; uydurma tema sessizce
            kabul edilmez (ihlal sayılır, karar dayanağı olamaz).
          - Geçerli korunan tema eşleşmesi dışlamaya ÜSTÜNDÜR; model
            `false` dönse bile kod `is_brand_relevant=true` uygular.
          - DETERMİNİSTİK KORUMA TABANI (Codex #1): koruma garantisi modelin
            alanı doldurmasına bağlı BIRAKILAMAZ. Keyword METNİ açıkça bir
            korunan temaya oturuyorsa (theme_matcher, anchor tarafıyla AYNI
            kural), model o alanı boş bıraksa/atlasa bile koruma uygulanır.
            21 keyword tam da bu boşluktan kaybedilmişti.
          - Korunan-tema sorusu YANITLANMAMIŞSA (alan hiç yok) cevap eksiktir
            ve TERMİNAL ELEME ÜRETEMEZ.
          - Kanonik bir dışlama temasına dayanmayan `false` de terminal eleme
            üretemez (mevcut fail-safe yönüyle aynı: dışlanmaz).
        """
        from app.core.site_analyzer.theme_matcher import (
            matched_theme,
            resolve_declared_theme,
        )

        model_relevant = self._is_brand_relevant(result)
        answered_protected = "matched_protected_theme" in result
        protected_raw = str(result.get("matched_protected_theme") or "").strip()
        exclude_raw = str(result.get("matched_exclude_theme") or "").strip()

        protected = resolve_declared_theme(protected_raw, self._protected_themes)
        exclude = resolve_declared_theme(exclude_raw, self._exclude_themes)

        if protected_raw and protected is None:
            audit["theme_contract_violations"] += 1
        if exclude_raw and exclude is None:
            audit["theme_contract_violations"] += 1

        deterministic = (
            matched_theme(keyword_text, self._protected_themes)
            if protected is None else None
        )

        applied_relevant = model_relevant
        winner = "none"
        if protected or deterministic:
            audit["protected_matches"] += 1
            if deterministic and not protected:
                audit["protected_deterministic"] += 1
            if not model_relevant:
                audit["protected_overrides"] += 1
            applied_relevant = True
            winner = "protected" if protected else "protected_deterministic"
        elif not model_relevant:
            if not answered_protected:
                audit["theme_contract_violations"] += 1
                audit["unbacked_exclusions"] += 1
                applied_relevant = True
                winner = "incomplete_answer_kept"
            elif exclude is None:
                audit["unbacked_exclusions"] += 1
                applied_relevant = True
                winner = "unbacked_exclusion_kept"
            else:
                winner = "exclude"

        return {
            "model_relevant": model_relevant,
            "applied_relevant": applied_relevant,
            "winner": winner,
            "matched_protected_theme": protected or deterministic,
            "matched_exclude_theme": exclude,
        }

    def _upsert_brand_exclusion(
        self,
        *,
        scoring_run_id: int,
        keyword_id: int,
        channel: str,
        reason: str,
        decision: dict[str, Any],
    ) -> None:
        existing = (
            self.db.query(PreFilterResult)
            .filter(
                PreFilterResult.scoring_run_id == scoring_run_id,
                PreFilterResult.keyword_id == keyword_id,
                PreFilterResult.channel == channel,
            )
            .first()
        )
        payload = {
            "reason_code": BRAND_EXCLUDED_REASON,
            "matched_exclude_theme": decision.get("matched_exclude_theme"),
            # Faz C audit: model kararı / uygulanan karar / kazanan tema türü
            "matched_protected_theme": decision.get("matched_protected_theme"),
            "model_decision": decision.get("model_relevant"),
            "applied_decision": decision.get("applied_relevant"),
            "theme_winner": decision.get("winner"),
        }
        if existing:
            existing.is_kept = False
            existing.label = None
            existing.ai_reasoning = reason
            existing.extra_data = payload
            existing.transfer_channel = None
            existing.is_fallback = False
            return
        self.db.add(
            PreFilterResult(
                scoring_run_id=scoring_run_id,
                keyword_id=keyword_id,
                channel=channel,
                is_kept=False,
                label=None,
                ai_reasoning=reason,
                extra_data=payload,
                transfer_channel=None,
                is_fallback=False,
            )
        )

    def _normalize_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        return []

    def _is_brand_relevant(self, result: dict[str, Any]) -> bool:
        value = result.get("is_brand_relevant")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "evet"}
        return True
