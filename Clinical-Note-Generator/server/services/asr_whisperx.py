# C:\Clinical-Note-Generator\server\services\asr_whisperx.py
import gc
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import torch
from omegaconf import DictConfig, ListConfig
from omegaconf.base import ContainerMetadata
from torch.serialization import add_safe_globals
from whisperx.diarize import Segment as SegmentX
from whisperx.vads.vad import Vad

# Allow pyannote/WhisperX checkpoints to unpickle OmegaConf configs under
# PyTorch 2.6+'s weights-only loader.
add_safe_globals([ListConfig, DictConfig, ContainerMetadata, Any, list])


class PassthroughVAD(Vad):
    """Minimal VAD that marks the whole audio as a single speech segment."""

    def __init__(self, vad_onset: float = 0.5):
        super().__init__(vad_onset=vad_onset)

    @staticmethod
    def preprocess_audio(audio):
        return audio

    @staticmethod
    def merge_chunks(segments, chunk_size, onset: float, offset: Optional[float]):
        return Vad.merge_chunks(segments, chunk_size, onset, offset)

    def __call__(self, audio, **kwargs):
        """Return the entire audio split into manageable 30-second chunks"""
        waveform = None
        sample_rate = 16000  # WhisperX default

        if isinstance(audio, dict):
            waveform = audio.get("waveform")
            sample_rate = float(audio.get("sample_rate") or 16000)
        else:
            waveform = getattr(audio, "waveform", None)
            sample_rate = float(getattr(audio, "sample_rate", 16000))

        if waveform is None:
            return []

        sample_rate = max(1.0, sample_rate)
        duration = len(waveform) / sample_rate

        if duration <= 0:
            return []

        # Split into 30-second chunks to avoid input shape errors
        chunk_duration = 30.0
        segments = []
        start = 0.0

        while start < duration:
            end = min(start + chunk_duration, duration)
            segments.append(SegmentX(start, end, "SPEECH"))
            start = end

        return segments


try:
    from whisperx.vads import pyannote as whisperx_pyannote  # type: ignore

    class _BypassPyannoteVAD(PassthroughVAD):
        """Override WhisperX Pyannote VAD with a passthrough implementation."""

        def __init__(self, *args, **kwargs):
            super().__init__()

        def __call__(self, audio, **kwargs):
            return super().__call__(audio, **kwargs)

    whisperx_pyannote.Pyannote = _BypassPyannoteVAD  # type: ignore[attr-defined]
except Exception:
    pass


class ASRSession:
    def __init__(self, session_id: str, initial_prompt: Optional[str] = None, save_audio: bool = True, file_suffix: Optional[str] = None):
        self.id = session_id
        self.initial_prompt = initial_prompt
        self.save_audio = save_audio
        self._chunks: bytearray = bytearray()
        self.temp_path: Optional[str] = None
        self.file_suffix: Optional[str] = file_suffix

    def append(self, data: bytes) -> int:
        self._chunks.extend(data)
        return len(self._chunks)

    def _detect_suffix(self) -> str:
        head = bytes(self._chunks[:16]) if self._chunks else b""
        try:
            if head.startswith(b"RIFF"):
                return ".wav"
            if head.startswith(b"OggS"):
                return ".ogg"
            if head.startswith(b"\x1A\x45\xDF\xA3"):
                return ".webm"
            if head.startswith(b"fLaC"):
                return ".flac"
        except Exception:
            pass
        return ".mp3"

    def finalize_to_file(self) -> str:
        if self.temp_path is None:
            suffix = self.file_suffix or self._detect_suffix()
            if not suffix.startswith("."):
                suffix = "." + suffix
            fd, path = tempfile.mkstemp(suffix=suffix, prefix=f"asr_{self.id}_")
            os.close(fd)
            self.temp_path = path
        with open(self.temp_path, "wb") as f:
            f.write(self._chunks)
        return self.temp_path


