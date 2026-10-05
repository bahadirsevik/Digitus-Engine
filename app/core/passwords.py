# -*- coding: utf-8 -*-
"""Parola hash'leme — argon2id.

Neden argon2id: bcrypt'in 72 bayt sessiz kirpma davranisi ve daha zayif
bellek-sertligi yok. Varsayilan parametreler argon2-cffi'nin onerdikleridir;
dusurulmemelidir.

`needs_rehash` ile parametre yukseltmesi destekli: ileride maliyet artirilirsa
kullanicilar bir sonraki girislerinde sessizce yeni hash'e tasinir.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# Parola uzunluk sinirlari. Ust sinir DoS korumasi: argon2 girdiyi hash'ledigi
# icin cok uzun parola CPU yakar.
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024


def hash_password(password: str) -> str:
    """Duz parolayi argon2id hash'ine cevirir (salt hash icinde gomulu)."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """
    Parolayi dogrular. Hic exception sizdirmaz — yalniz True/False.

    Bozuk/bos hash de False doner; boylece veritabaninda beklenmeyen bir
    deger varsa giris basarisiz olur (fail-closed), 500 donmez.
    """
    if not password or not password_hash:
        return False
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Hash mevcut parametrelerin altinda mi uretilmis? (yukseltme icin)"""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, TypeError, ValueError):
        return False


def validate_password_strength(password: str) -> str | None:
    """
    Kabul edilebilir parola mi? Sorun varsa Turkce hata metni, yoksa None.

    Karmasiklik kurali (buyuk/kucuk/rakam zorunlulugu) BILEREK yok: uzunluk
    tek basina daha iyi bir olcut ve kullaniciyi 'Parola1!' gibi tahmin
    edilebilir kaliplara itmez.
    """
    if password is None or not password.strip():
        return "Parola bos olamaz."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Parola en az {MIN_PASSWORD_LENGTH} karakter olmalidir."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Parola en fazla {MAX_PASSWORD_LENGTH} karakter olabilir."
    return None
