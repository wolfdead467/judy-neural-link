"""
Judy Colab TTS Sunucusu (XTTSv2 + RVC v2) - Google Colab (T4 16GB) uzerinde calisir.
Yerel PC -> Cloudflare tuneli -> POST /tts {"text": "..."} -> WAV (40kHz) geri doner.
Modeller Colab GPU'sunda kalici yuklu tutulur; yerel PC'de 0 MB VRAM kullanilir.
"""
import hmac
import os
import sys
import tempfile
import threading
import time

os.environ.setdefault("COQUI_TOS_AGREED", "1")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from flask import Flask, jsonify, request, send_file  # noqa: E402

from test_voice import JudyVoiceEngine  # noqa: E402

AUTH_TOKEN = os.getenv("JUDY_TOKEN", "")
if not AUTH_TOKEN:
    # Without a token anyone who finds the tunnel URL could use the GPU. Refuse to start instead of running open.
    sys.exit("[!] JUDY_TOKEN is not set. Set it (the notebook uses the session code) and start again. Refusing to run without authentication.")

app = Flask(__name__)
_lock = threading.Lock()

print("[*] Colab ses motoru yukleniyor (XTTSv2 + RVC v2, CUDA)...")
engine = JudyVoiceEngine(start_on_gpu=True, enable_rvc=True)
try:
    engine.synthesize("Warming up.", language="en", out_path=os.path.join(tempfile.gettempdir(), "judy_warm.wav"))
except Exception as e:  # warm-up hatasi sunucuyu durdurmasin
    print(f"[!] Warm-up hatasi: {e}")
print("[+] Colab ses motoru hazir!")


def _authorized() -> bool:
    return bool(AUTH_TOKEN) and hmac.compare_digest(request.headers.get("X-Judy-Token", "").encode("utf-8"), AUTH_TOKEN.encode("utf-8"))


@app.route("/health")
def health():
    return jsonify(status="ok", device=engine.device)


@app.route("/tts", methods=["POST"])
def tts():
    if not _authorized():
        return jsonify(error="unauthorized"), 401
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify(error="empty text"), 400
    language = body.get("language", "en")
    emotion = body.get("emotion", "calm")

    out_path = os.path.join(tempfile.gettempdir(), f"judy_{time.time_ns()}.wav")
    with _lock:
        t0 = time.time()
        out_file, _ = engine.synthesize(text, language=language, emotion=emotion, out_path=out_path)
        dt = time.time() - t0
    resp = send_file(out_file, mimetype="audio/wav")
    resp.headers["X-Synth-Time"] = f"{dt:.2f}"
    return resp
