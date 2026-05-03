"""
Google Ads token diagnostik scripti.
Mevcut credentials'in hangi adimda fail ettigini gosterir.

Kullanim:
    python scripts/test_google_ads_token.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_env():
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_file):
        print(f"[WARN] .env bulunamadi: {env_file}")
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


load_env()

DEVELOPER_TOKEN  = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
CLIENT_ID        = os.environ.get("GOOGLE_ADS_CLIENT_ID", "")
CLIENT_SECRET    = os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "")
REFRESH_TOKEN    = os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "")
LOGIN_CUSTOMER   = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")
CUSTOMER_ID      = os.environ.get("GOOGLE_ADS_CUSTOMER_ID", "")


def check(label, value, secret=False):
    display = (value[:6] + "..." + value[-4:]) if secret and len(value) > 12 else value
    status = "OK" if value else "EKSIK"
    print(f"  [{status}] {label}: {display if value else '(bos)'}")
    return bool(value)


print("\n=== 1. CREDENTIALS KONTROLU ===")
all_ok = all([
    check("DEVELOPER_TOKEN", DEVELOPER_TOKEN, secret=True),
    check("CLIENT_ID", CLIENT_ID),
    check("CLIENT_SECRET", CLIENT_SECRET, secret=True),
    check("REFRESH_TOKEN", REFRESH_TOKEN, secret=True),
    check("LOGIN_CUSTOMER_ID", LOGIN_CUSTOMER),
])

if not all_ok:
    print("\n[FAIL] Eksik credential var, devam edilemiyor.")
    sys.exit(1)


print("\n=== 2. OAUTH TOKEN YENILEME TESTI ===")
try:
    import google.oauth2.credentials
    import google.auth.transport.requests
    import requests as _requests

    creds = google.oauth2.credentials.Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
    )
    request = google.auth.transport.requests.Request(session=_requests.Session())
    creds.refresh(request)
    print(f"  [OK] Token yenilendi. Gecerlilik: {creds.expiry}")
except Exception as e:
    print(f"  [FAIL] Token yenilenemedi: {e}")
    print("\n  Olasi nedenler:")
    err = str(e).lower()
    if "invalid_grant" in err:
        print("  - Refresh token iptal edilmis veya suresi dolmus")
        print("  - Google hesabinda 'Uygulama erisimini' kontrol et:")
        print("    https://myaccount.google.com/permissions")
        print("  - OAuth uygulamasi 'Test' modundaysa 7 gunluk token suresi")
        print("    -> Cloud Console > OAuth consent screen > Publishing status")
    elif "invalid_client" in err:
        print("  - CLIENT_ID veya CLIENT_SECRET yanlis")
    elif "unauthorized_client" in err:
        print("  - Bu client_id bu refresh_token'i kullanamaz (farkli app'te uretilmis)")
    sys.exit(1)


print("\n=== 3. GOOGLE ADS CLIENT OLUSTURMA ===")
try:
    from google.ads.googleads.client import GoogleAdsClient
    client = GoogleAdsClient.load_from_dict({
        "developer_token": DEVELOPER_TOKEN,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "login_customer_id": LOGIN_CUSTOMER,
        "use_proto_plus": True,
    })
    print("  [OK] GoogleAdsClient olusturuldu")
except Exception as e:
    print(f"  [FAIL] Client olusturulamadi: {e}")
    sys.exit(1)


print("\n=== 4. LIST ACCESSIBLE CUSTOMERS ===")
try:
    svc = client.get_service("CustomerService")
    resp = svc.list_accessible_customers()
    customers = list(resp.resource_names)
    print(f"  [OK] Erisilen hesap sayisi: {len(customers)}")
    for c in customers[:5]:
        print(f"    - {c}")
except Exception as e:
    print(f"  [FAIL] Hesaplara erisilemiyor: {e}")
    sys.exit(1)


print("\n=== 5. CUSTOMER DETAIL (LOGIN_CUSTOMER_ID) ===")
try:
    customer_id_clean = LOGIN_CUSTOMER.replace("-", "")
    ga_svc = client.get_service("GoogleAdsService")
    query = """
        SELECT customer.id, customer.descriptive_name, customer.currency_code
        FROM customer
        LIMIT 1
    """
    response = ga_svc.search(customer_id=customer_id_clean, query=query)
    rows = list(response)
    if rows:
        c = rows[0].customer
        print(f"  [OK] Hesap: {c.descriptive_name} (ID: {c.id}, Para birimi: {c.currency_code})")
    else:
        print("  [WARN] Hesap bulundu ama satirlar bos")
except Exception as e:
    print(f"  [FAIL] Customer detail alinamadi: {e}")


if CUSTOMER_ID:
    print(f"\n=== 6. CUSTOMER_ID ({CUSTOMER_ID}) ERISIM TESTI ===")
    try:
        customer_id_clean = CUSTOMER_ID.replace("-", "")
        ga_svc = client.get_service("GoogleAdsService")
        query = "SELECT campaign.id, campaign.name FROM campaign LIMIT 3"
        response = ga_svc.search(customer_id=customer_id_clean, query=query)
        rows = list(response)
        print(f"  [OK] {len(rows)} kampanya bulundu")
        for r in rows:
            print(f"    - [{r.campaign.id}] {r.campaign.name}")
    except Exception as e:
        print(f"  [FAIL] {e}")

print("\n=== TAMAMLANDI ===\n")
