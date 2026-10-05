# -*- coding: utf-8 -*-
"""Kullanici olusturma / parola sifirlama betigi.

Acik kayit (self-service signup) ucu BILEREK yok. Kullanicilar yalniz bu
betikle acilir.

Kullanim (sunucuda, app konteyneri icinde):

    docker exec -it digitus_app_prod python scripts/create_user.py \
        --email ad@ornek.com --name "Ad Soyad"

Parola sorulur (ekranda gorunmez). Var olan bir e-posta verilirse parolayi
sifirlamak icin --reset-password gerekir; yanlislikla ustune yazma olmaz.

Parolayi komut satirinda vermek (--password) MUMKUN ama ONERILMEZ: kabuk
gecmisine ve surec listesine dusebilir.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timezone

# Betik repo kokunden calistirilir; app paketi import edilebilir olmali.
sys.path.insert(0, "/app")

from app.core.passwords import hash_password, validate_password_strength  # noqa: E402
from app.database.connection import SessionLocal  # noqa: E402
from app.database.models import User  # noqa: E402


def _prompt_password() -> str:
    """Parolayi iki kez sorar ve eslesmesini bekler."""
    for _ in range(3):
        first = getpass.getpass("Parola: ")
        problem = validate_password_strength(first)
        if problem:
            print(f"  ! {problem}")
            continue
        second = getpass.getpass("Parola (tekrar): ")
        if first != second:
            print("  ! Parolalar eslesmiyor.")
            continue
        return first
    print("Parola alinamadi, cikiliyor.")
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Digitus Engine kullanicisi olusturur veya parolasini sifirlar."
    )
    parser.add_argument("--email", required=True, help="Giris e-postasi")
    parser.add_argument("--name", default=None, help="Ad soyad (istege bagli)")
    parser.add_argument(
        "--password",
        default=None,
        help="ONERILMEZ — verilmezse guvenli sekilde sorulur",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="E-posta zaten kayitliysa parolasini degistir",
    )
    parser.add_argument(
        "--deactivate",
        action="store_true",
        help="Kullaniciyi pasife al (giris yapamaz, oturumlari duser)",
    )
    args = parser.parse_args()

    email = args.email.strip().lower()
    if "@" not in email:
        print("Gecerli bir e-posta adresi verin.")
        return 1

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()

        # ── pasife alma ──
        if args.deactivate:
            if existing is None:
                print(f"Kullanici bulunamadi: {email}")
                return 1
            existing.is_active = False
            db.commit()
            _revoke(existing.id)
            print(f"Pasife alindi: {email} (acik oturumlari kapatildi)")
            return 0

        # ── parola sifirlama ──
        if existing is not None:
            if not args.reset_password:
                print(
                    f"Bu e-posta zaten kayitli: {email}\n"
                    "Parolasini degistirmek icin --reset-password ekleyin."
                )
                return 1
            password = args.password or _prompt_password()
            problem = validate_password_strength(password)
            if problem:
                print(f"! {problem}")
                return 1
            existing.password_hash = hash_password(password)
            existing.is_active = True
            existing.deleted_at = None
            db.commit()
            _revoke(existing.id)
            print(
                f"Parola guncellendi: {email}\n"
                "Bu kullanicinin tum acik oturumlari kapatildi."
            )
            return 0

        # ── yeni kullanici ──
        password = args.password or _prompt_password()
        problem = validate_password_strength(password)
        if problem:
            print(f"! {problem}")
            return 1

        user = User(
            email=email,
            password_hash=hash_password(password),
            full_name=args.name,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.commit()
        print(f"Olusturuldu: {email} (id={user.id})")
        return 0
    finally:
        db.close()


def _revoke(user_id: int) -> None:
    """Oturumlari kapatmayi dener; Redis yoksa betigi basarisiz saymaz."""
    try:
        from app.core.sessions import revoke_all_sessions

        revoke_all_sessions(user_id)
    except Exception as exc:  # noqa: BLE001 - betik, en iyi caba
        print(f"  (uyari: oturumlar kapatilamadi: {exc})")


if __name__ == "__main__":
    raise SystemExit(main())
