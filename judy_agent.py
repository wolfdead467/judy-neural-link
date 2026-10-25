"""
Judy Neural-Link Agent + Dynamic-VRAM (CUDA <-> CPU) XTTSv2 & RVC v2 (40kHz) Pipeline.

- Odaya girildiginde (LOAD_VRAM): XTTSv2 + RVC v2 RTX 3050 VRAM'ine tasinir (~2-3 sn uretim!).
- Odadan cikildiginda (UNLOAD_VRAM): Modeller Sistem RAM'ine cekilir, VRAM 0 MB'a bosaltilir!
- Cümleler bölünmeden (enable_text_splitting=False) tek nefeste, %100 duygusal bütünlükle sentezlenir.

Run via:  baslat_judy.bat
"""
import ctypes
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import winsound
from ctypes import wintypes

import judy_config
import judy_rag

try:
    if sys.stdout and hasattr(sys.stdout, "reconfigure") and sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TTS_LANGUAGE = os.getenv("JUDY_TTS_LANG", "en")
ENABLE_TTS = os.getenv("JUDY_ENABLE_TTS", "1") == "1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CET_DIR = judy_config.find_cet_dir() or os.path.join(
    r"C:\Program Files (x86)\Steam\steamapps\common\Cyberpunk 2077", "bin", "x64", "plugins", "cyber_engine_tweaks"
)
MOD_DIR = os.path.join(CET_DIR, "mods", "judy_ai_mod")
BINDINGS_FILE = os.path.join(CET_DIR, "bindings.json")

TRIGGER_FILE = os.path.join(MOD_DIR, "judy_chat_trigger.txt")
REQUEST_FILE = os.path.join(MOD_DIR, "judy_chat_request.txt")
RESPONSE_FILE = os.path.join(MOD_DIR, "judy_chat_response.txt")
STATE_FILE = os.path.join(MOD_DIR, "judy_overlay_state.txt")
CLOSE_FILE = os.path.join(MOD_DIR, "judy_chat_close.txt")
VRAM_FILE = os.path.join(MOD_DIR, "judy_vram_state.txt")
BRAIN_FILE = os.path.join(MOD_DIR, "judy_brain.json")  # Lua'nin yazdigi biten gorev listesi
BRAIN_POLL_SEC = 2.0


POLL_SEC = 0.2
OVERLAY_DELAY_SEC = 1.0      # cuddle tetiginden sonra overlay'in acilma gecikmesi
VOICE_RECONNECT_SEC = 30.0   # ses motoru yokken kac saniyede bir tekrar baglanilsin (LLM'e istek ATMAZ)
_inflight = threading.Lock()

GAME_STATE = {
    "V_ROMANCE_STATUS": "Active",
    "EVELYN_STATUS": "unknown",
    "MAIKO_STATUS": "unknown",
    "CURRENT_TIME_WEATHER": "night, quiet in the apartment",
    "V_OUTFIT_STYLE": "casual",
    "LATEST_QUEST_CONTEXT": "unknown",
}

_history: list[dict] = []
_voice_engine = None
_tts_lock = threading.Lock()
_reconnect_thread = None


