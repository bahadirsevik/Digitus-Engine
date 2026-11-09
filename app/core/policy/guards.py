"""İki yönlü kilit protokolünün mutasyon-tarafı yardımcıları (plan v13 invariant #4).

Politika/anchor mutasyonu yapan HER endpoint, workspace row lock aldıktan sonra
bu kontrolü çağırır: pending/running kanal-atama + ADS/SEO/SOCIAL generation +
grup regenerate + devam eden export varken mutasyon 409 ile reddedilir
(çalışan iş eski politikayla 'fresh' içerik yazamasın).

Karşı yön (producer dispatch'lerin aynı workspace kilidini alıp
channel_pool_stale kontrolü yapması) ilgili dispatch noktalarında uygulanır.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import ExportJob, ScoringRun, TaskResult

ACTIVE_TASK_STATUSES = ("pending", "running")
# channel_assignment + tüm üretim türleri; policy_preview BİLEREK dışarıda
# (preview salt-okur, politika mutasyonunu engellemesi gerekmez).
GUARDED_TASK_TYPES = (
    "channel_assignment",
    "competitor_discovery",
    "seo_content",
    "ads",
    "social",
    "social_content",
    "export",
)
ACTIVE_EXPORT_STATUSES = ("pending", "processing")

# Kullanıcıya gösterilen iş adları (mesajlarda ham task_type/ID gösterilmez).
TASK_TYPE_LABELS = {
    "channel_assignment": "kanal ataması",
    "seo_content": "SEO içerik üretimi",
    "ads": "reklam üretimi",
    "social": "sosyal medya içerik üretimi",
    "social_content": "sosyal medya içerik üretimi",
    "corpus_screening": "ön tarama",
    "competitor_discovery": "rakip keşfi",
    "policy_preview": "rakip önizlemesi",
    "export": "dışa aktarım",
}


def task_type_label(task_type: Optional[str]) -> str:
    return TASK_TYPE_LABELS.get(task_type or "", "bir arka plan işlemi")


def find_active_workspace_work(db: Session, workspace_id: int) -> Optional[str]:
    """Workspace'te süren atama/üretim/export işi varsa kısa açıklama döndürür.

    None → mutasyon güvenle ilerleyebilir.
    """
    from app.tasks.task_status import fail_stale_tasks

    fail_stale_tasks(brand_profile_id=workspace_id)
    run_ids = [
        r.id
        for r in db.query(ScoringRun.id).filter(
            ScoringRun.brand_profile_id == workspace_id
        ).all()
    ]

    task_query = db.query(TaskResult).filter(
        TaskResult.status.in_(ACTIVE_TASK_STATUSES),
        TaskResult.task_type.in_(GUARDED_TASK_TYPES),
    )
    if run_ids:
        task = task_query.filter(
            (TaskResult.scoring_run_id.in_(run_ids))
            | (TaskResult.brand_profile_id == workspace_id)
        ).first()
    else:
        task = task_query.filter(
            TaskResult.brand_profile_id == workspace_id
        ).first()
    if task:
        return f"{task_type_label(task.task_type)} sürüyor"

    export = (
        db.query(ExportJob)
        .filter(
            ExportJob.brand_profile_id == workspace_id,
            ExportJob.status.in_(ACTIVE_EXPORT_STATUSES),
        )
        .first()
    )
    if export:
        return "dışa aktarım sürüyor"
    return None
