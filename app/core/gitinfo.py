"""Runtime git SHA çözümü — git binary'si GEREKMEZ (.git dosya okuması).

Codex v8-5: dev compose kod dizinini bind-mount ettiği için build-time
APP_GIT_SHA her commit'te bayatlıyordu (manifest kodu iki commit geriden
gösterdi). Çözüm sırası:
  1. .git/HEAD dosya okuması (bind-mount'lu dev'de her zaman güncel)
  2. APP_GIT_SHA env / Docker build arg (prod sözleşmesi — image'da .git yok)
  3. 'dev' (config default'u)

Fail-open: herhangi bir okuma hatasında None döner, çağıran env değerine düşer.
"""
from pathlib import Path
from typing import Optional


def resolve_git_sha(repo_root: Optional[Path] = None) -> Optional[str]:
    """`.git/HEAD` üzerinden kısa SHA (12 hane) döndürür; bulunamazsa None."""
    try:
        root = repo_root or Path(__file__).resolve().parents[2]
        head = root / ".git" / "HEAD"
        if not head.is_file():
            return None
        content = head.read_text(encoding="utf-8").strip()
        if not content:
            return None
        if not content.startswith("ref:"):
            # Detached HEAD: doğrudan SHA
            return content[:12]
        ref = content.split(" ", 1)[1].strip()
        ref_file = root / ".git" / ref
        if ref_file.is_file():
            sha = ref_file.read_text(encoding="utf-8").strip()
            return sha[:12] if sha else None
        packed = root / ".git" / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split(" ", 1)
                if len(parts) == 2 and parts[1].strip() == ref:
                    return parts[0][:12]
        return None
    except Exception:
        return None
