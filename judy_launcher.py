"""Judy Neural-Link launcher. pywebview + ui/launcher.html.

V1 yalnizca Colab: ses Hugging Face'ten iner, tünel Cloudflare, link oturum koduyla role uzerinden gelir.
"""
import filecmp
import os
import shutil
import subprocess
import sys
import urllib.request
import webbrowser

import judy_config as jc
import relay
import remote_voice

CREATE_NO_WINDOW = 0x08000000


class Api:
    def __init__(self):
        self.proc = None
        self._log = None
        self._window = None

    def state(self) -> dict:
        game = jc.find_game_dir()
        cet = jc.find_cet_dir()
        return {
            "groq_key": jc.get_api_key(),
            "ai_provider": jc.get_ai_provider(),
            "session_code": jc.get_session_code(create=True),
            "game_dir": game or "",
            "cet_dir": cet or "",
            "notebook_url": jc.get_notebook_url(),
            "hf_repo": jc.HF_REPO,
            "agent_running": self._running(),
        }

    def save_groq(self, key: str) -> bool:
        key = (key or "").strip()
        jc.save_config(groq_key=key, api_key=key)
        return True

    def save_provider(self, key: str, provider: str) -> bool:
        key = (key or "").strip()
        provider = (provider or "groq").strip().lower()
        if provider not in ("groq", "gemini", "openai"):
            provider = "groq"
        updates = {"api_key": key, "ai_provider": provider}
        if provider == "groq":
            updates["groq_key"] = key
        jc.save_config(**updates)
        return True

    def open_key_page(self, provider: str) -> bool:
        pages = {
            "gemini": "https://aistudio.google.com/apikey",
            "openai": "https://platform.openai.com/api-keys",
            "groq": "https://console.groq.com/keys",
        }
        webbrowser.open(pages.get((provider or "groq").strip().lower(), pages["groq"]))
        return True

    def open_groq(self) -> bool:
        webbrowser.open("https://console.groq.com/keys")
        return True

    def open_colab(self) -> bool:
        webbrowser.open(jc.get_notebook_url())
        return True

    def browse_game(self) -> dict:
        """Let the player pick the Cyberpunk 2077 folder when it is not in a standard place."""
        import webview

        if self._window is None:
            return {"ok": False, "error": "No window."}
        kind = webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
        picked = self._window.create_file_dialog(kind)
        if not picked:
            return {"ok": False, "error": ""}  # cancelled
        folder = os.path.normpath(picked[0] if isinstance(picked, (list, tuple)) else picked)
        # Accept the game root, but also bin\x64 or anything just below it.
        probe = folder
        for _ in range(4):
            if os.path.isfile(os.path.join(probe, "bin", "x64", "Cyberpunk2077.exe")):
                jc.save_config(game_dir=probe)
                return {"ok": True, "game_dir": probe}
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        return {"ok": False, "error": "That folder does not contain Cyberpunk 2077 (bin\\x64\\Cyberpunk2077.exe)."}

    def install_cet(self) -> dict:
        game = jc.find_game_dir()
        if not game:
            return {"ok": False, "error": "Cyberpunk 2077 not found."}
        cet = jc.cet_dir_for(game)
        if not os.path.isdir(cet):
            return {"ok": False, "error": "Cyber Engine Tweaks is not installed. Install it first."}
        dest = os.path.join(cet, "mods", "judy_ai_mod")
        try:
            os.makedirs(dest, exist_ok=True)
            for name in ("init.lua", "judy_story.lua"):
                src = jc.resource_path(os.path.join("cet_mod", name))
                if not os.path.isfile(src):
                    return {"ok": False, "error": f"cet_mod/{name} is missing."}
                target = os.path.join(dest, name)
                # Keep the player's own edits: back up init.lua once if it differs.
                if name == "init.lua" and os.path.isfile(target) and not filecmp.cmp(src, target, shallow=False):
                    shutil.copy2(target, target + ".bak")
                shutil.copy2(src, target)
        except OSError as e:
            return {"ok": False, "error": f"Could not write to the game folder ({e.strerror or e}). Close the game and try running the launcher as administrator."}
        jc.save_config(game_dir=game)
        return {"ok": True}

    def start_agent(self) -> dict:
        if not jc.get_api_key():
            return {"ok": False, "message": "Enter your API key first."}
        if self._running():
            return {"ok": True, "message": "Already running."}
        log_path = os.path.join(jc.data_dir(), "agent.log")
        self._log = open(log_path, "a", encoding="utf-8")
        flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        if jc.is_frozen():
            # Packaged exe: run the agent inside this same exe (no Python on the player's PC).
            cmd, cwd = [sys.executable, "--agent"], jc.data_dir()
        else:
            agent = os.path.join(os.path.dirname(os.path.abspath(__file__)), "judy_agent.py")
            cmd, cwd = [sys.executable, "-u", agent], os.path.dirname(agent)
        self.proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=flags,
        )
        return {"ok": True, "message": "Judy is listening. Go back to the game."}

    def stop_agent(self) -> dict:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
        return {"ok": True, "message": "Link closed."}

    def colab_status(self) -> dict:
        """Is the Colab server reachable? Used by the launcher's Voice track."""
        try:
            url = remote_voice.resolve_url()
        except Exception:
            url = ""
        if not url:
            return {"ok": False, "message": "Colab not found. Run the notebook."}
        headers = {"User-Agent": relay.UA}
        token = jc.get_session_code(create=False)
        if token:
            headers["X-Judy-Token"] = token
        try:
            req = urllib.request.Request(url.rstrip("/") + "/health", headers=headers)
            with urllib.request.urlopen(req, timeout=6) as r:
                return {"ok": r.status == 200}
        except Exception:
            return {"ok": False, "message": "Colab is not answering. Is the notebook running?"}

    def agent_status(self) -> dict:
        """Cheap poll for the UI. exit_code is set when the agent stopped by itself."""
        if self.proc is None:
            return {"running": False, "exit_code": None}
        code = self.proc.poll()
        if code is None:
            return {"running": True, "exit_code": None}
        self.proc = None
        return {"running": False, "exit_code": code}

    def _running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


def main() -> None:
    if "--agent" in sys.argv:
        sys.argv.remove("--agent")
        import judy_agent

        judy_agent.main()
        return

    import webview

    api = Api()
    html = jc.resource_path(os.path.join("ui", "launcher.html"))
    window = webview.create_window(
        "Judy Neural-Link",
        html,
        js_api=api,
        width=440,
        height=740,
        background_color="#05060a",
        resizable=False,
    )

    def _close():
        api.stop_agent()

    api._window = window
    window.events.closing += _close
    webview.start()


if __name__ == "__main__":
    main()
