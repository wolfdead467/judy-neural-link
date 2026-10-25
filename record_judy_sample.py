"""
Judy Referans Ses Yakalayici (WASAPI Loopback Recorder)
Mikrofon degil, dogrudan bilgisayarin ic sesini (Cyberpunk 2077 diyalog sesini)
stüdyo kalitesinde yakalar, bastaki/sondaki sessizlikleri kirpar ve
XTTSv2 icin ideal olan 24000 Hz Mono WAV formatinda 'judy_ornek.wav' olarak kaydeder.
"""
import os
import sys
import time
import wave
import numpy as np
import pyaudiowpatch as pyaudio
import scipy.signal as signal
import soundfile as sf

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_WAV = os.path.join(OUT_DIR, "judy_ornek.wav")
LATENT_CACHE = os.path.join(OUT_DIR, "judy_voice_latents.pt")
RECORD_SECONDS = 10


def record_system_audio(duration: int = RECORD_SECONDS):
    p = pyaudio.PyAudio()
    try:
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])

        if not default_speakers["isLoopbackDevice"]:
            for loopback in p.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    default_speakers = loopback
                    break

        print("====================================================")
        print("   JUDY OYUN ICI SES YAKALAYICI (STÜDYO KALITESI)")
        print("====================================================")
        print(f"[*] Cihaz: {default_speakers['name']}")
        print("[!] ONEMLI: Oyun ayarlarindan Music ve SFX sesini 0 yapin,")
        print("            sadece Dialogue sesini 100 birakin.")
        print("\n[*] 5 saniye icinde kayit basliyor! Oyuna gecin ve Judy'yi konusturun...")
        for i in range(5, 0, -1):
            print(f"    {i}...")
            time.sleep(1)

        rate = int(default_speakers["defaultSampleRate"])
        channels = int(default_speakers["maxInputChannels"])
        chunk = 1024

        print(f"\n[KAYIT BASLADI] >>> {duration} saniye boyunca Judy'nin sesini dinliyorum...")
        frames = []

        with p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            frames_per_buffer=chunk,
            input=True,
            input_device_index=default_speakers["index"],
        ) as stream:
            for _ in range(0, int(rate / chunk * duration)):
                data = stream.read(chunk, exception_on_overflow=False)
                frames.append(data)

        print("[+] Kayit tamamlandi! Ses isleniyor ve optimize ediliyor...")

        raw_pcm = b"".join(frames)
        audio = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)

        # 24000 Hz Mono'ya (XTTSv2 native sample rate) resample et
        target_sr = 24000
        if rate != target_sr:
            num_samples = int(len(audio) * target_sr / rate)
            audio = signal.resample(audio, num_samples)

        # Bastaki ve sondaki bosluklari (sessizligi) otomatik kirp
        abs_audio = np.abs(audio)
        threshold = max(0.015, np.max(abs_audio) * 0.05)
        active_indices = np.where(abs_audio > threshold)[0]
        if len(active_indices) > target_sr:  # En az 1 saniye ses varsa kirp
            pad = int(0.15 * target_sr)
            start_idx = max(0, active_indices[0] - pad)
            end_idx = min(len(audio), active_indices[-1] + pad)
            audio = audio[start_idx:end_idx]

        # Ses seviyesini normalize et (-1 dB peak)
        peak = np.max(np.abs(audio))
        if peak > 1e-4:
            audio = (audio / peak) * 0.89

        sf.write(OUT_WAV, audio, target_sr, subtype="PCM_16")
        if os.path.exists(LATENT_CACHE):
            os.remove(LATENT_CACHE)

        dur = len(audio) / target_sr
        print(f"[+] BASARILI! '{OUT_WAV}' kaydedildi ({dur:.2f} saniye, 24000 Hz Mono).")
        print("[*] Simdi 'test_voice.py' calistirilarak Judy'nin sesi klonlanabilir!")
    finally:
        p.terminate()


if __name__ == "__main__":
    sec = int(sys.argv[1]) if len(sys.argv) > 1 else RECORD_SECONDS
    record_system_audio(sec)
