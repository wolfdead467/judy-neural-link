"""
Dynamic-VRAM (CUDA <-> CPU Hot-Swappable) RVC v2 Voice Converter for Judy Alvarez
Uses PyTorch CUDA in-room and drops to 0 MB VRAM (CPU) when leaving the apartment.
"""
import gc
import math
import os
import time
import faiss
import librosa
import numpy as np
import scipy.signal as signal
import soundfile as sf
import torch
import torch.nn.functional as F
from transformers import HubertModel

from rvc.lib.algorithm.synthesizers import Synthesizer
from rvc.lib.predictors.RMVPE import RMVPE0Predictor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RVC_DIR = os.path.join(BASE_DIR, "rvc_judy")
PTH_PATH = os.path.join(RVC_DIR, "Judy_Alvarez.pth")
INDEX_PATH = os.path.join(RVC_DIR, "added_IVF1831_Flat_nprobe_1_Judy_Alvarez_v2.index")
RMVPE_PATH = os.path.join(RVC_DIR, "rmvpe.pt")
CONTENTVEC_REPO = "lengyue233/content-vec-best"


class HubertModelWithFinalProj(HubertModel):
    def __init__(self, config):
        super().__init__(config)
        self.final_proj = torch.nn.Linear(config.hidden_size, config.classifier_proj_size)


