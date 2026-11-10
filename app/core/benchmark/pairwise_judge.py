"""DeepSeek V4 Pro ikili (pairwise) hakem — plan K4 (3), §7.3.

Çift başına İKİ çağrı (pozisyon takası): (A,B) ve (B,A). S(A,B)=1 (A tercih) / 0 (B) / 0,5 (berabere);
skor S = (S(A,B) + 1 − S(B,A)) / 2. Aynı POZİSYONU iki sırada da seçen hakem pozisyon yanlılığı
gösterir -> S=0,5 + `position_bias=True`. Çağrısı/ayrıştırması başarısız yön -> çift "yetersiz" olarak
DIŞLANIR (sebep kodlu), asla sessizce 0,5 sayılmaz. Hakeme yalnız normalize anonim A/B içerik gider.

Çağrılar `ai.for_stage(stage, provider="deepseek", model=model).complete_json(...)` üzerinden gider
(BudgetGuardedAI tavanı geçerli). NOT: DeepSeekService `response_schema`'yı sağlayıcıya GÖNDERMEZ
(JSON mode); şema burada yerel doğrulama + prompt'taki biçim tarifi içindir.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.core.benchmark.claude_gates import cohen_kappa
from app.core.benchmark.content_pairs import rubric_dims
from app.core.benchmark.reference_characterization import CharacterizationError
from app.core.channel.ai_json import AIJsonParseError, parse_ai_json_object

RUBRIC_V1 = (
    "Kriterler (1–5; yüksek = iyi). Genel tercihi bu boyutların ağırlıklı toplamına göre ver:\n"
    "- Marka/ürün sadakati (ağırlık 3): marka ve ürün gerçeklerine uygunluk, uydurma iddia yok.\n"
    "- Türkçe doğallık (2): akıcı, doğal, çeviri kokmayan Türkçe.\n"
    "- Anahtar kelime/CTA hedefine uyum (2): hedef kelime/niyetle ve çağrı ile örtüşme.\n"
    "- Özgünlük (1): kalıp dışı, tekrar etmeyen anlatım.\n"
    "Format ekleri (ağırlık 2): blog = bilgi yeterliliği, yapı, arama niyeti; "
    "RSA = başlık çeşitliliği, mesaj kapsamı; sosyal = hook, uygulanabilirlik, format uygunluğu.\n"
    "Uzunluğa puan verme. Hangi sistemin yazdığını tahmin etmeye çalışma."
)
RUBRIC_SHA = hashlib.sha256(RUBRIC_V1.encode("utf-8")).hexdigest()
PROMPT_VERSION = "pairwise_v1"
MAX_CONSECUTIVE_FAILURES = 5


class JudgeAborted(CharacterizationError):
    """Art arda başarısız çağrı (rota/anahtar/şema hatası belirtisi) — para yakmadan dur."""


def build_response_schema(fmt: str) -> Dict[str, Any]:
    dims = {d: {"type": "object", "properties": {"A": {"type": "integer", "minimum": 1, "maximum": 5},
                                                 "B": {"type": "integer", "minimum": 1, "maximum": 5}},
                "required": ["A", "B"], "additionalProperties": False}
            for d, _, _ in rubric_dims(fmt)}
    return {"type": "object", "additionalProperties": False,
            "properties": {"preference": {"type": "string", "enum": ["A", "B", "tie"]},
                           "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                           "dimension_scores": {"type": "object", "properties": dims,
                                                "required": list(dims), "additionalProperties": False}},
            "required": ["preference", "confidence", "dimension_scores"]}


def _render(blocks: Sequence[Sequence[str]]) -> str:
    return "\n".join(f"[{lab}]\n{txt}" for lab, txt in blocks)


def build_prompt(fmt: str, key_label: str, shown_a: Sequence, shown_b: Sequence,
                 rubric: str = RUBRIC_V1) -> str:
    dims = ", ".join(d for d, _, _ in rubric_dims(fmt))
    return (
        "Sen tarafsız bir Türkçe dijital pazarlama içerik hakemisin. Aynı konu için yazılmış iki "
        f"içeriği ({fmt}) karşılaştır ve hangisinin daha iyi olduğuna karar ver.\n\n"
        f"Konu / anahtar kelime: {key_label}\n\n{rubric}\n\n"
        f"=== İÇERİK A ===\n{_render(shown_a)}\n\n=== İÇERİK B ===\n{_render(shown_b)}\n\n"
        "Yalnızca şu biçimde KATI JSON döndür, başka metin yazma:\n"
        '{"preference": "A" | "B" | "tie", "confidence": 0..1, '
        '"dimension_scores": {"<boyut>": {"A": 1-5, "B": 1-5}}}\n'
        f"Boyutlar: {dims}.")


def _s_value(pref: str) -> float:
    return {"A": 1.0, "B": 0.0, "tie": 0.5}[pref]


def _call_one(ai: Any, prompt: str, fmt: str, *, stage: str, model: str, max_tokens: int) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"preference_shown": None, "confidence": None, "dimension_scores": None,
                           "error": None, "raw_text": None}
    try:
        raw = ai.for_stage(stage, provider="deepseek", model=model).complete_json(
            prompt, max_tokens=max_tokens, response_schema=build_response_schema(fmt))
    except CharacterizationError:
        raise                       # bütçe/tavan/rezervasyon: durdur, "başarısız çağrı" sayılmaz
    except Exception as exc:  # noqa: BLE001 — sağlayıcı/rota hatası: yön kaydedilir, çift dışlanır
        rec["error"] = f"call_failed:{type(exc).__name__}"
        return rec
    rec["raw_text"] = (raw if isinstance(raw, str) else str(raw))[:2000]
    try:
        obj = parse_ai_json_object(raw, required_fields=("preference",))
    except (AIJsonParseError, TypeError):
        rec["error"] = "unparseable"
        return rec
    if obj.get("preference") not in ("A", "B", "tie"):
        rec["error"] = "invalid_preference"
        return rec
    rec["preference_shown"] = obj["preference"]
    conf = obj.get("confidence")
    rec["confidence"] = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) \
        and 0 <= conf <= 1 else None
    ds = obj.get("dimension_scores")
    rec["dimension_scores"] = ds if isinstance(ds, dict) else None
    return rec


def judge_pairs(pairs_public: Sequence[Mapping[str, Any]], *, ai: Any, stage: str = "pairwise_judge",
                model: str = "deepseek-v4-pro", rubric: str = RUBRIC_V1, max_tokens: int = 600,
                max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES) -> Dict[str, Dict[str, Any]]:
    """-> {pair_id: {S_AB, S_BA, S, position_bias, excluded, reason, raw:[ab, ba]}}.
    S_AB: orijinal A'nın (A,B) sırasındaki tercih skoru; S_BA: orijinal B'nin (B,A) sırasında
    (gösterilen 'A' = orijinal B) tercih skoru; S = (S_AB + 1 − S_BA)/2. Dışlanan çiftte S=None."""
    out: Dict[str, Dict[str, Any]] = {}
    consecutive = 0
    for p in pairs_public:
        fmt, A, B = p["format"], p["A"], p["B"]
        ab = _call_one(ai, build_prompt(fmt, p["key_label"], A, B, rubric), fmt,
                       stage=stage, model=model, max_tokens=max_tokens)
        ba = _call_one(ai, build_prompt(fmt, p["key_label"], B, A, rubric), fmt,
                       stage=stage, model=model, max_tokens=max_tokens)
        for r in (ab, ba):
            consecutive = consecutive + 1 if r["error"] else 0
            if consecutive >= max_consecutive_failures:
                raise JudgeAborted(f"{consecutive} art arda başarısız hakem çağrısı "
                                   f"(son hata: {r['error']}) — rota/anahtar/bütçe kontrol edin")
        ab["order"], ba["order"] = "AB", "BA"
        s_ab = None if ab["error"] else _s_value(ab["preference_shown"])
        s_ba = None if ba["error"] else _s_value(ba["preference_shown"])
        res: Dict[str, Any] = {"S_AB": s_ab, "S_BA": s_ba, "S": None, "position_bias": False,
                               "excluded": False, "reason": None, "raw": [ab, ba]}
        if s_ab is None or s_ba is None:
            res["excluded"] = True
            res["reason"] = "insufficient:" + ",".join(
                n for n, v in (("AB", s_ab), ("BA", s_ba)) if v is None) + "_failed"
        else:
            res["S"] = (s_ab + 1.0 - s_ba) / 2.0
            # aynı POZİSYON iki sırada da kazandı (A,A ya da B,B) -> S=0,5 ama bayrak
            res["position_bias"] = (s_ab == s_ba) and s_ab in (0.0, 1.0)
        out[p["pair_id"]] = res
    return out


def judge_pref_from_S(S: Optional[float], tie_band: float = 0.05) -> Optional[str]:   # plan K4: |S-0,5| < 0,05
    """'A' (S >= 0,5+band) | 'B' (S <= 0,5−band) | 'tie' ; S None -> None. Plan K4 bandı
    |S−0,5| < 0,05 (kapılar eşiği mühürlü plandan okur); görev varsayılanı 0,1 çağıranca verilir."""
    if S is None:
        return None
    if S >= 0.5 + tie_band - 1e-12:
        return "A"
    if S <= 0.5 - tie_band + 1e-12:
        return "B"
    return "tie"


def _to_cand_ref(pref: Optional[str], side_map: Mapping[str, str]) -> Optional[str]:
    return None if pref is None else "tie" if pref == "tie" else side_map[pref]


def hard_rules_from_secret(mapping_secret: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, bool]]:
    return {pid: {"ref_ok": bool(m["hard_rule_ref_ok"]), "cand_ok": bool(m["hard_rule_cand_ok"])}
            for pid, m in mapping_secret.items()}


def judge_pairs_to_gate_input(pairs_public: Sequence[Mapping[str, Any]],
                              judge_results: Mapping[str, Mapping[str, Any]],
                              patron_unblinded: Mapping[str, str],
                              mapping_secret: Mapping[str, Mapping[str, Any]],
                              hard_rules: Mapping[str, Mapping[str, bool]],
                              tie_band: float = 0.05) -> List[Dict[str, Any]]:
    """`claude_gates.evaluate_content` girdisi. patron_unblinded: unblind() çıktısı (yalnız patron
    çiftleri); judge_pref hakem sonucundan açılır (dışlanan/bakılmayan = None). hard_rules'ta
    olmayan pair_id için ValueError (sert kural paydasından düşme yok)."""
    rows = []
    for p in pairs_public:
        pid = p["pair_id"]
        if pid not in hard_rules:
            raise ValueError(f"{pid}: hard_rules eksik")
        jr = judge_results.get(pid)
        jp = judge_pref_from_S(jr["S"], tie_band) if jr else None
        rows.append({"format": p["format"], "ws": p["ws"], "patron_pref": patron_unblinded.get(pid),
                     "judge_pref": _to_cand_ref(jp, mapping_secret[pid]),
                     "hard_rule_cand_ok": bool(hard_rules[pid]["cand_ok"]),
                     "hard_rule_ref_ok": bool(hard_rules[pid]["ref_ok"])})
    return rows


def kappa_patron_vs_judge(gate_rows: Sequence[Mapping[str, Any]],
                          fmt: Optional[str] = None) -> Dict[str, Any]:
    """Patron ile hakemin ORTAK puanladığı çiftlerde Cohen κ (cand/ref/tie kategorileri)."""
    both = [(r["patron_pref"], r["judge_pref"]) for r in gate_rows
            if (fmt is None or r["format"] == fmt)
            and r.get("patron_pref") is not None and r.get("judge_pref") is not None]
    return {"kappa": cohen_kappa([a for a, _ in both], [b for _, b in both]) if both else None,
            "n": len(both)}


def judge_provenance(model: str = "deepseek-v4-pro", stage: str = "pairwise_judge") -> Dict[str, Any]:
    return {"prompt_version": PROMPT_VERSION, "rubric_sha256": RUBRIC_SHA, "model": model,
            "stage": stage, "schema_sha256": hashlib.sha256(json.dumps(
                {f: build_response_schema(f) for f in ("blog", "rsa", "social")},
                sort_keys=True).encode("utf-8")).hexdigest()}
