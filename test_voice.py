"""
Judy Dynamic-VRAM XTTSv2 + RVC v2 (40kHz) Ses Motoru (Method C)
- Odaya girince (LOAD_VRAM): XTTSv2 + RVC v2 modelleri RTX 3050 VRAM'ine (CUDA) tasinir (~2-3 sn uretim!).
- Odadan cikinca (UNLOAD_VRAM): Modeller Sistem RAM'ine (CPU) cekilir ve VRAM 0 MB'a bosaltilir!
- enable_text_splitting=False ile cumleler bolunmeden tek nefeste, %100 duygusal butunlukle sentezlenir.
"""
import gc
import os
import sys
import time
import numpy as np
import soundfile as sf
import torch

from rvc_converter import JudyRVCConverter

try:
    if hasattr(sys.stdout, "reconfigure") and sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

os.environ["COQUI_TOS_AGREED"] = "1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAKER_WAV = os.path.join(BASE_DIR, "judy_ornek.wav")
CLEAN_REF_WAV = os.path.join(BASE_DIR, "judy_ornek_packed.wav")
LATENT_CACHE = os.path.join(BASE_DIR, "judy_voice_latents.pt")
RAW_OUT_WAV = os.path.join(BASE_DIR, "judy_test_raw.wav")
RVC_OUT_WAV = os.path.join(BASE_DIR, "judy_test_cikti.wav")


def get_vram_mb() -> float:
    if torch.cuda.is_available():
        return torch.cuda.memory_allocated() / (1024 * 1024)
    return 0.0


