"""
Brand Defaults Resolver.
Resolves brand_name, brand_usp, brand_context from confirmed BrandProfile.
Used by generation endpoints to auto-fill brand info.
"""
import logging
from typing import Optional, List

from sqlalchemy.orm import Session

from app.database.models import (
    BrandProfile, AdGroup,
    SocialCategory, SocialIdea, SocialContent,
)

logger = logging.getLogger(__name__)


class BrandResolveError(Exception):
    """Typed exception for brand resolution failures.

    Codes:
        "not_found"  -> 404: ID(ler) DB'de bulunamadi
        "mixed_run"  -> 400: ID'ler farkli scoring run'lara ait
        "empty_list" -> 400: Bos liste gonderildi
    """

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class BrandDefaultsResolver:
    """Resolves brand defaults from confirmed BrandProfile for a scoring run."""

    def __init__(self, db: Session):
        self.db = db

    # --- Ana resolver ---

    def resolve(self, scoring_run_id: Optional[int]) -> dict:
        """
        Onayli profilden brand defaults doner.
        SADECE status == "confirmed" bakilir. draft/pending ASLA.
        Profil yoksa veya confirmed degilse -> bos dict (fail-open).

        Returns: {
            "brand_name": "Vepa Firca",
            "brand_usp": "Dogal sac fircasi, dis fircasi -- kisisel bakim urunleri",
            "brand_context": "Marka: Vepa Firca\nUrunler: ...\nKullanim: ...",
        }
        """
        if not scoring_run_id:
            return {}

        profile = self.db.query(BrandProfile).filter(
            BrandProfile.scoring_run_id == scoring_run_id,
            BrandProfile.status == "confirmed",
        ).first()

        if not profile or not profile.profile_data:
            return {}

        data = profile.profile_data
        brand_name = self.safe_str(data.get("company_name"))
        brand_usp = self._build_usp(data)
        brand_context = self._build_context(data)

        result = {}
        if brand_name:
            result["brand_name"] = brand_name
        if brand_usp:
            result["brand_usp"] = brand_usp
        if brand_context:
            result["brand_context"] = brand_context

        return result

    # --- ID Turetme (DB traverse) ---

    def run_id_from_category(self, category_ids: List[int]) -> int:
        """category_ids -> SocialCategory.scoring_run_id"""
        if not category_ids:
            raise BrandResolveError("Kategori ID listesi bos", code="empty_list")

        categories = self.db.query(SocialCategory).filter(
            SocialCategory.id.in_(category_ids)
        ).all()

        found_ids = {c.id for c in categories}
        missing = set(category_ids) - found_ids
        if missing:
            raise BrandResolveError(
                f"Kategori bulunamadi: {sorted(missing)}", code="not_found"
            )

        run_ids = {c.scoring_run_id for c in categories}
        if len(run_ids) > 1:
            raise BrandResolveError(
                f"Kategoriler farkli scoring run'lara ait: {sorted(run_ids)}",
                code="mixed_run",
            )

        return run_ids.pop()

    def run_id_from_idea(self, idea_ids: List[int]) -> int:
        """idea_ids -> SocialIdea.category_id -> SocialCategory.scoring_run_id"""
        if not idea_ids:
            raise BrandResolveError("Fikir ID listesi bos", code="empty_list")

        ideas = (
            self.db.query(SocialIdea, SocialCategory.scoring_run_id)
            .join(SocialCategory, SocialIdea.category_id == SocialCategory.id)
            .filter(SocialIdea.id.in_(idea_ids))
            .all()
        )

        found_ids = {idea.id for idea, _ in ideas}
        missing = set(idea_ids) - found_ids
        if missing:
            raise BrandResolveError(
                f"Fikir bulunamadi: {sorted(missing)}", code="not_found"
            )

        run_ids = {run_id for _, run_id in ideas}
        if len(run_ids) > 1:
            raise BrandResolveError(
                f"Fikirler farkli scoring run'lara ait: {sorted(run_ids)}",
                code="mixed_run",
            )

        return run_ids.pop()

    def run_id_from_content(self, content_id: int) -> int:
        """content_id -> SocialContent.idea_id -> chain -> scoring_run_id"""
        row = (
            self.db.query(SocialCategory.scoring_run_id)
            .join(SocialIdea, SocialIdea.category_id == SocialCategory.id)
            .join(SocialContent, SocialContent.idea_id == SocialIdea.id)
            .filter(SocialContent.id == content_id)
            .first()
        )

        if not row:
            raise BrandResolveError(
                f"Content bulunamadi: {content_id}", code="not_found"
            )

        return row[0]

    def run_id_from_ad_group(self, group_id: int) -> int:
        """group_id -> AdGroup.scoring_run_id"""
        group = self.db.query(AdGroup).filter(AdGroup.id == group_id).first()
        if not group:
            raise BrandResolveError(
                f"Ad group bulunamadi: {group_id}", code="not_found"
            )
        return group.scoring_run_id

    # --- Yardimcilar ---

    @staticmethod
    def safe_str(val) -> str:
        """None-safe: (val or "").strip()
        None, "", " " -> "" doner.
        """
        return (val or "").strip()

    def _build_usp(self, profile_data: dict) -> str:
        """products[:3] + sector'dan USP olustur. Max 150 karakter."""
        products = profile_data.get("products", [])
        sector = self.safe_str(profile_data.get("sector"))

        if not products and not sector:
            return ""

        product_part = ", ".join(products[:3]) if products else ""
        if product_part and sector:
            usp = f"{product_part} -- {sector}"
        elif product_part:
            usp = product_part
        else:
            usp = sector

        # Cap at 150 chars
        if len(usp) > 150:
            usp = usp[:147] + "..."

        return usp

    def _build_context(self, profile_data: dict) -> str:
        """company_name + products + use_cases + problems_solved -> duz metin.
        Markdown baslik KULLANMA.
        """
        lines = []

        company = self.safe_str(profile_data.get("company_name"))
        if company:
            lines.append(f"Marka: {company}")

        products = profile_data.get("products", [])
        if products:
            lines.append(f"Urunler: {', '.join(products)}")

        use_cases = profile_data.get("use_cases", [])
        if use_cases:
            lines.append(f"Kullanim Alanlari: {', '.join(use_cases)}")

        problems = profile_data.get("problems_solved", [])
        if problems:
            lines.append(f"Cozdukleri Sorunlar: {', '.join(problems)}")

        return "\n".join(lines)
