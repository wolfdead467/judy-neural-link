# Judy Neural-Link (beta)

An AI companion mod for Cyberpunk 2077. Talk to Judy Alvarez in a chat overlay. She remembers what happened in your playthrough, answers in her own voice, and stays in character.

> **Fan-made and unofficial.** Not affiliated with, endorsed by, or connected to CD PROJEKT RED. Cyberpunk 2077 and Judy Alvarez belong to their respective owners.
>
> **This mod uses generative AI.** Judy's dialogue is written by a large language model. Her voice is AI-generated (XTTS v2 plus RVC voice conversion) and imitates the original voice actor. The speaker profile was derived from a short clip taken from the game's audio. No recording is included in this repository, and the voice actor has not endorsed this project.

## What it does

- **Chat with Judy** in a CET overlay. Replies come from an LLM you choose (Groq, Gemini or OpenAI) with your own API key.
- **Story-aware.** The mod reads which quests you have finished (for example the Heist, Evelyn, Maiko) and gives Judy only the memories that fit what you just said. She will not talk about events you have not reached.
- **Voice (optional).** Her replies are spoken. The heavy voice models run on a free Google Colab GPU, so your PC needs no extra VRAM while the game is running.
- **Text-only works too.** If you skip Colab, Judy still answers in text.

## Requirements

- Cyberpunk 2077 (PC, Steam, GOG or Epic) and [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107) installed. Open the CET overlay once and bind its hotkey.
- Windows 10 or 11 with the Microsoft Edge WebView2 runtime (already present on most systems).
- An API key from Groq, Google AI Studio (Gemini) or OpenAI. Free tiers exist but limits change. Groq worked best in testing.
- For voice only: a Google account and a Colab session with a T4 GPU.

## Install

1. Download the latest build from **Releases** and unzip it anywhere.
2. Run `JudyNeuralLink.exe`.
3. Pick an AI provider and paste your API key (use **Get a key** to open the right page).
4. Press **Install mod**. The launcher finds the game and copies the mod into `...\cyber_engine_tweaks\mods\judy_ai_mod`. If it cannot find the game, press **Find game** and choose the game folder.
5. Press **Jack in**, then start the game.

### Voice (optional)

1. In the launcher press **Copy** next to the session code, then **Open Colab**.
2. In Colab choose *Runtime > Change runtime type > T4 GPU*.
3. Paste the session code into the form and run the cell. Loading takes a few minutes the first time.
4. When Colab prints `=== CONNECTED ===`, the **Voice** light in the launcher turns green. Keep the Colab tab open while you play.

The session code stays the same between sessions. It is also the password for your private voice server, so do not share it.

## In game

if u are relationship with judy invite her in ur house ,sit on the couch and cuddle her the chat log will open and u can talk with judy

## Known issues (beta)

- Spoken replies take about 6 to 10 seconds on a free Colab GPU.
- Free Colab sessions can disconnect or refuse a GPU. Run the notebook again if the Voice light goes yellow. A fresh tunnel can take a few seconds before it answers.
- You have to start the Colab notebook before each play session.
- Windows only. Judy's replies are in English.
- Free LLM tiers can hit rate limits. Judy then says the link dropped. Wait a moment and speak again.
- I didn't checked the mod is really work at V's other houses it should work but idk  
- LLM will reply u with the language u speak to her but the voice engine can struggle to make pronunciation so i recommend to speak English

## Privacy

- What you type goes to the AI provider you choose. Judy's reply text goes to your Colab session to be turned into speech.
- Your API key is stored only on your PC in `%APPDATA%\JudyNeuralLink\config.json` and is sent only to your provider.
- The Colab link is passed from Colab to your PC through the public relay [ntfy.sh](https://ntfy.sh), under a channel name derived from a hash of your session code. The code itself is never sent to the relay.
- The mod itself has no telemetry.

## Troubleshooting

| Message | What to do |
|---|---|
| *Cyberpunk 2077 not found* | Press **Find game** and choose the game folder. |
| *Cyber Engine Tweaks is not installed* | Install CET first, start the game once, then press **Install mod** again. |
| *Cannot read the Cyber Engine Tweaks overlay key* | Start the game, open the CET overlay and bind its hotkey, then jack in again. |
| *Could not write to the game folder* | Close the game. If the game is under Program Files, run the launcher as administrator. |
| Voice light stays yellow | Make sure the Colab cell is still running and shows `=== CONNECTED ===`. |

The log is at `%APPDATA%\JudyNeuralLink\agent.log`. Remove anything private before you share it in an issue.

## Build from source

```powershell
python -m venv .build_venv
.\.build_venv\Scripts\python -m pip install -r requirements-build.txt
.\.build_venv\Scripts\pyinstaller --noconfirm JudyNeuralLink.spec
```

The result is in `dist\JudyNeuralLink\`. To run without building: `pip install -r requirements.txt`, then `python judy_launcher.py`.

The voice models are not in this repository. The Colab notebook downloads them from Hugging Face: [wolfdead/judy-neural-link-voice](https://huggingface.co/wolfdead/judy-neural-link-voice).

## Project layout

| Path | Purpose |
|---|---|
| `judy_launcher.py`, `ui/` | Launcher window |
| `judy_agent.py`, `judy_rag.py`, `judy_server.py`, `RAG`, `RAG_branches.json` | The agent: reads game state, builds the prompt, calls the LLM, plays the voice |
| `cet_mod/` | The in-game CET mod (Lua) |
| `notebook.ipynb`, `colab_server.py`, `test_voice.py`, `rvc_converter.py`, `rvc/` | Voice server that runs on Colab |
| `remote_voice.py`, `relay.py` | PC side of the voice link |

## License

The code in this repository is released under the [MIT License](LICENSE). Third-party software, models and game content keep their own licenses, see [THIRD_PARTY.md](THIRD_PARTY.md). The MIT license does not cover Cyberpunk 2077 content or the voice model.
