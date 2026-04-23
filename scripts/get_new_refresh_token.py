"""
Yeni Google Ads refresh token alir.
Kullanim: python scripts/get_new_refresh_token.py

Adimlar:
  1. Script browser acar
  2. Google hesabinla giris yap ve izin ver
  3. Yonlendirilen URL'yi buraya yapistir
  4. Script yeni GOOGLE_ADS_REFRESH_TOKEN degerini yazdirir
"""
import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_env():
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


load_env()

CLIENT_ID     = os.environ.get("GOOGLE_ADS_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "")

if not CLIENT_ID or not CLIENT_SECRET:
    print("[HATA] .env dosyasinda GOOGLE_ADS_CLIENT_ID veya GOOGLE_ADS_CLIENT_SECRET eksik.")
    sys.exit(1)

SCOPE = "https://www.googleapis.com/auth/adwords"
REDIRECT_URI = "http://localhost:8085"
PORT = 8085

auth_url = (
    "https://accounts.google.com/o/oauth2/auth"
    f"?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}"
    f"&scope={SCOPE}"
    "&access_type=offline"
    "&response_type=code"
    "&prompt=consent"
)

print("\n=== ADIM 1: Browser'da izin ver ===")
print(f"Asagidaki URL aciliyor:\n{auth_url}\n")
webbrowser.open(auth_url)

# Localhost'ta kodu otomatik yakala
import http.server
import urllib.parse as _up
import threading

code = None

class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global code
        params = dict(_up.parse_qsl(_up.urlsplit(self.path).query))
        if "code" in params:
            code = params["code"]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Tamam! Bu sekmeyi kapabilirsiniz.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Kod bulunamadi.")
    def log_message(self, *args):
        pass

server = http.server.HTTPServer(("localhost", PORT), _Handler)
server.timeout = 120  # 2 dakika bekle
print(f"Localhost:{PORT} bekleniyor... (2 dakika zaman asiminiz var)\n")
try:
    server.handle_request()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()

if not code:
    print("\nOtomatik alinamazsa:")
    print("1) Browser'da izin verdikten sonra yonlendirilen URL'yi kopyala")
    print("2) URL'deki 'code=...' parametresini asagiya yapistir\n")
    code = input("Kod: ").strip()
    # URL yapistirildiysa code= parametresini cikart
    if "code=" in code:
        code = dict(_up.parse_qsl(_up.urlsplit(code).query)).get("code", code)

print("\n=== ADIM 3: Token aliniyor... ===")
try:
    import urllib.request
    import urllib.parse
    import json

    data = urllib.parse.urlencode({
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    refresh_token = result.get("refresh_token", "")
    if not refresh_token:
        print("[HATA] refresh_token gelmedi. Yanit:", result)
        sys.exit(1)

    print("\n=== BASARILI ===")
    print(f"\nYeni GOOGLE_ADS_REFRESH_TOKEN ({len(refresh_token)} karakter):\n  {refresh_token}")

    # .env dosyasini otomatik guncelle
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            content = f.read()
        import re as _re
        new_content = _re.sub(
            r"GOOGLE_ADS_REFRESH_TOKEN=.*",
            f"GOOGLE_ADS_REFRESH_TOKEN={refresh_token}",
            content,
        )
        with open(env_path, "w") as f:
            f.write(new_content)
        print(f"\n[OK] .env dosyasi otomatik guncellendi: {env_path}")
        print("Simdi: docker-compose up -d app")
    else:
        print(f"\n.env bulunamadi. Manuel olarak ekle:\n  GOOGLE_ADS_REFRESH_TOKEN={refresh_token}")

except Exception as e:
    print(f"[HATA] {e}")
    sys.exit(1)
