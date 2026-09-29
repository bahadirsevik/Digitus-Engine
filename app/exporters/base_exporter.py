"""
Base Exporter Abstract Class.

Tüm exporter'lar bu sınıftan türetilir.
"""
from abc import ABC, abstractmethod
from typing import List
from sqlalchemy.orm import Session

from app.schemas.export import FullReport, ExportSectionEnum


class BaseExporter(ABC):
    """
    Abstract base class for all exporters.
    
    Usage:
        exporter = DocxExporter(db)
        filepath = exporter.export(scoring_run_id, sections, filepath)
    """
    
    def __init__(self, db: Session):
        self.db = db
        # Lazy import to avoid circular dependency
        from app.exporters.data_collector import ExportDataCollector
        self.data_collector = ExportDataCollector(db)
    
    @abstractmethod
    def export(
        self,
        scoring_run_id: int,
        sections: List[ExportSectionEnum],
        filepath: str
    ) -> str:
        """
        Export işlemini yapar.
        
        Args:
            scoring_run_id: Scoring run ID
            sections: Dahil edilecek bölümler
            filepath: Çıktı dosya yolu
            
        Returns:
            Oluşturulan dosyanın yolu
        """
        pass
    
    def collect_data(
        self,
        scoring_run_id: int,
        sections: List[ExportSectionEnum]
    ) -> FullReport:
        """
        Veritabanından gerekli verileri toplar.
        
        Args:
            scoring_run_id: Scoring run ID
            sections: Dahil edilecek bölümler
            
        Returns:
            FullReport with collected data
        """
        return self.data_collector.collect(scoring_run_id, sections)
    
    def _should_include(
        self,
        section: ExportSectionEnum,
        requested_sections: List[ExportSectionEnum]
    ) -> bool:
        """Belirtilen bölüm dahil edilmeli mi?"""
        return ExportSectionEnum.ALL in requested_sections or section in requested_sections
