"""
Judy Neural-Link ortak ayar modulu (launcher, agent, server hepsi bunu kullanir).

- config.json: Groq key, oturum kodu, CET yolu, mod (colab / local)
- Oyun (Steam / GOG / Epic) ve CET klasorunu otomatik bulur
- PyInstaller (frozen) ve normal Python calismasi icin yol yardimcilari
"""
import json
import os
import re
import secrets
import sys

# --- Yayin oncesi doldurulacak sabitler (config.json ile de ezilebilir) ----------------
HF_REPO = "wolfdead/judy-neural-link-voice"
COLAB_NOTEBOOK_URL = "https://colab.research.google.com/notebook#fileId=https%3A//huggingface.co/wolfdead/judy-neural-link-voice.ipynb"
# --------------------------------------------------------------------------------------

APP_NAME = "JudyNeuralLink"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_path(rel: str) -> str:
    """Pakete gomulu salt-okunur dosyalar (RAG, cet_mod, ui)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel)


def data_dir() -> str:
    """Yazilabilir kullanici verisi: exe ise %APPDATA%, gelistirmede proje klasoru."""
    if is_frozen():
        d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    else:
        d = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(d, exist_ok=True)
    return d


CONFIG_FILE = os.path.join(data_dir(), "config.json")


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(**updates) -> dict:
    cfg = load_config()
    cfg.update(updates)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)
    return cfg


def get_groq_key() -> str:
    return (os.getenv("JUDY_GROQ_KEY") or load_config().get("groq_key") or "").strip()


def get_api_key() -> str:
    return (os.getenv("JUDY_API_KEY") or load_config().get("api_key") or get_groq_key()).strip()


def get_ai_provider() -> str:
    # Varsayılan olarak groq kullan
    return (os.getenv("JUDY_AI_PROVIDER") or load_config().get("ai_provider") or "groq").strip().lower()


def get_api_url() -> str:
    # Varsayılan olarak Groq URL'si kalır, kullanıcı isterse değiştirir
    return (os.getenv("JUDY_API_URL") or load_config().get("api_url") or "https://api.groq.com/openai/v1/chat/completions").strip()


def get_model_name() -> str:
    # llama3-8b-8192 Groq'ta 2025-08-30'da kapandı. Varsayılan çalışan model.
    return (os.getenv("JUDY_MODEL") or load_config().get("model_name") or "qwen/qwen3.8-27b").strip()


def get_session_code(create: bool = True) -> str:
    """16 karakterlik rastgele kod: hem rolenin kanal adini hem Colab sunucu sifresini turetir."""
    code = (os.getenv("JUDY_SESSION_CODE") or load_config().get("session_code") or "").strip()
    if not code and create:
        code = secrets.token_urlsafe(12)
        save_config(session_code=code)
    return code


def new_session_code() -> str:
    code = secrets.token_urlsafe(12)
    save_config(session_code=code)
    return code


def get_mode() -> str:
    return (os.getenv("JUDY_MODE") or load_config().get("mode") or "colab").strip().lower()


def get_notebook_url() -> str:
    return load_config().get("notebook_url") or COLAB_NOTEBOOK_URL


# --------------------------------------------------------------------------- #
# Oyun / CET bulma
# --------------------------------------------------------------------------- #
def _steam_roots() -> list[str]:
    roots: list[str] = []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            roots.append(winreg.QueryValueEx(k, "SteamPath")[0].replace("/", "\\"))
    except Exception:
        pass
    roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    libs: list[str] = []
    for base in roots:
        libs.append(base)
        vdf = os.path.join(base, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, "r", encoding="utf-8", errors="ignore") as f:
                for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                    libs.append(m.group(1).replace("\\\\", "\\"))
        except OSError:
            pass
    return libs


def _game_candidates() -> list[str]:
    out = [os.path.join(lib, "steamapps", "common", "Cyberpunk 2077") for lib in _steam_roots()]
    out += [
        r"C:\GOG Games\Cyberpunk 2077",
        r"C:\Program Files (x86)\GOG Galaxy\Games\Cyberpunk 2077",
        r"C:\Program Files\Epic Games\Cyberpunk2077",
        r"C:\Program Files (x86)\Epic Games\Cyberpunk2077",
    ]
    return out


def find_game_dir() -> str | None:
    cfg_dir = load_config().get("game_dir")
    cands = ([cfg_dir] if cfg_dir else []) + _game_candidates()
    for d in cands:
        if d and os.path.isfile(os.path.join(d, "bin", "x64", "Cyberpunk2077.exe")):
            return d
    return None


def cet_dir_for(game_dir: str) -> str:
    return os.path.join(game_dir, "bin", "x64", "plugins", "cyber_engine_tweaks")


def find_cet_dir() -> str | None:
    """CET kurulu oyun klasoru (yoksa None). Oyun var ama CET yoksa da None doner."""
    g = find_game_dir()
    if g:
        d = cet_dir_for(g)
        if os.path.isdir(d):
            return d
    return None
