"""Etiket tek-kaynak sözleşmesi (Codex v15-1).

Akış: annotator Markdown tablosunu doldurur → `import_annotations.py`
bu modülle DOĞRULAYIP JSON'a aktarır → harness JSON'u okur ama önce
`verify_consistency` ile Markdown-JSON uyumunu doğrular; uyuşmazlıkta
ÇALIŞMAYI REDDEDER. JSON elle düzenlenmez.

Doğrulama kuralları:
- Markdown'daki her satırın kimliği JSON'da olmalı; kelime METNİ birebir
  eşleşmeli (yeniden sıralama/elle bozulma yakalanır).
- Etiket değerleri şemaya uymalı; 'skip' (yalnız bozuk metin) ve '?'
  (çözümleme turuna kalan) geçerli ara değerlerdir.
- FREEZE (dondurma) için: hiç 'FILL' / '?' kalmamalı; çift-etiket
  maddelerinde ikinci sayfa da dolu olmalı ve uyuşmazlıklar alan-bazlı
  adjudicated_labels + adjudication_notes ile çözülmüş olmalı.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# annotation_sheet.json'daki label_schema ile aynı sözleşme; import
# sırasında JSON'daki şema esas alınır (buradaki kopya yalnız yardımcı).
LABEL_FIELDS = [
    "intent_type", "gt", "ga", "ads_class",
    "opinion_discussion", "curiosity_comparison", "agenda_theme",
    "relevance_band",
]
PENDING_VALUES = {"FILL", "?"}
SKIP = "skip"

# v16-4: skip -> evaluation_mask eşlemesi. Bir gruptaki HERHANGİ bir alan
# 'skip' ise o görev bu madde için değerlendirilemez (kısmi skip'li grup
# değerlendirmesi sağlıksız) -> mask false. Yalnız importer yazar.
MASK_GROUPS = {
    "intent": ["intent_type"],
    "gt_ga": ["gt", "ga"],
    "ads_class": ["ads_class"],
    "social_dims": ["opinion_discussion", "curiosity_comparison", "agenda_theme"],
    "relevance": ["relevance_band"],
}


def _expected_mask(labels: Dict[str, object]) -> Dict[str, bool]:
    return {
        group: not any(labels.get(field) == SKIP for field in fields)
        for group, fields in MASK_GROUPS.items()
    }


def _write_json_atomic(path: Path, doc: dict) -> None:
    """Write a complete JSON document without exposing a partial file."""
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(doc, handle, ensure_ascii=False, indent=1)
            temp_name = handle.name
        Path(temp_name).replace(path)
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)

# Markdown sütun sırası (birincil: Çift sütunu VAR; ikinci: YOK)
_PRIMARY_COLS = ["item", "cift", "keyword"] + LABEL_FIELDS
_SECOND_COLS = ["item", "keyword"] + LABEL_FIELDS


class AnnotationError(ValueError):
    """Doğrulama hatası — satır/alan bağlamıyla."""


def _parse_md_rows(md_text: str, expected_cols: int) -> List[List[str]]:
    rows = []
    for line in md_text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|--") or line.startswith("|-"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or not re.fullmatch(r"K\d{3}", cells[0]):
            continue  # başlık/ayraç satırları
        if len(cells) != expected_cols:
            raise AnnotationError(
                f"{cells[0]}: {expected_cols} sütun bekleniyordu, "
                f"{len(cells)} bulundu (tabloda '|' kaçağı olabilir)"
            )
        rows.append(cells)
    return rows


def _coerce(field: str, raw: str, schema: Dict[str, list]) -> object:
    value = (raw or "").strip()
    if value == "":
        return "FILL"
    if value in PENDING_VALUES:
        return "?" if value == "?" else "FILL"
    if value.lower() == SKIP:
        return SKIP
    allowed = schema[field]
    if all(isinstance(a, int) for a in allowed):
        try:
            iv = int(value)
        except ValueError:
            raise AnnotationError(
                f"{field}: {value!r} sayı değil (izinli: {allowed} / skip / ?)"
            )
        if iv not in allowed:
            raise AnnotationError(f"{field}: {iv} izinli değil ({allowed})")
        return iv
    lowered = value.lower()
    if lowered not in [str(a).lower() for a in allowed]:
        raise AnnotationError(
            f"{field}: {value!r} izinli değil ({allowed} / skip / ?)"
        )
    return lowered


def parse_primary_md(md_text: str, schema: Dict[str, list]) -> Dict[str, dict]:
    out = {}
    for cells in _parse_md_rows(md_text, len(_PRIMARY_COLS)):
        item_id, _cift, keyword = cells[0], cells[1], cells[2]
        if item_id in out:
            raise AnnotationError(f"{item_id}: markdown'da yinelenen kimlik")
        labels = {}
        for field, raw in zip(LABEL_FIELDS, cells[3:]):
            try:
                labels[field] = _coerce(field, raw, schema)
            except AnnotationError as e:
                raise AnnotationError(f"{item_id}: {e}") from e
        out[item_id] = {"keyword": keyword, "labels": labels}
    return out


def parse_second_md(md_text: str, schema: Dict[str, list]) -> Dict[str, dict]:
    out = {}
    for cells in _parse_md_rows(md_text, len(_SECOND_COLS)):
        item_id, keyword = cells[0], cells[1]
        if item_id in out:
            raise AnnotationError(f"{item_id}: markdown'da yinelenen kimlik")
        labels = {}
        for field, raw in zip(LABEL_FIELDS, cells[2:]):
            try:
                labels[field] = _coerce(field, raw, schema)
            except AnnotationError as e:
                raise AnnotationError(f"{item_id}(2.): {e}") from e
        out[item_id] = {"keyword": keyword, "labels": labels}
    return out


def _match_entries(parsed: Dict[str, dict], entries: List[dict],
                   sheet_name: str) -> None:
    by_id = {e["item_id"]: e for e in entries}
    if len(by_id) != len(entries):
        raise AnnotationError(f"{sheet_name}: JSON'da yinelenen kimlik var")
    missing = set(by_id) - set(parsed)
    extra = set(parsed) - set(by_id)
    if missing:
        raise AnnotationError(
            f"{sheet_name}: markdown'da eksik kimlikler: {sorted(missing)[:5]}..."
        )
    if extra:
        raise AnnotationError(
            f"{sheet_name}: JSON'da olmayan kimlikler: {sorted(extra)[:5]}"
        )
    for item_id, row in parsed.items():
        expected = by_id[item_id]["keyword"]
        if row["keyword"] != expected:
            raise AnnotationError(
                f"{sheet_name}/{item_id}: kelime uyuşmuyor "
                f"(md={row['keyword']!r}, json={expected!r}) — satırlar "
                f"kaymış/düzenlenmiş olabilir"
            )


def apply_md_to_json(md_path: Path, json_path: Path, *, second: bool = False,
                     force: bool = False) -> Dict[str, int]:
    """Markdown etiketlerini doğrulayıp JSON'a yazar (tek yazıcı).

    JSON'da FILL-dışı mevcut bir etiket md'dekiyle çelişiyorsa force
    olmadan reddedilir (sessiz üzerine-yazma yok).
    """
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    schema = doc["label_schema"]
    md_text = md_path.read_text(encoding="utf-8")
    parsed = (parse_second_md if second else parse_primary_md)(md_text, schema)
    _match_entries(parsed, doc["entries"], md_path.name)

    stats = {"written": 0, "pending_fill": 0, "pending_question": 0,
             "conflicts": 0}
    conflicts = []
    for entry in doc["entries"]:
        row = parsed[entry["item_id"]]
        for field in LABEL_FIELDS:
            new = row["labels"][field]
            old = entry["labels"].get(field, "FILL")
            if new == "FILL":
                stats["pending_fill"] += 1
                continue
            if new == "?":
                stats["pending_question"] += 1
            if old not in ("FILL", new):
                conflicts.append(f"{entry['item_id']}.{field}: json={old!r} md={new!r}")
                if not force:
                    continue
            entry["labels"][field] = new
            stats["written"] += 1
    if conflicts and not force:
        stats["conflicts"] = len(conflicts)
        raise AnnotationError(
            "JSON'da farklı değerli mevcut etiketler var (force'suz "
            "üzerine yazılmaz):\n  " + "\n  ".join(conflicts[:10])
        )

    # v16-4: evaluation_mask etiketlerden TAM deterministik türetilir
    # (skip -> false; skip sonradan gerçek etikete dönerse mask geri açılır).
    # Yalnız birincil sayfada mask vardır; tek yazıcı importer'dır.
    if not second:
        for entry in doc["entries"]:
            entry["evaluation_mask"] = _expected_mask(entry["labels"])

    _write_json_atomic(json_path, doc)
    return stats


def verify_consistency(md_path: Path, json_path: Path, *,
                       second: bool = False) -> List[str]:
    """Harness ön-koşulu: md ile json AYNI etiketleri taşıyor mu?

    Dönüş: uyumsuzluk listesi (boş = tutarlı). Markdown'da dolu olup
    JSON'da FILL kalan değer de uyumsuzluktur (aktarım unutulmuş).
    """
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    schema = doc["label_schema"]
    md_text = md_path.read_text(encoding="utf-8")
    parsed = (parse_second_md if second else parse_primary_md)(md_text, schema)
    _match_entries(parsed, doc["entries"], md_path.name)

    diffs = []
    for entry in doc["entries"]:
        row = parsed[entry["item_id"]]
        for field in LABEL_FIELDS:
            md_val = row["labels"][field]
            json_val = entry["labels"].get(field, "FILL")
            if md_val != json_val:
                diffs.append(
                    f"{entry['item_id']}.{field}: md={md_val!r} json={json_val!r}"
                )
    return diffs


def _is_real_label(field: str, value, schema: Dict[str, list]) -> bool:
    """Şemaya uygun GERÇEK etiket mi? (FILL/?/skip değil)."""
    if value in PENDING_VALUES or value == SKIP or value is None:
        return False
    return value in schema.get(field, [])


def freeze_report(json_path: Path,
                  second_json_path: Optional[Path] = None) -> Tuple[bool, List[str]]:
    """Dondurma kontrolü (v16-1 sertleştirmesi). Şartlar:

    - Hiç FILL/? kalmamış olmalı (birincil + ikinci).
    - skip'li grup için evaluation_mask false olmalı (v16-4 tutarlılık).
    - Çift maddelerde uyuşmazlık YALNIZ geçerli adjudication ile çözülür:
      adjudicated değer şemaya uygun GERÇEK etiket (FILL/?/skip DEĞİL) ve
      adjudication_note dolu olmalı.
    - Uyuşmazlık olmayan alanda adjudication OLMAMALI (adjudication bir
      etiket-düzenleme arka kapısı değildir).
    """
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    schema = doc.get("label_schema") or {}
    problems = []
    second_by_id = {}
    if second_json_path is not None and second_json_path.exists():
        sdoc = json.loads(second_json_path.read_text(encoding="utf-8"))
        second_by_id = {e["item_id"]: e for e in sdoc["entries"]}

    for entry in doc["entries"]:
        item_id = entry["item_id"]
        for field in LABEL_FIELDS:
            v = entry["labels"].get(field, "FILL")
            if v in PENDING_VALUES:
                problems.append(f"{item_id}.{field}: {v}")

        # Mask is entirely derived from labels. Check both directions so a
        # stale false value cannot silently remove a valid label from metrics.
        mask = entry.get("evaluation_mask") or {}
        expected_mask = _expected_mask(entry["labels"])
        if mask != expected_mask:
            problems.append(
                f"{item_id}: evaluation_mask etiketlerle uyuşmuyor "
                f"(json={mask!r}, beklenen={expected_mask!r})"
            )

        adjudicated_map = entry.get("adjudicated_labels") or {}
        notes_map = entry.get("adjudication_notes") or {}
        note_summary = (entry.get("adjudication_note") or "").strip()

        if not entry.get("double_label_required"):
            if adjudicated_map or notes_map or note_summary:
                problems.append(
                    f"{item_id}: çift-etiket maddesi değil ama "
                    f"adjudication verisi dolu (arka kapı düzenleme?)"
                )
            continue

        s = second_by_id.get(item_id)
        if s is None:
            problems.append(f"{item_id}: ikinci etiket dosyada yok")
            continue
        for field in LABEL_FIELDS:
            sv = s["labels"].get(field, "FILL")
            if sv in PENDING_VALUES:
                problems.append(f"{item_id}.{field}(2.): {sv}")
                continue
            pv = entry["labels"].get(field, "FILL")
            adjudicated = adjudicated_map.get(field)
            field_note = str(notes_map.get(field) or "").strip()
            if pv != sv:
                # v16-1: adjudication GEÇERLİ olmalı — varlık yetmez
                if adjudicated is None:
                    problems.append(
                        f"{item_id}.{field}: uyuşmazlık "
                        f"(1.={pv!r}, 2.={sv!r}) — adjudicated_labels gerekli"
                    )
                elif not _is_real_label(field, adjudicated, schema):
                    problems.append(
                        f"{item_id}.{field}: adjudicated değer geçersiz "
                        f"({adjudicated!r} — şemaya uygun gerçek etiket değil)"
                    )
                elif not field_note:
                    problems.append(
                        f"{item_id}.{field}: adjudication gerekçesi boş — "
                        f"gerekçe zorunlu"
                    )
            elif adjudicated is not None or field_note:
                problems.append(
                    f"{item_id}.{field}: uyuşmazlık yok ama adjudicated "
                    f"değer/gerekçe var (arka kapı düzenleme?)"
                )

        expected_summary = _adjudication_summary(notes_map)
        if note_summary != expected_summary:
            problems.append(
                f"{item_id}: adjudication_note alan-bazlı gerekçelerle "
                f"uyuşmuyor"
            )
    return (len(problems) == 0, problems)


# ── v16-2: adjudication yazma yolu (JSON elle DÜZENLENMEZ sözleşmesi) ──

_ADJ_COLS = 4  # | item | alan | karar | gerekçe |


def parse_adjudication_md(md_text: str) -> List[dict]:
    rows = []
    seen = set()
    for line in md_text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|--") or line.startswith("|-"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or not re.fullmatch(r"K\d{3}", cells[0]):
            continue
        if len(cells) != _ADJ_COLS:
            raise AnnotationError(
                f"{cells[0]}: adjudication satırı {_ADJ_COLS} sütun olmalı, "
                f"{len(cells)} bulundu"
            )
        key = (cells[0], cells[1])
        if key in seen:
            raise AnnotationError(
                f"{cells[0]}.{cells[1]}: yinelenen adjudication satırı"
            )
        seen.add(key)
        rows.append({"item_id": cells[0], "field": cells[1],
                     "value": cells[2], "note": cells[3]})
    return rows


def _adjudication_summary(notes: Dict[str, str]) -> str:
    return "; ".join(
        f"{field}: {notes[field]}" for field in LABEL_FIELDS if notes.get(field)
    )


def _build_adjudication_state(md_path: Path, json_path: Path,
                              second_json_path: Path):
    """Validate the whole adjudication sheet and derive its complete state."""
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    sdoc = json.loads(second_json_path.read_text(encoding="utf-8"))
    schema = doc["label_schema"]
    by_id = {e["item_id"]: e for e in doc["entries"]}
    second_by_id = {e["item_id"]: e for e in sdoc["entries"]}
    if len(by_id) != len(doc["entries"]):
        raise AnnotationError("birincil JSON'da yinelenen kimlik var")
    if len(second_by_id) != len(sdoc["entries"]):
        raise AnnotationError("ikinci JSON'da yinelenen kimlik var")

    rows = parse_adjudication_md(md_path.read_text(encoding="utf-8"))
    decisions_by_item: Dict[str, Dict[str, object]] = {}
    notes_by_item: Dict[str, Dict[str, str]] = {}
    for row in rows:
        item_id, field = row["item_id"], row["field"]
        entry = by_id.get(item_id)
        if entry is None:
            raise AnnotationError(f"{item_id}: birincil sayfada yok")
        if not entry.get("double_label_required"):
            raise AnnotationError(f"{item_id}: çift-etiket maddesi değil")
        if field not in LABEL_FIELDS:
            raise AnnotationError(f"{item_id}: bilinmeyen alan {field!r}")
        s = second_by_id.get(item_id)
        if s is None:
            raise AnnotationError(f"{item_id}: ikinci etiket dosyada yok")
        pv = entry["labels"].get(field, "FILL")
        sv = s["labels"].get(field, "FILL")
        if pv in PENDING_VALUES or sv in PENDING_VALUES:
            raise AnnotationError(
                f"{item_id}.{field}: iki etiket de dolu olmadan "
                f"adjudication yapılamaz (1.={pv!r}, 2.={sv!r})"
            )
        if pv == sv:
            raise AnnotationError(
                f"{item_id}.{field}: uyuşmazlık yok (ikisi de {pv!r}) — "
                f"adjudication etiket-düzenleme arka kapısı değildir"
            )
        value = _coerce(field, row["value"], schema)
        if not _is_real_label(field, value, schema):
            raise AnnotationError(
                f"{item_id}.{field}: karar {row['value']!r} geçerli bir "
                f"etiket değil (FILL/?/skip kabul edilmez)"
            )
        note = row["note"].strip()
        if not note:
            raise AnnotationError(f"{item_id}.{field}: gerekçe boş olamaz")

        decisions_by_item.setdefault(item_id, {})[field] = value
        notes_by_item.setdefault(item_id, {})[field] = note

    return doc, decisions_by_item, notes_by_item


def verify_adjudication_consistency(md_path: Path, json_path: Path,
                                    second_json_path: Path) -> List[str]:
    """Return differences between canonical adjudication Markdown and JSON."""
    doc, decisions_by_item, notes_by_item = _build_adjudication_state(
        md_path, json_path, second_json_path
    )
    diffs = []
    for entry in doc["entries"]:
        item_id = entry["item_id"]
        expected_labels = decisions_by_item.get(item_id, {})
        expected_notes = notes_by_item.get(item_id, {})
        actual_labels = entry.get("adjudicated_labels") or {}
        actual_notes = entry.get("adjudication_notes") or {}
        expected_summary = _adjudication_summary(expected_notes)
        actual_summary = (entry.get("adjudication_note") or "").strip()
        if actual_labels != expected_labels:
            diffs.append(
                f"{item_id}.adjudicated_labels: json={actual_labels!r} "
                f"md={expected_labels!r}"
            )
        if actual_notes != expected_notes:
            diffs.append(
                f"{item_id}.adjudication_notes: json={actual_notes!r} "
                f"md={expected_notes!r}"
            )
        if actual_summary != expected_summary:
            diffs.append(
                f"{item_id}.adjudication_note: json={actual_summary!r} "
                f"md={expected_summary!r}"
            )
    return diffs


def apply_adjudications(md_path: Path, json_path: Path,
                        second_json_path: Path) -> Dict[str, int]:
    """Replace JSON adjudication state with the validated Markdown state."""
    doc, decisions_by_item, notes_by_item = _build_adjudication_state(
        md_path, json_path, second_json_path
    )

    for entry in doc["entries"]:
        item_id = entry["item_id"]
        decisions = decisions_by_item.get(item_id, {})
        notes = notes_by_item.get(item_id, {})
        entry["adjudicated_labels"] = decisions or None
        entry["adjudication_notes"] = notes
        entry["adjudication_note"] = _adjudication_summary(notes)

    _write_json_atomic(json_path, doc)
    return {
        "applied": sum(len(values) for values in decisions_by_item.values()),
        "items": len(decisions_by_item),
    }