def fast_resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return audio
    g = math.gcd(orig_sr, target_sr)
    return signal.resample_poly(audio, target_sr // g, orig_sr // g).astype(np.float32)


class JudyRVCConverter:
    def __init__(self, device: str = "cpu"):
        self.device = device
        t0 = time.time()
        print(f"[*] RVC v2 (Judy Alvarez 40kHz) motoru hazirlaniyor [{self.device.upper()}]...")

        self.hubert = HubertModelWithFinalProj.from_pretrained(CONTENTVEC_REPO).to(self.device)
        self.hubert.eval()

        self.rmvpe = RMVPE0Predictor(RMVPE_PATH, device=self.device)

        cpt = torch.load(PTH_PATH, map_location=self.device, weights_only=False)
        self.tgt_sr = cpt["config"][-1]  # 40000
        cpt["config"][-3] = cpt["weight"]["emb_g.weight"].shape[0]
        self.net_g = Synthesizer(
            *cpt["config"],
            use_f0=cpt.get("f0", 1) == 1,
            text_enc_hidden_dim=768,
            vocoder="HiFi-GAN",
        )
        self.net_g.load_state_dict(cpt["weight"], strict=False)
        self.net_g.eval().to(self.device)
        self.net_g.remove_weight_norm()
        del self.net_g.enc_q

        self.index = None
        self.big_npy = None
        # Index ON by default: it pulls the timbre toward Judy's real recordings.
        # Set JUDY_USE_FAISS=0 to skip it and save ~450 MB RAM (voice gets less Judy-like).
        if os.getenv("JUDY_USE_FAISS", "1") == "1" and os.path.exists(INDEX_PATH):
            self.index = faiss.read_index(INDEX_PATH)
            self.big_npy = self.index.reconstruct_n(0, self.index.ntotal)

        print(f"[+] Judy RVC v2 hazir! ({time.time() - t0:.2f} sn, {self.tgt_sr} Hz, index={'ON' if self.index is not None else 'OFF'})")

    def set_device(self, target_device: str):
        """Hot-swaps RVC models between System RAM ('cpu') and GPU VRAM ('cuda')."""
        if self.device == target_device:
            return
        self.device = target_device
        self.hubert = self.hubert.to(target_device)
        self.net_g = self.net_g.to(target_device)
        self.rmvpe.device = target_device
        self.rmvpe.model = self.rmvpe.model.to(target_device)
        self.rmvpe.mel_extractor = self.rmvpe.mel_extractor.to(target_device)
        if target_device == "cpu" and torch.cuda.is_available():
            gc.collect()
            torch.cuda.empty_cache()

    def _get_f0_post(self, f0: np.ndarray, f0_up_key: float = 0.0):
        f0 *= pow(2, f0_up_key / 12)
        f0_mel = 1127.0 * np.log(1.0 + f0 / 700.0)
        f0_mel_min = 1127.0 * np.log(1.0 + 50.0 / 700.0)
        f0_mel_max = 1127.0 * np.log(1.0 + 1100.0 / 700.0)
        mask = f0_mel > 0
        f0_mel[mask] = (f0_mel[mask] - f0_mel_min) * 254.0 / (f0_mel_max - f0_mel_min) + 1.0
        f0_mel[f0_mel <= 1] = 1
        f0_mel[f0_mel > 255] = 255
        f0_coarse = np.rint(f0_mel).astype(np.int32)
        return f0_coarse, f0

    def convert(
        self,
        in_wav_path: str,
        out_wav_path: str,
        f0_up_key: float = -1.0,
        index_rate: float = 0.75,
        rms_mix_rate: float = 0.25,
    ) -> tuple[str, float]:
        t0 = time.time()
        audio, sr = sf.read(in_wav_path, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        audio_16k = librosa.resample(audio, orig_sr=sr, target_sr=16000)
        bh, ah = signal.butter(N=5, Wn=48, btype="high", fs=16000)
        audio_16k = signal.filtfilt(bh, ah, audio_16k)

        pad_len = 4800
        audio_pad = np.pad(audio_16k, (pad_len, pad_len), mode="reflect")
        p_len = audio_pad.shape[0] // 160

        with torch.inference_mode():
            feats = torch.from_numpy(audio_pad).float().unsqueeze(0).to(self.device)
            out_hubert = self.hubert(feats, output_hidden_states=True)
            feats = out_hubert.hidden_states[12]

            if self.index is not None and self.big_npy is not None and index_rate > 0:
                npy = feats[0].cpu().numpy().astype("float32")
                score, ix = self.index.search(npy, k=8)
                weight = np.square(1.0 / (score + 1e-6))
                weight /= weight.sum(axis=1, keepdims=True)
                npy = np.sum(self.big_npy[ix] * np.expand_dims(weight, axis=2), axis=1)
                feats = (
                    torch.from_numpy(npy).unsqueeze(0).to(self.device) * index_rate
                    + (1.0 - index_rate) * feats
                )

            feats = F.interpolate(feats.permute(0, 2, 1), scale_factor=2).permute(0, 2, 1)
            p_len = min(p_len, feats.shape[1])

            f0 = self.rmvpe.infer_from_audio(audio_pad, thred=0.03)
            pitch, pitchf = self._get_f0_post(f0[:p_len], f0_up_key=f0_up_key)

            p_len = min(p_len, pitch.shape[0])
            feats = feats[:, :p_len, :]
            pitch_t = torch.tensor(pitch[:p_len], device=self.device, dtype=torch.long).unsqueeze(0)
            pitchf_t = torch.tensor(pitchf[:p_len], device=self.device, dtype=torch.float32).unsqueeze(0)
            p_len_t = torch.tensor([p_len], device=self.device, dtype=torch.long)
            sid = torch.tensor([0], device=self.device, dtype=torch.long)

            audio_out = (
                self.net_g.infer(feats, p_len_t, pitch_t, pitchf_t, sid)[0][0, 0]
                .data.cpu()
                .float()
                .numpy()
            )

        cut_samples = int(pad_len / 16000.0 * self.tgt_sr)
        if len(audio_out) > 2 * cut_samples:
            audio_out = audio_out[cut_samples:-cut_samples]

        if rms_mix_rate < 1.0:
            rms1 = librosa.feature.rms(y=audio_16k, frame_length=640, hop_length=160)
            rms1 = F.interpolate(
                torch.from_numpy(rms1).unsqueeze(0),
                size=audio_out.shape[0],
                mode="linear",
            )[0, 0].numpy()
            rms2 = librosa.feature.rms(
                y=audio_out,
                frame_length=int(0.04 * self.tgt_sr),
                hop_length=int(0.01 * self.tgt_sr),
            )
            rms2 = F.interpolate(
                torch.from_numpy(rms2).unsqueeze(0),
                size=audio_out.shape[0],
                mode="linear",
            )[0, 0].numpy()
            rms2 = np.maximum(rms2, 1e-4)
            audio_out *= np.power(rms1 / rms2, 1.0 - rms_mix_rate)

        peak = np.max(np.abs(audio_out))
        if peak > 1e-4:
            audio_out = (audio_out / peak) * 0.92

        sf.write(out_wav_path, audio_out, self.tgt_sr, subtype="PCM_16")
        return out_wav_path, time.time() - t0
