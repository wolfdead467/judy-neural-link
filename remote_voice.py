"""
Yerel PC tarafi: Colab'daki XTTSv2 + RVC sunucusuna Cloudflare tuneli uzerinden baglanir.
JudyVoiceEngine ile ayni arayuzu (synthesize / mount_vram / unmount_vram) sunar.
Yerelde GPU/VRAM kullanilmaz. Tunel linki oturum kodu ile role uzerinden otomatik bulunur.
"""
import json
import os
import time
import urllib.error
import urllib.request

import judy_config as jc
import relay

OUT_WAV = os.path.join(jc.data_dir(), "judy_remote_cikti.wav")
LEGACY_URL_FILE = os.path.join(jc.data_dir(), "colab_url.txt")


def resolve_url() -> str:
    """Oncelik: JUDY_COLAB_URL env -> role (oturum kodu) -> colab_url.txt -> config.json."""
    url = os.getenv("JUDY_COLAB_URL", "").strip()
    if url:
        return url.rstrip("/")

    code = jc.get_session_code(create=False)
    if code:
        try:
            found = relay.fetch_latest(code)
        except Exception:
            found = None
        if found:
            return found.rstrip("/")

    if os.path.exists(LEGACY_URL_FILE):
        try:
            with open(LEGACY_URL_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("http"):
                        return line.rstrip("/")
        except OSError:
            pass

    cfg = jc.load_config()
    return (cfg.get("colab_url") or "").strip().rstrip("/")


load_colab_url = resolve_url  # eski ad


class RemoteVoiceEngine:
    def __init__(self, base_url: str, token: str = "", timeout: float = 90.0):
        self.base_url = base_url.rstrip("/")
        self.token = token or os.getenv("JUDY_TOKEN") or jc.get_session_code(create=False)
        self.timeout = timeout
        self.device = "remote"
        self._check_health()

    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "User-Agent": relay.UA,
            "ngrok-skip-browser-warning": "1",
        }
        if self.token:
            h["X-Judy-Token"] = self.token
        return h

    def _check_health(self):
        req = urllib.request.Request(f"{self.base_url}/health", headers=self._headers())
        with urllib.request.urlopen(req, timeout=20) as r:
            info = json.loads(r.read().decode("utf-8"))
        print(f"[+] Connected to the Colab server: {self.base_url} ({info})")

    def _refresh_url(self) -> bool:
        new = resolve_url()
        if new and new != self.base_url:
            print(f"[judy-tts] Yeni tunel linki bulundu: {new}")
            self.base_url = new
            return True
        return False

    def mount_vram(self):  # Colab GPU'su kalici yuklu; yerelde yapilacak is yok
        pass

    def unmount_vram(self):
        pass

    def _post_tts(self, payload: bytes) -> bytes:
        req = urllib.request.Request(f"{self.base_url}/tts", data=payload, headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read()

    def synthesize(self, text, language="en", emotion="calm", out_path: str = OUT_WAV):
        t0 = time.time()
        payload = json.dumps({"text": text, "language": language, "emotion": emotion}).encode("utf-8")
        audio = None
        last_err = None
        for attempt in range(2):
            try:
                audio = self._post_tts(payload)
                break
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise RuntimeError("The session code does not match the one in Colab (401).")
                last_err = f"HTTP {e.code}"
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                last_err = str(e)
            if attempt == 0:  # tunel yenilenmis olabilir: linki role'den tazele ve bir kez daha dene
                time.sleep(2)
                self._refresh_url()
        if audio is None:
            raise RuntimeError(f"Colab TTS unreachable: {last_err}")

        tmp = out_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(audio)
        os.replace(tmp, out_path)
        return out_path, time.time() - t0
