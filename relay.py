"""
Colab -> Launcher tunel linki rolesi (ntfy.sh, hesapsiz ve ucretsiz).

Colab, Cloudflare tunel linkini oturum kodundan turetilen gizli bir kanala yazar;
launcher / agent ayni koddan kanali hesaplayip son linki okur.
Kanal adi kodun SHA-256'sindan turetilir, yani role kodun kendisini (= sunucu sifresini) gormez.
Sadece standart kutuphane kullanir (Colab ve exe icin ek bagimlilik yok).
"""
import hashlib
import json
import re
import urllib.request

RELAY_BASE = "https://ntfy.sh"
UA = "Mozilla/5.0 JudyNeuralLink/1.0"
URL_RE = re.compile(r"^https://[a-z0-9-]+\.trycloudflare\.com$")
URL_FIND_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def topic_for(code: str) -> str:
    return "judy-" + hashlib.sha256(("judy-topic:" + code).encode("utf-8")).hexdigest()[:32]


def publish(code: str, url: str, timeout: float = 15.0) -> None:
    req = urllib.request.Request(
        f"{RELAY_BASE}/{topic_for(code)}",
        data=url.encode("utf-8"),
        headers={"User-Agent": UA, "Title": "judy-tunnel"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=timeout).read()


def fetch_latest(code: str, since: str = "12h", timeout: float = 10.0) -> str | None:
    """Kanaldaki en son gecerli trycloudflare linkini dondurur (yoksa None)."""
    req = urllib.request.Request(
        f"{RELAY_BASE}/{topic_for(code)}/json?poll=1&since={since}",
        headers={"User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "ignore")
    last = None
    for raw in body.splitlines():
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if ev.get("event") == "message":
            msg = (ev.get("message") or "").strip()
            if URL_RE.match(msg):
                last = msg
    return last
