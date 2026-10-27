import time
import json
import os
import urllib.request
import urllib.error

# ==========================================
# JUDY NEURAL-LINK: YAPAY ZEKA BEYNİ (GROQ API)
# ==========================================

# 1. AYARLAR VE YOLLAR
# Groq anahtari artik kodda tutulmaz: launcher'dan (config.json) ya da JUDY_GROQ_KEY env'den okunur.
import judy_config

GAME_DIR = os.path.join(judy_config.find_cet_dir() or "", "mods", "JudyAI")
BRAIN_FILE = os.path.join(GAME_DIR, "judy_brain.json")
RAG_FILE = judy_config.resource_path("RAG")

# 2. RAG DOSYASINI OKUMA
def get_rag_prompt():
    try:
        with open(RAG_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"[-] Could not read the RAG file: {e}")
        return "You are Judy Alvarez from Cyberpunk 2077."

# 3. GROQ API İLE İLETİŞİM (Ekstra kütüphane kurdurmamak için dahili urllib kullanıyoruz)
_cached_groq_key = ""


def ask_judy(system_prompt, user_text, game_context):
    url = "https://api.groq.com/openai/v1/chat/completions"
    # config.json launcher tarafindan yazilirken okuma bos donebilir.
    # Son saglam anahtari tut; bos okuma sohbeti dusurmesin.
    global _cached_groq_key
    api_key = judy_config.get_groq_key()
    if api_key:
        _cached_groq_key = api_key
    else:
        api_key = _cached_groq_key
    if not api_key:
        return '{"text": "API error: no Groq API key set (launcher).", "emotion": "SAD", "action": "none"}'
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    # RAG komutlarına Oyunun o anki durumunu da ekliyoruz (Context Injection)
    injected_system = system_prompt + f"\n\n[CURRENT GAME CONTEXT]\n{json.dumps(game_context)}\n"
    
    payload = {
        "model": "qwen/qwen3.8-27b", 
        "messages": [
            {"role": "system", "content": injected_system},
            {"role": "user", "content": user_text}
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"}
    }
    
    print("\n[~] Judy is thinking (connecting to the Groq API)...")
    
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode()
        return f'{{"text": "API error: {err_msg}", "emotion": "SAD", "action": "none"}}'
    except Exception as e:
        return f'{{"text": "Connection error: {e}", "emotion": "SAD", "action": "none"}}'

# 4. ANA DÖNGÜ (OYUNU DİNLEME VE KULLANICI GİRİŞİ)
def start_server():
    print("========================================")
    print("  JUDY NEURAL-LINK BRAIN ACTIVE (GROQ)")
    print("========================================")
    print(f"[*] Waiting for the game signal: {BRAIN_FILE}\n")
    
    system_prompt = get_rag_prompt()
    latest_game_state = {"status": "Waiting for game data"}

    while True:
        # Oyundan gelen taze veriyi kontrol et
        if os.path.exists(BRAIN_FILE):
            try:
                with open(BRAIN_FILE, "r", encoding="utf-8") as f:
                    data = f.read()
                if data.strip():
                    latest_game_state = json.loads(data)
                    print(f"[+] New memories from the game: {latest_game_state}")
                os.remove(BRAIN_FILE)
            except:
                pass
        
        # Telepati arayüzü (Kullanıcıdan yazı bekle)
        print("\n----------------------------------------")
        user_input = input("V (Sen): ")
        
        if user_input.strip() == "":
            continue
        if user_input.lower() in ['exit', 'quit']:
            print("Disconnecting...")
            break
            
        # Groq API'ye gönder
        response_json_str = ask_judy(system_prompt, user_input, latest_game_state)
        
        try:
            # Gelen cevabı JSON olarak çöz ve ekrana şıkça yazdır
            judy_response = json.loads(response_json_str)
            print(f"\nJUDY ({judy_response.get('emotion', 'CALM')}): {judy_response.get('text', '')}")
            if judy_response.get('action') and judy_response.get('action') != "none":
                print(f"*[Judy Yapar: {judy_response.get('action')}]*")
        except Exception as e:
            print(f"\nJUDY (corrupted signal): {response_json_str}")
        
        time.sleep(0.1)

if __name__ == "__main__":
    start_server()