def prepare_tight_reference(wav_path: str, out_path: str) -> str:
    audio, sr = sf.read(wav_path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    frame_len = int(0.04 * sr)
    kept_frames = []
    silence_count = 0
    for i in range(0, len(audio), frame_len):
        chunk = audio[i : i + frame_len]
        rms = np.sqrt(np.mean(chunk ** 2))
        if rms > 0.012:
            kept_frames.append(chunk)
            silence_count = 0
        else:
            silence_count += 1
            if silence_count <= 3:
                kept_frames.append(chunk)

    tight_audio = np.concatenate(kept_frames) if kept_frames else audio
    if len(tight_audio) / sr < 8.0:
        pause = np.zeros(int(0.12 * sr), dtype=np.float32)
        tight_audio = np.concatenate([tight_audio, pause, tight_audio])

    sf.write(out_path, tight_audio, sr, subtype="PCM_16")
    return out_path


class JudyVoiceEngine:
    def __init__(self, start_on_gpu: bool = True, enable_rvc: bool = True):
        from TTS.api import TTS

        self.has_cuda = torch.cuda.is_available()
        self.device = "cuda" if (start_on_gpu and self.has_cuda) else "cpu"
        print(f"[*] XTTSv2 + RVC v2 Ses Motoru Baslatiliyor [Baslangic Modu: {self.device.upper()}]...")

        t0 = time.time()
        self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(self.device)
        self.model = self.tts.synthesizer.tts_model
        print(f"[+] XTTSv2 hazir! ({time.time() - t0:.2f} sn)")

        self.gpt_cond_latent = None
        self.speaker_embedding = None
        self.rvc = JudyRVCConverter(device=self.device) if enable_rvc else None
        self.load_or_compute_latents(SPEAKER_WAV)
        print(f"[*] Aktif GPU VRAM Kullanimi: {get_vram_mb():.1f} MB")

    def mount_vram(self):
        """Odaya girildiginde modelleri 0.4 saniyede RTX 3050 VRAM'ine tasir."""
        if not self.has_cuda or self.device == "cuda":
            return
        t0 = time.time()
        self.device = "cuda"
        self.model = self.model.to("cuda")
        if self.gpt_cond_latent is not None:
            self.gpt_cond_latent = self.gpt_cond_latent.to("cuda")
        if self.speaker_embedding is not None:
            self.speaker_embedding = self.speaker_embedding.to("cuda")
        if self.rvc is not None:
            self.rvc.set_device("cuda")
        print(f"[VRAM-MOUNT] Oda ici mod aktif -> Ses motoru GPU'ya tasindi ({time.time() - t0:.2f}s | VRAM: {get_vram_mb():.1f} MB)")

    def unmount_vram(self):
        """Odadan cikildiginda modelleri Sistem RAM'ine ceker ve VRAM'i 0 MB'a sifirlar."""
        if not self.has_cuda or self.device == "cpu":
            return
        t0 = time.time()
        self.device = "cpu"
        self.model = self.model.to("cpu")
        if self.gpt_cond_latent is not None:
            self.gpt_cond_latent = self.gpt_cond_latent.to("cpu")
        if self.speaker_embedding is not None:
            self.speaker_embedding = self.speaker_embedding.to("cpu")
        if self.rvc is not None:
            self.rvc.set_device("cpu")
        gc.collect()
        torch.cuda.empty_cache()
        print(f"[VRAM-UNMOUNT] Odadan cikildi -> GPU VRAM bosaltildi ({time.time() - t0:.2f}s | VRAM: {get_vram_mb():.1f} MB)")

    def load_or_compute_latents(self, wav_path: str = SPEAKER_WAV, force_recompute: bool = False):
        if os.path.exists(LATENT_CACHE) and not force_recompute:
            data = torch.load(LATENT_CACHE, map_location=self.device, weights_only=True)
            self.gpt_cond_latent = data["gpt_cond_latent"].to(self.device)
            self.speaker_embedding = data["speaker_embedding"].to(self.device)
            return

        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"'{wav_path}' bulunamadi!")

        packed_wav = prepare_tight_reference(wav_path, CLEAN_REF_WAV)
        with torch.inference_mode():
            gpt_cond_latent, speaker_embedding = self.model.get_conditioning_latents(
                audio_path=[packed_wav],
                gpt_cond_len=18,
                gpt_cond_chunk_len=3,
                max_ref_length=20,
                sound_norm_refs=True,
            )
        self.gpt_cond_latent = gpt_cond_latent.to(self.device)
        self.speaker_embedding = speaker_embedding.to(self.device)
        torch.save(
            {
                "gpt_cond_latent": gpt_cond_latent.cpu(),
                "speaker_embedding": speaker_embedding.cpu(),
            },
            LATENT_CACHE,
        )

    def synthesize(
        self,
        text: str,
        language: str = "en",
        emotion: str = "calm",
        out_path: str = RVC_OUT_WAV,
    ) -> tuple[str, float]:
        # Eger sohbet sirasinda hala CPU'daysa otomatik GPU'ya al
        if self.has_cuda and self.device != "cuda":
            self.mount_vram()

        if self.gpt_cond_latent is None or self.speaker_embedding is None:
            self.load_or_compute_latents()

        # Noktadan noktaya kopukluk olmamasi icin cumle icindeki sert noktalari yumusak virgul/akisa cevir
        clean_text = text.strip()

        t0 = time.time()
        with torch.inference_mode():
            out = self.model.inference(
                text=clean_text,
                language=language,
                gpt_cond_latent=self.gpt_cond_latent,
                speaker_embedding=self.speaker_embedding,
                temperature=0.35,
                length_penalty=1.0,
                repetition_penalty=6.0,
                top_k=35,
                top_p=0.80,
                speed=0.97,
                enable_text_splitting=False,  # TEK NEFESTE KESINTISIZ DUYGUSAL URETIM!
            )

        wav = np.asarray(out["wav"], dtype=np.float32)
        sf.write(RAW_OUT_WAV, wav, 24000, subtype="PCM_16")
        dt_xtts = time.time() - t0

        if self.rvc is not None:
            out_file, dt_rvc = self.rvc.convert(
                RAW_OUT_WAV,
                out_path,
                f0_up_key=-1.0,
                index_rate=0.75,
                rms_mix_rate=0.25,
            )
            print(f"    -> [CUDA] XTTSv2 (Tek Nefes): {dt_xtts:.2f}s | RVC v2 (40kHz): {dt_rvc:.2f}s")
            return out_file, time.time() - t0

        return RAW_OUT_WAV, dt_xtts


def main():
    import winsound  # Windows-only; Colab (Linux) sunucusu bu fonksiyonu kullanmaz

    print("================================================================")
    print("   JUDY DYNAMIC-VRAM SES MOTORU (CUDA XTTSv2 + RVC v2 40kHz)")
    print("================================================================")

    engine = JudyVoiceEngine(start_on_gpu=True, enable_rvc=True)

    metin = "You really are a smart one, choom, not just a pretty gonk. So what are you thinking about while you're just lying there?"
    print(f"\n[*] SENTEZLENIYOR: '{metin}'")
    out_file, dt = engine.synthesize(metin, language="en")
    print(f"[+] Toplam sure: {dt:.2f} saniye | Cikti: {out_file}")

    # Test VRAM Unmount / Mount
    engine.unmount_vram()
    engine.mount_vram()

    winsound.PlaySound(out_file, winsound.SND_FILENAME)


if __name__ == "__main__":
    main()