class WhisperXASREngine:
    ENGINE_NAME = "whisperx"

    # Hardcoded WhisperX settings per user request (can be moved to config later)
    WHISPERX_MODEL_PATH = r"C:\Clinical-Note-Generator\models\whisper\medium.en"
    DEVICE = "cuda"  # Default; can be overridden by env/config
    ALIGN_DEVICE = "cpu"
    DIAR_DEVICE = "cpu"
    COMPUTE_TYPE = "float16"
    LANGUAGE = "en"
    SAVE_AUDIO = True
    RETAINED_AUDIO = 5

    # Performance / memory knobs
    TRANSCRIBE_BATCH_SIZE = 5  # D) lower peak VRAM vs 16

    # HF token: prefer env; fallback to provided token if needed
    HF_TOKEN_FALLBACK = "NONE"

    def __init__(self):
        self._cfg = self._load_config()
        self._ffmpeg_bin: Optional[str] = None
        self.model_size_or_path = self._resolve_model_path()
        self.device = self._resolve_device("ASR_DEVICE", "asr_device", self.DEVICE)
        self._align_device = self._resolve_device("ASR_ALIGN_DEVICE", "asr_align_device", self.ALIGN_DEVICE)
        self._diar_device = self._resolve_device("ASR_DIAR_DEVICE", "asr_diar_device", self.DIAR_DEVICE)
        self._compute_type = self._resolve_compute_type()
        self._language = self.LANGUAGE
        self._initial_prompt = self._resolve_initial_prompt()
        self._transcribe_batch_size = self._resolve_transcribe_batch_size()
        self._enable_diarization = str(os.environ.get("ASR_ENABLE_DIARIZATION", "1")).strip().lower() not in {"0", "false", "no", "off"}
        self._enable_alignment = str(os.environ.get("ASR_ENABLE_ALIGNMENT", "1")).strip().lower() not in {"0", "false", "no", "off"}
        self._save_audio = bool(self.SAVE_AUDIO)
        self._retained_audio = int(self.RETAINED_AUDIO)
        self._temp_audio_dir = (Path(__file__).resolve().parents[1] / "temp-audio").resolve()
        self._temp_audio_dir.mkdir(parents=True, exist_ok=True)
        self._vad = PassthroughVAD()

        # Optional diarization speaker count
        self._default_num_speakers: Optional[int] = None

        # Models are loaded lazily
        self._wx_model = None
        self._align_model = None
        self._align_meta = None
        self._diar_model = None

        self.sessions: Dict[str, ASRSession] = {}
        self._auto_flush = True
        self._configure_ffmpeg_path()

    @staticmethod
    def _load_config() -> Dict[str, Any]:
        try:
            cfg_path = Path(__file__).resolve().parents[2] / "config" / "config.json"
            if cfg_path.exists():
                with cfg_path.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
        except Exception:
            pass
        return {}

    def _resolve_device(self, env_key: str, cfg_key: str, default: str) -> str:
        raw = (os.environ.get(env_key) or self._cfg.get(cfg_key) or default or "").strip().lower()
        if not raw:
            return "cpu"
        if raw == "cpu":
            return "cpu"
        if raw == "cuda":
            return "cuda"
        if raw.startswith("cuda:"):
            return raw
        print(f"[ASR] Unsupported device '{raw}' for {env_key}/{cfg_key}; falling back to {default}")
        return default

    def _resolve_compute_type(self) -> str:
        env = (os.environ.get("ASR_COMPUTE_TYPE") or "").strip()
        if env:
            return env
        cfg = self._cfg.get("asr_compute_type")
        if isinstance(cfg, str) and cfg.strip():
            return cfg.strip()
        return self.COMPUTE_TYPE

    def _resolve_initial_prompt(self) -> Optional[str]:
        env = os.environ.get("ASR_INITIAL_PROMPT")
        if env is not None:
            val = env.strip()
            return val if val else None
        cfg = self._cfg.get("initial_prompt")
        if isinstance(cfg, str):
            val = cfg.strip()
            return val if val else None
        return None

    def _resolve_model_path(self) -> str:
        env = os.environ.get("ASR_MODEL_PATH")
        if env and env.strip():
            return env.strip()
        cfg = self._cfg.get("asr_model_path")
        if isinstance(cfg, str) and cfg.strip():
            return cfg.strip()
        legacy = self._cfg.get("whisper_model")
        if isinstance(legacy, str) and legacy.strip() and legacy.strip().lower() != "none":
            return legacy.strip()
        return self.WHISPERX_MODEL_PATH

    def _resolve_transcribe_batch_size(self) -> int:
        env = os.environ.get("ASR_TRANSCRIBE_BATCH_SIZE")
        if env and env.strip():
            try:
                return max(1, int(env.strip()))
            except Exception:
                print(f"[ASR] Invalid ASR_TRANSCRIBE_BATCH_SIZE='{env}', using default")
        cfg = self._cfg.get("asr_transcribe_batch_size")
        if isinstance(cfg, int) and cfg > 0:
            return cfg
        if isinstance(cfg, str) and cfg.strip():
            try:
                return max(1, int(cfg.strip()))
            except Exception:
                pass
        return int(self.TRANSCRIBE_BATCH_SIZE)

    def _configure_ffmpeg_path(self) -> None:
        """
        Ensure ffmpeg is discoverable even when running as a Windows service.
        """
        ffmpeg_cfg = self._cfg.get("ffmpeg_path")
        if isinstance(ffmpeg_cfg, str) and ffmpeg_cfg.strip():
            ffmpeg_path = Path(ffmpeg_cfg.strip())
            if ffmpeg_path.exists():
                self._ffmpeg_bin = str(ffmpeg_path)
                ffmpeg_dir = str(ffmpeg_path.parent)
                current_path = os.environ.get("PATH", "")
                if ffmpeg_dir not in current_path:
                    os.environ["PATH"] = ffmpeg_dir + os.pathsep + current_path
                os.environ.setdefault("FFMPEG_BINARY", str(ffmpeg_path))

    def _resolve_ffmpeg_bin(self) -> Optional[str]:
        env = os.environ.get("FFMPEG_BIN")
        if env and os.path.exists(env):
            return env
        if self._ffmpeg_bin and os.path.exists(self._ffmpeg_bin):
            return self._ffmpeg_bin
        return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")

    def _maybe_ffmpeg_convert(self, src_path: str) -> str:
        ext = os.path.splitext(src_path)[1].lower()
        if ext == ".wav":
            return src_path
        ffmpeg_bin = self._resolve_ffmpeg_bin()
        if not ffmpeg_bin:
            return src_path
        dst_path = src_path + ".tmp.wav"
        cmd = [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error", "-i", src_path, "-ar", "16000", "-ac", "1", dst_path]
        try:
            subprocess.check_call(cmd)
            return dst_path
        except Exception:
            return src_path

    def _ensure_models(self):
        # Prefer masking visible devices via env so WhisperX can use device='cuda'
        # IMPORTANT: Do NOT change CUDA visibility here; rely on launcher env
        try:
            print(f"[ASR] CUDA_VISIBLE_DEVICES (seen by ASR): {os.environ.get('CUDA_VISIBLE_DEVICES', '<unset>')}")
        except Exception:
            pass

        if self._wx_model is None:
            try:
                import whisperx  # type: ignore
            except Exception as e:
                raise RuntimeError(f"whisperx is not installed: {e}")

            model_id = self.model_size_or_path
            print(f"[ASR] Loading WhisperX: {model_id}")
            print(f"[ASR] Device: {self.device}, Compute type: {self._compute_type}, Language: {self._language}")
            print("[ASR] Voice activity detection disabled (passthrough mode)")

            # Keep ASR model on configured device (often GPU for speed)
            self._wx_model = whisperx.load_model(
                model_id,
                device=self.device,
                compute_type=self._compute_type,
                vad_model=self._vad,
                vad_method="silero",
                vad_options={"chunk_size": 30},
            )

        # B) Alignment on CPU to reduce GPU memory pressure and contention
        if self._align_model is None and self._enable_alignment:
            import whisperx  # type: ignore
            self._align_model, self._align_meta = whisperx.load_align_model(
                language_code=self._language,
                device=self._align_device,
            )

        # B) Diarization on CPU (or disabled) to minimize GPU contention with llama.cpp
        if self._diar_model is None and self._enable_diarization:
            try:
                try:
                    from whisperx.diarize import DiarizationPipeline  # type: ignore
                    pipeline_src = "whisperx.diarize.DiarizationPipeline"
                except Exception:
                    import whisperx  # type: ignore
                    DiarizationPipeline = getattr(whisperx, "DiarizationPipeline", None)
                    pipeline_src = "whisperx.DiarizationPipeline"
                if DiarizationPipeline is None:
                    raise AttributeError("DiarizationPipeline not found in whisperx")

                hf_token = os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN") or self.HF_TOKEN_FALLBACK
                print(f"[ASR] Initializing diarization pipeline from {pipeline_src}; token_present={'yes' if hf_token else 'no'}")

                # E) Scope the torch.load weights_only patch only to diarization init
                original_torch_load = torch.load

                def patched_torch_load(*args, **kwargs):
                    kwargs["weights_only"] = False
                    return original_torch_load(*args, **kwargs)

                try:
                    torch.load = patched_torch_load  # type: ignore[assignment]
                    self._diar_model = DiarizationPipeline(
                        use_auth_token=hf_token,
                        device=self._diar_device,
                    )
                    print("[ASR] Diarization pipeline initialized successfully")
                finally:
                    torch.load = original_torch_load  # restore no matter what

            except Exception as e:
                print(f"[ASR] Diarization unavailable ({e}); proceeding without diarization")
                self._diar_model = None
        elif not self._enable_diarization:
            self._diar_model = None

    def warmup(self) -> None:
        self._ensure_models()

    # Back-compat: some code expects _ensure_model (singular)
    def _ensure_model(self):
        self._ensure_models()

    # C) stronger CUDA + Python cleanup after each job
    def _flush_cuda_cache(self) -> None:
        if not self._auto_flush:
            return

        try:
            if torch.cuda.is_available():
                # Ensure all kernels are complete before freeing caches
                try:
                    torch.cuda.synchronize()
                except Exception:
                    pass

                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass

                # Helps release CUDA IPC resources in long-lived services
                try:
                    torch.cuda.ipc_collect()
                except Exception:
                    pass
        except Exception:
            pass

        # Ensure Python releases references promptly
        try:
            gc.collect()
        except Exception:
            pass

    try:
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        if hasattr(torch, "set_float32_matmul_precision"):
            torch.set_float32_matmul_precision("high")
    except Exception:
        pass

    # ---- Session API ----
    def new_session(self, initial_prompt: Optional[str] = None, file_suffix: Optional[str] = None) -> str:
        sid = str(uuid.uuid4())
        self.sessions[sid] = ASRSession(sid, initial_prompt=initial_prompt, save_audio=self._save_audio, file_suffix=file_suffix)
        return sid

    def set_num_speakers(self, session_id: str, num: int) -> None:
        try:
            self._default_num_speakers = int(num)
        except Exception:
            self._default_num_speakers = None

    def append_chunk(self, session_id: str, data: bytes) -> int:
        sess = self.sessions.get(session_id)
        if not sess:
            raise KeyError("invalid session")
        return sess.append(data)

    # ---- Transcription helpers ----
    def _retain_audio_copy(self, wav_path: str) -> None:
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            name = f"asr_{ts}_{os.path.basename(wav_path)}"
            dst = self._temp_audio_dir / name
            shutil.copyfile(wav_path, dst)
            # Keep last N files only
            files = sorted(self._temp_audio_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[self._retained_audio:]:
                try:
                    old.unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception:
            pass

    def _transcribe_internal(self, wav_path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        self._ensure_models()
        import whisperx  # type: ignore

        audio = whisperx.load_audio(wav_path)

        result: Dict[str, Any] = {}
        aligned: Optional[Dict[str, Any]] = None
        diar = None

        try:
            # D) inference_mode reduces allocations and VRAM pressure
            with torch.inference_mode():
                try:
                    result = self._wx_model.transcribe(
                        audio,
                        batch_size=int(self._transcribe_batch_size),
                        language=self._language,
                        initial_prompt=self._initial_prompt,
                    )
                except TypeError:
                    result = self._wx_model.transcribe(
                        audio,
                        batch_size=int(self._transcribe_batch_size),
                        language=self._language,
                    )

                # B) Alignment on CPU: pass device="cpu" (your align model is CPU)
                if self._enable_alignment and self._align_model is not None:
                    try:
                        aligned = whisperx.align(
                            result.get("segments", []),
                            self._align_model,
                            self._align_meta,
                            audio,
                            device=self._align_device,
                        )
                        result["segments"] = aligned.get("segments", result.get("segments", []))
                    except Exception as e:
                        print(f"[ASR] Alignment failed: {e}")

                # Diarization (optional, CPU in this setup)
                if self._diar_model is not None:
                    try:
                        diar = self._diar_model(audio)
                        result = whisperx.assign_word_speakers(diar, result)
                    except Exception as e:
                        print(f"[ASR] Diarization failed: {e}")

            return result.get("segments", []), result

        finally:
            # C) ensure large references are dropped before CUDA cache flush
            try:
                del diar
            except Exception:
                pass
            try:
                del aligned
            except Exception:
                pass
            try:
                del result
            except Exception:
                pass
            try:
                del audio
            except Exception:
                pass

            self._flush_cuda_cache()

    def _format_segments(self, segments: List[Dict[str, Any]]) -> List[str]:
        lines: List[str] = []
        for seg in segments:
            txt = str(seg.get("text", "")).strip()
            if not txt:
                continue
            spk = seg.get("speaker") or seg.get("speaker_id") or None
            if spk is None and isinstance(seg.get("words"), list):
                for w in seg.get("words", []):
                    spk = w.get("speaker") or w.get("speaker_id")
                    if spk:
                        break
            prefix = f"[{spk}] " if spk else ""
            lines.append(prefix + txt)
        return lines

    def transcribe_stream(self, session_id: str) -> Iterator[str]:
        sess = self.sessions.get(session_id)
        if not sess:
            raise KeyError("invalid session")
        src_path = sess.finalize_to_file()
        wav_path = self._maybe_ffmpeg_convert(src_path)
        try:
            print(f"[ASR] Transcribe stream: file={wav_path}")
            segs, _ = self._transcribe_internal(wav_path)
            if self._save_audio and os.path.exists(wav_path):
                self._retain_audio_copy(wav_path)
        finally:
            pass

        for line in self._format_segments(segs):
            yield line

    def transcribe(self, session_id: str) -> Tuple[str, float]:
        sess = self.sessions.get(session_id)
        if not sess:
            raise KeyError("invalid session")
        src_path = sess.finalize_to_file()
        wav_path = self._maybe_ffmpeg_convert(src_path)
        print(f"[ASR] Transcribe (oneshot): file={wav_path}")
        segs, _res = self._transcribe_internal(wav_path)
        if self._save_audio and os.path.exists(wav_path):
            self._retain_audio_copy(wav_path)
        text = "\n".join(self._format_segments(segs)).strip()

        # Confidence proxy if available
        try:
            logs: List[float] = []
            for s in segs:
                v = s.get("avg_logprob")
                if isinstance(v, (int, float)):
                    logs.append(float(v))
            conf = float(min(1.0, max(0.0, (sum(logs) / len(logs) + 5) / 10))) if logs else (0.85 if text else 0.0)
        except Exception:
            conf = 0.85 if text else 0.0
        return text, conf

    def cleanup_session(self, session_id: str) -> None:
        sess = self.sessions.get(session_id)
        if not sess:
            return
        try:
            if getattr(sess, "temp_path", None) and os.path.exists(sess.temp_path):
                try:
                    os.remove(sess.temp_path)  # type: ignore[arg-type]
                except Exception:
                    pass
            tmp_wav = (sess.temp_path or "") + ".tmp.wav"
            if tmp_wav and os.path.exists(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except Exception:
                    pass
        finally:
            try:
                if session_id in self.sessions:
                    del self.sessions[session_id]
            except Exception:
                pass

    def get_info(self) -> Dict[str, str]:
        try:
            warmed = "yes" if self._wx_model is not None else "no"
            return {
                "engine": self.ENGINE_NAME,
                "device": str(self.device),
                "model": str(self.model_size_or_path),
                "warmed": warmed,
                "compute_type": str(self._compute_type),
                "language": self._language,
                "diarization": "disabled" if not self._enable_diarization else ("on" if self._diar_model is not None else "off"),
                "alignment": "disabled" if not self._enable_alignment else ("on" if self._align_model is not None else "off"),
                "cuda_visible": os.environ.get("CUDA_VISIBLE_DEVICES", "<unset>"),
                "align_device": str(self._align_device),
                "diar_device": str(self._diar_device) if self._enable_diarization else "disabled",
                "transcribe_batch_size": str(self._transcribe_batch_size),
            }
        except Exception:
            return {"engine": self.ENGINE_NAME}