# --------------------------------------------------------------------------- #
# CET overlay key -> synthetic keystroke
# --------------------------------------------------------------------------- #
user32 = ctypes.WinDLL("user32", use_last_error=True)
ULONG_PTR = wintypes.WPARAM
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send_key(vk: int, up: bool):
    scan = user32.MapVirtualKeyW(vk, 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    flags = KEYEVENTF_SCANCODE
    if up:
        flags |= KEYEVENTF_KEYUP
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def press_keys(vks: list[int], hold: float = 0.15):
    for vk in vks:
        _send_key(vk, up=False)
        time.sleep(0.01)
    time.sleep(hold)
    for vk in reversed(vks):
        _send_key(vk, up=True)
        time.sleep(0.01)


def load_overlay_vks() -> list[int]:
    override = os.getenv("JUDY_CET_KEY_VK")
    if override:
        return [int(override, 0)]
    with open(BINDINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    bind = int(data["cet"]["overlay_key"])
    vks = [(bind >> (16 * i)) & 0xFFFF for i in range(4)]
    return [v for v in vks if v]


def foreground_title() -> str:
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


# --------------------------------------------------------------------------- #
# File helpers
# --------------------------------------------------------------------------- #
def read_text(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-16", "cp1254"):
        try:
            return raw.decode(enc).replace("\x00", "").strip()
        except UnicodeError:
            continue
    return None


def remove_quiet(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def _claim_request() -> str | None:
    """Istegi tek ajan alsin. Dosya duruyorsa ayni satir ikinci kez Groq'a gitmesin."""
    if not os.path.exists(REQUEST_FILE):
        return None
    claimed = REQUEST_FILE + ".claimed"
    try:
        os.replace(REQUEST_FILE, claimed)
    except OSError:
        return None
    text = read_text(claimed)
    remove_quiet(claimed)
    return text or None


def overlay_is_open() -> bool:
    return read_text(STATE_FILE) == "1"


# --------------------------------------------------------------------------- #
# Hikaye takibi: Lua'nin judy_brain.json dosyasi -> GAME_STATE
# --------------------------------------------------------------------------- #
# Kronolojik SIRA burada tutulur (Lua sadece bitenlerin id'lerini gonderir, sirasi onemsiz).
# (id, Judy olayi biliyor mu, Ingilizce cumle)
#   knows=True  -> "paylasilan gecmis" olarak prompta girer (Judy bunlari yasadi; V acmadikca kendisi bahsetmez)
#   knows=False -> V'nin kendi isi: LATEST_QUEST_CONTEXT olur (RAG kural 6-7: V anlatana kadar Judy bilmez)
# Sonuclari fact gerektirenler (Maiko'ya ne yapildi gibi) burada YOK; fact adlari bulununca eklenir.
# Lua id'leri = judy_story.lua icindeki FACTS id'leri. "maiko_alive" Lua'dan gelmez, burada turetilir.
STORY_EVENTS = [
    ("heist", True, "The Konpeki Plaza heist went wrong, and Jackie died in it."),
    ("heroes", True, "Jackie's funeral happened."),
    ("both_sides", True, "Evelyn took her own life in Judy's apartment, and V helped Judy afterwards."),
    ("ex_factor", True, "Judy and V went through the mess with Maiko and Clouds together."),
    ("pizza", True, "Judy, V, Maiko, Tom and Roxanne planned the Clouds takeover over pizza."),
    ("pisces", True, "The Clouds takeover is over, and Hiromi Sato is dead."),
    ("maiko_dead", True, "V killed Maiko in the Clouds showdown, and Judy was in shock about it."),
    ("maiko_alive", True, "Maiko came out of the takeover alive."),
    ("judy_lover", True, "V and Judy are together."),
    ("judy_friend", True, "V and Judy stayed friends, nothing romantic."),
    ("sinnerman", False, "the Sinnerman job"),
]
_STORY_BY_ID = {e[0]: e for e in STORY_EVENTS}
SHARED_HISTORY_MAX = 12  # tum olaylar sigsin: eski ama onemli olaylar (Jackie, Evelyn) kesilmesin (~120 token)
_DEFAULT_ROMANCE = GAME_STATE["V_ROMANCE_STATUS"]

_brain_mtime = 0.0
_done_ids: set[str] = set()   # STORY_EVENTS icindeki biten olaylar (ozet cumleleri icin)
_state_ids: set[str] = set()  # Lua'nin yazdigi TUM id'ler (+turetilenler): dal parcalarinin requires sartlari icin


def poll_brain():
    """judy_brain.json ({"done": ["heist", ...]}) degistiyse oku ve GAME_STATE'i guncelle."""
    global _brain_mtime, _done_ids, _state_ids
    try:
        mtime = os.path.getmtime(BRAIN_FILE)
    except OSError:
        return
    if mtime == _brain_mtime:
        return
    raw = read_text(BRAIN_FILE)
    if not raw:
        return
    try:
        data = json.loads(raw)
    except ValueError:
        return  # Lua yaziyor olabilir; mtime guncellenmedi, bir sonraki turda tekrar dener
    _brain_mtime = mtime
    all_ids = {str(x).strip() for x in (data.get("done") or []) if str(x).strip()}

    # Turetilen sonuc: Pisces bitti ve Maiko oluk degilse hayatta.
    if "pisces" in all_ids and "maiko_dead" not in all_ids:
        all_ids.add("maiko_alive")
    _state_ids = all_ids
    ids = all_ids & set(_STORY_BY_ID)  # ozet cumlesi olmayan id'ler (maiko_boss gibi) sadece dal sartlarinda kullanilir
    _done_ids = ids

    # Kronolojik sira STORY_EVENTS'tan gelir
    done_events = [e for e in STORY_EVENTS if e[0] in _done_ids]
    hidden = [e for e in done_events if not e[1]]
    GAME_STATE["LATEST_QUEST_CONTEXT"] = hidden[-1][2] if hidden else "unknown"

    GAME_STATE["EVELYN_STATUS"] = (
        "Dead (she took her own life)" if "both_sides" in _done_ids else "unknown"
    )
    if "maiko_dead" in _done_ids:
        GAME_STATE["MAIKO_STATUS"] = "Dead"
    elif "maiko_alive" in _done_ids:
        GAME_STATE["MAIKO_STATUS"] = "Alive (she survived the Clouds takeover)"
    else:
        GAME_STATE["MAIKO_STATUS"] = "unknown"
    # Romantizm: sadece "arkadas" kaniti varsa degistir, yoksa varsayilana don (mod romantik sahne icin tasarlandi)
    if "judy_friend" in _done_ids and "judy_lover" not in _done_ids:
        GAME_STATE["V_ROMANCE_STATUS"] = "Friends only (not romantic)"
    else:
        GAME_STATE["V_ROMANCE_STATUS"] = _DEFAULT_ROMANCE
    print(f"[judy-brain] biten: {[e[0] for e in done_events]} | latest(gizli): {GAME_STATE['LATEST_QUEST_CONTEXT']}")
    print(f"[judy-brain] tum durum id'leri: {sorted(_state_ids)}")
    print(f"[judy-brain] romance={GAME_STATE['V_ROMANCE_STATUS']!r} maiko={GAME_STATE['MAIKO_STATUS']!r} evelyn={GAME_STATE['EVELYN_STATUS']!r}")


def _shared_history() -> str:
    """Judy'nin yasadigi olaylar: tek bir kisa blok. Bos ise ''."""
    lines = [e[2] for e in STORY_EVENTS if e[0] in _done_ids and e[1]][-SHARED_HISTORY_MAX:]
    if not lines:
        return ""
    return (
        "# SHARED HISTORY WITH V (things you lived through together)\n"
        + " ".join(lines)
        + "\nThese events and the statuses in CURRENT GAME STATE are CERTAIN knowledge: you were there or you know the outcome."
        + "\nIf V asks about any of them, answer directly and confidently with your real feelings. Never say you are unsure,"
        + " never say you only heard rumors, and do not ask V whether they saw something about these."
        + "\nRule 7 (rumors) applies ONLY to the single job named in rule 6, never to anything listed here."
        + "\nDo not bring these up yourself unless V does."
    )


# --------------------------------------------------------------------------- #
# LLM
# --------------------------------------------------------------------------- #
def _load_judy_server():
    import judy_server  # PyInstaller uyumlu: dosya yolundan degil modul olarak yuklenir

    return judy_server


_judy_server = None
_rag = None

_VOICE_RULE = (
    "1. VOICE RULE: Always respond strictly in English. Keep replies concise and intimate "
    "(1 to 2 short spoken sentences, maximum 22 words total) so real-time voice synthesis stays instant. "
    "Do not use asterisks or stage directions inside the 'text' field."
)
_MULTILINGUAL_RE = re.compile(
    r"1\. MULTILINGUAL RULE:.*?reply in Russian\)\.",
    re.S,
)
# Groq: "try again in 25.0s", "try again in 7.02s" veya "try again in 1m5.2s"
_RETRY_RE = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s")


def _retry_seconds(err_msg: str) -> float | None:
    m = _RETRY_RE.search(err_msg.lower())
    if not m:
        return None
    try:
        return int(m.group(1) or 0) * 60 + float(m.group(2))
    except ValueError:
        return None


def _load_rag():
    global _judy_server, _rag
    if _judy_server is None:
        _judy_server = _load_judy_server()
    if _rag is None:
        _rag = judy_rag.JudyRag(_judy_server.get_rag_prompt())
        n = _rag.add_chunks(_load_branch_chunks())
        print(f"[judy-rag] {len(_rag.chunks) - n} ani + {n} dal parcasi yuklendi")
    return _rag


def _load_branch_chunks() -> list:
    """RAG_branches.json: oyun sonuclarina (fact'lere) bagli ek anilar. Yoksa/bozuksa ajan yine calisir."""
    path = judy_config.resource_path("RAG_branches.json")
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[judy-rag] UYARI: RAG_branches.json okunamadi, dallar kapali: {e}")
        return []


def _fill_game_state(text: str) -> str:
    def fill(m):
        return GAME_STATE.get(m.group(1), "unknown")

    return re.sub(r"\{\s*([A-Z_]+)\s*\}", fill, text)


def _recent_text() -> str:
    return " ".join(m["content"] for m in _history[-2:])


def _system_for_turn(user_text: str) -> str:
    recent = _recent_text()
    rag = _load_rag()
    system = rag.build_prompt(user_text, recent, extra=_shared_history(), state=_state_ids)
    print(f"[judy-rag] anilar: {rag.picked_ids(user_text, recent, _state_ids)}")
    print(f"[judy-brain] prompta: maiko={GAME_STATE['MAIKO_STATUS']!r} gecmis_olay={len(_done_ids)}")
    system = _MULTILINGUAL_RE.sub(_VOICE_RULE, system, count=1)
    return _fill_game_state(system)


def _build_user_text(user_text: str) -> str:
    if not _history:
        return user_text
    lines = [f"{'V' if m['role'] == 'user' else 'Judy'}: {m['content']}" for m in _history[-4:]]
    return "[RECENT CONVERSATION]\n" + "\n".join(lines) + f"\n\n[V NOW SAYS]\n{user_text}"


def ask_judy(user_text: str) -> tuple[str, str, bool]:
    global _judy_server
    if _judy_server is None:
        _judy_server = _load_judy_server()

    provider = judy_config.get_ai_provider()
    api_key = judy_config.get_api_key()

    if not api_key:
        print("[judy] HATA: API Key eksik.")
        return "...the link dropped for a sec, V. Say that again?", "calm", False

    # URL ve model oyuncuya sorulmaz. Saglayiciya gore burada secilir.
    # llama3-8b-8192 ve gemini-1.5-flash kapali. Acik kimlikler kullanilir.
    if provider == "gemini":
        api_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        model_name = "gemini-3.8-flash"
    elif provider == "openai":
        api_url = "https://api.openai.com/v1/chat/completions"
        model_name = "gpt-4o-mini"
    else:
        provider = "groq"
        api_url = "https://api.groq.com/openai/v1/chat/completions"
        model_name = "qwen/qwen3.8-27b"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "JudyNeuralLink/1.0",
    }

    injected_system = _system_for_turn(user_text)

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": injected_system},
            {"role": "user", "content": _build_user_text(user_text)},
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }

    print(f"\n[~] Judy düşünüyor ({provider.upper()} - {model_name})...")

    # Gemini düşünerek cevaplar; 15 sn yetmez. Diger saglayicilar 15 sn.
    timeout_sec = 60.0 if provider == "gemini" else 15.0

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(api_url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            result = json.loads(response.read().decode("utf-8"))
            raw_answer = result["choices"][0]["message"]["content"]

        raw_answer = re.sub(r"<think>.*?</think>", "", raw_answer or "", flags=re.S).strip()
        try:
            parsed_data = json.loads(raw_answer)
            answer = str(parsed_data.get("text", "")).strip()
            emotion = str(parsed_data.get("emotion", "calm") or "calm").strip().lower()
        except Exception:
            answer = raw_answer
            emotion = "calm"

        if not answer:
            return "...the link dropped for a sec, V. Say that again?", "calm", False

        _history.append({"role": "user", "content": user_text})
        _history.append({"role": "assistant", "content": answer})
        return answer, emotion, True

    except urllib.error.HTTPError as e:
        err_msg = e.read().decode(errors="ignore")

        # 429: "try again in ..." icindeki sureyi ust kata birak.
        if e.code == 429:
            wait_time = _retry_seconds(err_msg)
            if wait_time is not None:
                return f"RATE_LIMIT:{wait_time}", "calm", False

        print(f"[judy] API Hatasi ({provider}): {err_msg}")
        return "...the link dropped for a sec, V. Say that again?", "calm", False
    except Exception as e:
        print(f"[judy] Baglanti Hatasi: {e}")
        return "...the link dropped for a sec, V. Say that again?", "calm", False


def write_response(text: str):
    for a, b in (
        ("\u2019", "'"),
        ("\u2018", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2014", "-"),
        ("\u2013", "-"),
        ("\u2026", "..."),
    ):
        text = text.replace(a, b)
    payload = f"{time.time_ns()}\n{text}"
    tmp = RESPONSE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(payload)
    os.replace(tmp, RESPONSE_FILE)


# --------------------------------------------------------------------------- #
# Main loop & Dynamic-VRAM Voice Pipeline
# --------------------------------------------------------------------------- #
def _connect_remote_voice(quiet: bool = False) -> bool:
    """Colab adresini cozup baglanir. Basarisizsa istisna firlatir, adres yoksa False doner."""
    global _voice_engine
    from remote_voice import RemoteVoiceEngine, load_colab_url

    colab_url = load_colab_url()
    if not colab_url:
        return False
    if not quiet:
        print(f"\n[*] Judy ses motoru: Colab (XTTSv2 + RVC v2) -> {colab_url}")
    _voice_engine = RemoteVoiceEngine(colab_url)
    print("[+] Colab ses motoru hazir! (Yerel VRAM kullanimi: 0 MB)\n")
    return True


def _voice_reconnect_loop():
    """Ajan Colab'dan once acildiysa: sadece /health ile sessizce tekrar dener. LLM'e istek atmaz."""
    # A fresh Cloudflare tunnel needs a few seconds before its DNS name resolves: retry fast first.
    quick_delays = [5.0, 5.0, 10.0, 10.0]
    while _voice_engine is None:
        time.sleep(quick_delays.pop(0) if quick_delays else VOICE_RECONNECT_SEC)
        try:
            if _connect_remote_voice(quiet=True):
                return
        except Exception:
            pass  # Colab hala kalkmadi; bir sonraki turda tekrar


def _start_voice_reconnect():
    global _reconnect_thread
    if _reconnect_thread is not None and _reconnect_thread.is_alive():
        return
    _reconnect_thread = threading.Thread(target=_voice_reconnect_loop, daemon=True)
    _reconnect_thread.start()


def init_voice_engine():
    global _voice_engine
    if not ENABLE_TTS:
        print("[*] TTS devre disi (JUDY_ENABLE_TTS=0).")
        return

    try:
        if _connect_remote_voice():
            return
        print("\n[!] Colab URL yok. Run the Colab notebook with your session code. Text only for now.")
    except Exception as e:
        print(f"[!] Colab sesine ulasilamadi. Yerel ekran karti acilmadi. Sadece metin: {e}")

    print(f"[*] Arka planda her {VOICE_RECONNECT_SEC:.0f} sn'de bir Colab tekrar denenecek (LLM'e istek atilmaz).")
    _start_voice_reconnect()


def _new_wav_path() -> str:
    return os.path.join(judy_config.data_dir(), f"judy_say_{time.time_ns()}.wav")


def _cleanup_old_wavs(max_keep: int = 2):
    d = judy_config.data_dir()
    try:
        files = sorted(f for f in os.listdir(d) if f.startswith("judy_say_") and f.endswith(".wav"))
    except OSError:
        return
    for f in files[:-max_keep]:
        remove_quiet(os.path.join(d, f))  # hala caliniyorsa Windows silmez; sorun degil, sonra silinir


def wait_overlay_state(want_open: bool, timeout: float = 1.5) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if overlay_is_open() == want_open:
            return True
        time.sleep(0.05)
    return False


def toggle_overlay(vks: list[int], want_open: bool):
    state = read_text(STATE_FILE)
    print(f"[judy] overlay state file = {state!r}, want_open = {want_open}")
    if overlay_is_open() == want_open:
        print("[judy] already in the wanted state, not pressing anything")
        return
    print(f"[judy] foreground window: {foreground_title()!r}")
    press_keys(vks)
    ok = wait_overlay_state(want_open)
    print(f"[judy] overlay {'opened' if want_open else 'closed'}: {'OK' if ok else 'no confirmation from Lua'}")


def handle_request(text: str, vks: list[int]):
    try:
        _handle_request(text, vks)
    finally:
        _inflight.release()


def _handle_request(text: str, vks: list[int]):
    print(f"[V] {text}")
    answer, emotion, ok = ask_judy(text)

    if not ok:
        if answer.startswith("RATE_LIMIT:"):
            # Saglayicinin istedigi sure. Ust sinir 30 sn.
            wait_time = float(answer.split(":")[1]) + 0.5
            wait_time = min(wait_time, 30.0)
            print(f"[judy] Kota doldu. {wait_time:.1f} saniye bekleniyor...")
            time.sleep(wait_time)
            answer, emotion, ok = ask_judy(text)
            if not ok and answer.startswith("RATE_LIMIT:"):
                answer = "...the link dropped for a sec, V. Say that again?"
        else:
            # Zaman asimi ve kota disi hatalarda ikinci istek yok.
            print("[judy] Zaman asimi veya kritik hata. Tekrar denenmiyor.")

    print(f"[Judy] {answer}")

    out_wav = None
    engine = _voice_engine  # reconnect thread ayni anda degistirebilir; yerel kopya al
    if engine is not None and ok:
        with _tts_lock:
            try:
                out_wav, dt = engine.synthesize(
                    answer,
                    language=TTS_LANGUAGE,
                    emotion=emotion,
                    out_path=_new_wav_path(),  # her cumle ayri dosya: onceki ses calarken uzerine yazilmaz
                )
                print(f"[judy-tts] Ses hazir ({dt:.2f}s) -> Senkronize oynatiliyor!")
            except Exception as e:
                print(f"[judy-tts] Sentez hatasi: {e}")

    write_response(answer)
    if out_wav and os.path.exists(out_wav):
        winsound.PlaySound(out_wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
        _cleanup_old_wavs()


def selftest():
    vks = load_overlay_vks()
    print("CET overlay key VK codes:", [hex(v) for v in vks])
    print("Mod dir exists:", os.path.isdir(MOD_DIR))
    print("Groq key set:", bool(judy_config.get_groq_key()))
    ans, emo, _ = ask_judy("Hey Judy, I'm home. How was your day?")
    print(f"Judy ({emo}) says:", ans)


def press_test():
    vks = load_overlay_vks()
    print(f"Pressing VK {[hex(v) for v in vks]} in 5 seconds - click on the Cyberpunk window NOW.")
    for i in range(5, 0, -1):
        print(f"  {i}...  foreground: {foreground_title()!r}")
        time.sleep(1)
    press_keys(vks)
    print("Pressed. Did the CET overlay toggle?")


def main():
    # Ikinci ajan ayni portu alamaz; acilista kendisi kapanir.
    import socket
    global _instance_lock
    _instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _instance_lock.bind(("127.0.0.1", 55555))
    except socket.error:
        print("[!] HATA: Baska bir Judy Ajani zaten arka planda calisiyor. Kendimi imha ediyorum.")
        sys.exit(0)

    if "--selftest" in sys.argv:
        selftest()
        return
    if "--press" in sys.argv:
        press_test()
        return

    try:
        vks = load_overlay_vks()
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"[!] Cannot read the Cyber Engine Tweaks overlay key ({e}).")
        print("    Start the game once, open the CET overlay and bind its hotkey, then jack in again.")
        sys.exit(2)
    for p in (TRIGGER_FILE, REQUEST_FILE, RESPONSE_FILE, STATE_FILE, CLOSE_FILE, VRAM_FILE):
        remove_quiet(p)

    init_voice_engine()

    print("=========================================================")
    print("[+] Judy Neural-Link Agent ACTIVE (Colab XTTSv2 + RVC v2)")
    print(f"    Language: {TTS_LANGUAGE.upper()} | Mode: Dynamic Proximity VRAM")
    print(f"    CET overlay key VK codes: {[hex(v) for v in vks]}")
    print(f"    Watching: {MOD_DIR}")
    print("    Waiting for V to Cuddle... (Ctrl+C to stop)")
    print("=========================================================")

    open_overlay_at = None  # cuddle tetigi sonrasi overlay'i acma zamani (dongu bloke olmaz)
    next_brain_poll = 0.0

    while True:
        try:
            if time.time() >= next_brain_poll:
                next_brain_poll = time.time() + BRAIN_POLL_SEC
                poll_brain()

            if os.path.exists(VRAM_FILE):
                vram_cmd = read_text(VRAM_FILE)
                remove_quiet(VRAM_FILE)
                engine = _voice_engine
                if engine is not None and vram_cmd:
                    with _tts_lock:
                        if vram_cmd == "LOAD_VRAM":
                            engine.mount_vram()
                        elif vram_cmd == "UNLOAD_VRAM":
                            engine.unmount_vram()

            if os.path.exists(TRIGGER_FILE):
                remove_quiet(TRIGGER_FILE)
                print(f"[judy] Cuddle trigger -> overlay {OVERLAY_DELAY_SEC}s sonra acilacak (V'nin yaklasmasi bekleniyor)")
                open_overlay_at = time.time() + OVERLAY_DELAY_SEC

            if open_overlay_at is not None and time.time() >= open_overlay_at:
                open_overlay_at = None
                print(f"[judy] {time.strftime('%H:%M:%S')} Opening CET overlay (state={read_text(STATE_FILE)!r})")
                press_keys(vks)
                if wait_overlay_state(True):
                    print(f"[judy] {time.strftime('%H:%M:%S')} overlay opened: OK")
                else:
                    print("[judy] overlay acilmadi / Lua'dan onay yok (tekrar basilmadi)")

            if os.path.exists(CLOSE_FILE):
                remove_quiet(CLOSE_FILE)
                print("[judy] Close button -> closing CET overlay")
                toggle_overlay(vks, want_open=False)

            if _inflight.acquire(blocking=False):
                req = _claim_request()
                if req:
                    threading.Thread(target=handle_request, args=(req, vks), daemon=True).start()
                else:
                    _inflight.release()
        except Exception as e:
            print(f"[judy] loop error: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[judy] bye")