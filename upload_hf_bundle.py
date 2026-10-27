"""Public Hugging Face model repo'suna Judy ses bundle'ini yukler.

Token bu dosyaya yazilmaz. Once kendi terminalinde:

    huggingface-cli login

Sonra:

    python upload_hf_bundle.py HF_KULLANICI_ADIN

Repo: HF_KULLANICI_ADIN/judy-neural-link-voice  (public, yaklasik 440 MB)
"""
import os
import sys
import tempfile

from huggingface_hub import HfApi

REPO_NAME = "judy-neural-link-voice"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ALLOW = [
    "colab_server.py",
    "test_voice.py",
    "rvc_converter.py",
    "relay.py",
    "judy_voice_latents.pt",
    "rvc_judy/Judy_Alvarez.pth",
    "rvc_judy/rmvpe.pt",
    "rvc_judy/*.index",
    "rvc/**/*.py",
]

README = """---
license: other
tags:
  - text-to-speech
---

# Judy Neural-Link voice bundle

Fan-mod voice runtime for a personal Cyberpunk 2077 companion. Colab downloads this repo with `snapshot_download`.

Contains cached XTTS v2 speaker latents, the RVC v2 40 kHz weights and the small Python runtime (`colab_server.py`). No recording is included. Unofficial fan project, not affiliated with CD PROJEKT RED. The voice is AI-generated and has not been endorsed by the original voice actor.

No API keys live in this repo.
"""


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        sys.exit("Kullanim: python upload_hf_bundle.py HF_KULLANICI_ADIN")
    user = sys.argv[1].strip().strip("/")
    repo_id = f"{user}/{REPO_NAME}"

    api = HfApi()
    try:
        who = api.whoami()
    except Exception as e:
        sys.exit(
            "HF girisi yok. Token'i sohbete yapistirma.\n"
            "Once tarayicidan bir write token al: https://huggingface.co/settings/tokens\n"
            "Sonra giris yap:\n"
            "  huggingface-cli login"
            f"\n{e}"
        )
    name = who.get("name") or who.get("fullname")
    print(f"Giris: {name}")
    if name and name.lower() != user.lower():
        print(f"Uyari: komuttaki kullanici ({user}) giris yapan hesapla ({name}) ayni degil.")

    print(f"Repo aciliyor: {repo_id}")
    api.create_repo(repo_id, repo_type="model", private=False, exist_ok=True)

    print("Dosyalar yukleniyor (~440 MB)...")
    api.upload_folder(
        repo_id=repo_id,
        repo_type="model",
        folder_path=BASE_DIR,
        allow_patterns=ALLOW,
        ignore_patterns=["**/__pycache__/**", "**/*.pyc"],
        commit_message="Add Judy voice bundle",
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as f:
        f.write(README)
        readme_path = f.name
    try:
        api.upload_file(
            repo_id=repo_id,
            repo_type="model",
            path_or_fileobj=readme_path,
            path_in_repo="README.md",
            commit_message="Add model card",
        )
    finally:
        os.remove(readme_path)

    print(f"Bitti: https://huggingface.co/{repo_id}")
    print(f"judy_config.py icinde HF_REPO = \"{repo_id}\"")


if __name__ == "__main__":
    main()
