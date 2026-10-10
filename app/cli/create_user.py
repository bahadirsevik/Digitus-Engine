# -*- coding: utf-8 -*-
"""Kullanici yonetimi betigi — acma, parola sifirlama, pasife alma.

Acik kayit (self-service signup) ucu BILEREK yok. Kullanicilar yalniz bu
betikle acilir.

NEDEN app/ ICINDE: repo kokundeki `scripts/` dizini `.dockerignore`'da
("dagitim disi gelistirme icerigi") ve imaja girmez. Bu betik bir URETIM
aracidir, bu yuzden app paketi altinda durur.

Kullanim (sunucuda):

    # tek kullanici, parola sorulur
    docker exec -it digitus_app_prod python -m app.cli.create_user \\
        --email ad@ornek.com --name "Ad Soyad"

    # birden fazla kullanici, gecici parola URET ve ekrana yaz
    docker exec digitus_app_prod python -m app.cli.create_user \\
        --email a@ornek.com --email b@ornek.com --generate-password

    # parola sifirlama
    docker exec -it digitus_app_prod python -m app.cli.create_user \\
        --email ad@ornek.com --reset-password

    # pasife alma (acik oturumlari da duser)
    docker exec digitus_app_prod python -m app.cli.create_user \\
        --email ad@ornek.com --deactivate

--generate-password ile uretilen parolalar STDOUT'a yazilir. Ciktiyi yalniz
root'un okuyabilecegi bir dosyaya yonlendirin; kabuk gecmisine parola
dusmemesi icin parolayi --password ile ELLE vermekten kacinin.
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from datetime import datetime, timezone

from app.core.passwords import hash_password, validate_password_strength
from app.database.connection import SessionLocal
from app.database.models import User

# Gecici parola alfabesi: karistirilabilen karakterler (O/0, l/1/I) BILEREK
# yok — parola telefonla/WhatsApp ile iletilecek ve elle yazilacak.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
_GENERATED_LENGTH = 16


def generate_temp_password() -> str:
    """Kriptografik olarak guvenli gecici parola."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(_GENERATED_LENGTH))


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


def _revoke(user_id: int) -> None:
    """Oturumlari kapatmayi dener; Redis yoksa betigi basarisiz saymaz."""
    try:
        from app.core.sessions import revoke_all_sessions

        revoke_all_sessions(user_id)
    except Exception as exc:  # noqa: BLE001 - betik, en iyi caba
        print(f"  (uyari: oturumlar kapatilamadi: {exc})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Digitus Engine kullanicilarini yonetir.",
    )
    parser.add_argument(
        "--email",
        action="append",
        required=True,
        metavar="ADRES",
        help="Giris e-postasi. Birden fazla kullanici icin tekrar edin.",
    )
    parser.add_argument("--name", default=None, help="Ad soyad (yalniz tek kullanicida)")
    parser.add_argument(
        "--password",
        default=None,
        help="ONERILMEZ (kabuk gecmisine duser) — verilmezse sorulur",
    )
    parser.add_argument(
        "--generate-password",
        action="store_true",
        help="Gecici parola uret ve ekrana yaz",
    )
    parser.add_argument(
        "--must-change",
        dest="must_change",
        action="store_true",
        default=None,
        help="Ilk giriste parola degistirmeyi ZORUNLU kil "
        "(--generate-password ile varsayilan)",
    )
    parser.add_argument(
        "--no-must-change",
        dest="must_change",
        action="store_false",
        help="Parola degistirme zorunlulugu KOYMA",
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

    emails: list[str] = []
    for raw in args.email:
        email = raw.strip().lower()
        if "@" not in email:
            print(f"Gecerli bir e-posta adresi degil: {raw}")
            return 1
        if email not in emails:
            emails.append(email)

    if len(emails) > 1 and args.name:
        print("--name yalniz tek kullanici acarken kullanilabilir.")
        return 1
    if len(emails) > 1 and args.password:
        print("--password yalniz tek kullanici acarken kullanilabilir.")
        return 1
    if len(emails) > 1 and not (args.generate_password or args.deactivate):
        print(
            "Birden fazla kullanici icin --generate-password (veya --deactivate) gerekir;\n"
            "aksi halde her biri icin parola tek tek sorulmasi gerekirdi."
        )
        return 1

    # Gecici parola uretildiginde degistirme zorunlulugu VARSAYILAN olarak
    # acilir: gecici parolanin amaci zaten tek kullanimlik olmasidir.
    must_change = args.must_change
    if must_change is None:
        must_change = bool(args.generate_password)

    failures = 0
    db = SessionLocal()
    try:
        for email in emails:
            existing = db.query(User).filter(User.email == email).first()

            # ── pasife alma ──
            if args.deactivate:
                if existing is None:
                    print(f"[atlandi] bulunamadi: {email}")
                    failures += 1
                    continue
                existing.is_active = False
                # Surum artar: sonradan yeniden etkinlestirilse bile pasifken
                # acik kalmis oturumlar geri donmez (Redis silinemese de).
                existing.session_version = User.session_version + 1
                db.commit()
                _revoke(existing.id)
                print(f"[pasif]   {email} (acik oturumlari kapatildi)")
                continue

            # ── parola belirleme ──
            if args.generate_password:
                password = generate_temp_password()
            elif args.password:
                password = args.password
            else:
                print(f"--- {email} ---")
                password = _prompt_password()

            problem = validate_password_strength(password)
            if problem:
                print(f"[hata]    {email}: {problem}")
                failures += 1
                continue

            # ── parola sifirlama ──
            if existing is not None:
                if not args.reset_password:
                    print(
                        f"[atlandi] zaten kayitli: {email} "
                        "(parolasini degistirmek icin --reset-password ekleyin)"
                    )
                    failures += 1
                    continue
                existing.password_hash = hash_password(password)
                existing.is_active = True
                existing.deleted_at = None
                existing.must_change_password = must_change
                # Parola ile AYNI commit'te: eski oturumlar Redis temizligi
                # basarisiz olsa bile kapida reddedilir.
                existing.session_version = User.session_version + 1
                db.commit()
                _revoke(existing.id)
                suffix = "  PAROLA DEGISTIRME ZORUNLU" if must_change else ""
                if args.generate_password:
                    print(f"[sifirlandi] {email}  parola: {password}{suffix}")
                else:
                    print(f"[sifirlandi] {email}{suffix}")
                continue

            # ── yeni kullanici ──
            user = User(
                email=email,
                password_hash=hash_password(password),
                full_name=args.name,
                is_active=True,
                must_change_password=must_change,
                created_at=datetime.now(timezone.utc),
            )
            db.add(user)
            db.commit()
            suffix = "  PAROLA DEGISTIRME ZORUNLU" if must_change else ""
            if args.generate_password:
                print(f"[acildi]  {email}  parola: {password}{suffix}")
            else:
                print(f"[acildi]  {email} (id={user.id}){suffix}")
    finally:
        db.close()

    if failures:
        print(f"\n{failures} islem basarisiz/atlandi.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
