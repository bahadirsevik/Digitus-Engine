"""Workspace policy servisleri (plan_kalite_maliyet.md A+B + plan v13).

- competitor_policy: rakip terim provenance modeli + kanal politikası +
  kanonik URL anahtarı (registrable domain)
- topic_policy: dışlanan konu terim/alias eşleştirme + excluded_info
  tek-kaynak senkronu
- review: paylaşılan rakip/konu inceleme çekirdeği + profil yazma kapısı
- freshness: ChannelPool bayatlık hesabı (policy + relevance eksenleri)

Ortak ilke: filtreye YALNIZCA kullanıcı onaylı kayıtlar girer. Eşleşme
substring değil, Türkçe-normalize edilmiş TOKEN sınırlarıyla yapılır.
"""
from app.core.policy.competitor_policy import (  # noqa: F401
    add_source_url,
    approved_competitor_terms,
    backfill_legacy_source_urls,
    competitor_policy_for,
    is_legacy_term,
    match_term,
    normalize_competitor_url,
    reconcile_removed_urls,
    reconcile_url_decision,
    set_manual_approval,
    suggest_competitor_terms,
)
from app.core.policy.freshness import (  # noqa: F401
    PoolFreshness,
    compute_pool_freshness,
)
from app.core.policy.review import (  # noqa: F401
    PolicyValidationError,
    apply_competitor_review,
    apply_profile_data_update,
    canonical_anchor_fingerprint,
)
from app.core.policy.topic_policy import (  # noqa: F401
    approved_topic_terms,
    match_topic_term,
    parse_excluded_info,
    sync_excluded_terms,
)
