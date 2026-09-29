"""
Sosyal medya export yardımcıları (plan_social_brief_akisi.md §8).

`format_payload` (video segment / carousel slide / thread post) ve süre
alanlarını okunabilir metne çeviren TEK ortak kaynak — 4 exporter (csv,
docx, excel, pdf) burayı kullanır; render mantığı 4 yerde ayrı yazılıp
zamanla sapmasın.
"""
from typing import Any, Dict, List, Optional


def format_payload_lines(payload: Optional[Dict[str, Any]]) -> List[str]:
    """format_payload'i okunabilir satır listesine çevirir.

    video -> segments[] (zaman aralığı + sahne + ekran metni + seslendirme)
    carousel -> slides[]
    thread -> posts[]
    post/story/caption -> boş liste (caption alanı zaten ayrı gösteriliyor)
    """
    if not isinstance(payload, dict):
        return []
    kind = payload.get("kind")

    if kind == "video":
        lines = []
        for i, seg in enumerate(payload.get("segments") or [], 1):
            if not isinstance(seg, dict):
                continue
            start = seg.get("start_sec")
            end = seg.get("end_sec")
            time_range = f"{start}-{end}sn" if start is not None and end is not None else ""
            header = f"Sahne {i}" + (f" ({time_range})" if time_range else "")
            detail_parts = []
            if seg.get("scene"):
                detail_parts.append(str(seg["scene"]))
            if seg.get("on_screen_text"):
                detail_parts.append(f"Ekran: {seg['on_screen_text']}")
            if seg.get("voiceover"):
                detail_parts.append(f"Seslendirme: {seg['voiceover']}")
            detail = " | ".join(detail_parts)
            lines.append(f"{header}: {detail}" if detail else header)
        return lines

    if kind == "carousel":
        lines = []
        for i, slide in enumerate(payload.get("slides") or [], 1):
            if isinstance(slide, dict):
                text = slide.get("text") or slide.get("caption") or slide.get("title") or ""
            else:
                text = str(slide)
            lines.append(f"Slayt {i}: {text}")
        return lines

    if kind == "thread":
        lines = []
        for i, post in enumerate(payload.get("posts") or [], 1):
            text = post.get("text") if isinstance(post, dict) else str(post)
            lines.append(f"Gönderi {i}: {text or ''}")
        return lines

    return []


def format_payload_text(payload: Optional[Dict[str, Any]]) -> str:
    """`format_payload_lines` çıktısını tek metne (satır sonu ile) çevirir."""
    return "\n".join(format_payload_lines(payload))


def duration_requested_text(min_sec: Optional[int], max_sec: Optional[int]) -> str:
    """Brief hedefinde istenen süre aralığını okunabilir metne çevirir."""
    if min_sec is None and max_sec is None:
        return "-"
    if min_sec is not None and max_sec is not None:
        return f"{min_sec}-{max_sec} sn"
    return f"{min_sec if min_sec is not None else max_sec} sn"


_DURATION_STATUS_LABELS = {
    "valid": "Uygun",
    "mismatch": "Tutmadı",
    "unparseable": "Ayrıştırılamadı",
    "not_applicable": "Uygulanamaz",
}


def duration_status_text(status: Optional[str]) -> str:
    """`SocialContent.duration_status` değerini Türkçe etikete çevirir."""
    if not status:
        return "-"
    return _DURATION_STATUS_LABELS.get(status, status)
