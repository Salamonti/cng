# C:\Clinical-Note-Generator\asr\asr_engine_cpu.py
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from whisperx.vads.vad import Vad


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
        waveform = None
        sample_rate = 16000

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

        chunk_duration = 30.0
        segments = []
        start = 0.0
        while start < duration:
            end = min(start + chunk_duration, duration)
            segments.append((start, end))
            start = end
        return [self._segment(start, end) for start, end in segments]

    @staticmethod
    def _segment(start: float, end: float):
        from whisperx.diarize import Segment as SegmentX  # local import to keep deps minimal
        return SegmentX(start, end, "SPEECH")


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


class WhisperXCPUASREngine:
    ENGINE_NAME = "whisperx-cpu"

    WHISPERX_MODEL_PATH = "base.en"
    WHISPERX_MODEL_DIR = r"D:\Models\Whisper"
    DEVICE = "cpu"
    COMPUTE_TYPE = "int8"
    LANGUAGE = "en"
    SAVE_AUDIO = True
    RETAINED_AUDIO = 5

    def __init__(self):
        self._cfg = self._load_config()
        self._ffmpeg_bin: Optional[str] = None
        self.model_size_or_path = os.environ.get("ASR_CPU_MODEL") or self.WHISPERX_MODEL_PATH
        self.model_dir = os.environ.get("ASR_CPU_MODEL_DIR") or self.WHISPERX_MODEL_DIR
        self.device = self.DEVICE
        self._compute_type = self._resolve_compute_type()
        self._language = self.LANGUAGE
        self._save_audio = bool(self.SAVE_AUDIO)
        self._retained_audio = int(self.RETAINED_AUDIO)
        self._temp_audio_dir = (Path(__file__).resolve().parents[1] / "temp-audio").resolve()
        self._temp_audio_dir.mkdir(parents=True, exist_ok=True)
        self._vad = PassthroughVAD()

        self._wx_model = None
        self.sessions: Dict[str, ASRSession] = {}
        self._auto_flush = True
        self._configure_ffmpeg_path()

    def _resolve_compute_type(self) -> str:
        env = (os.environ.get("ASR_CPU_COMPUTE_TYPE") or "").strip().lower()
        if env and env != "int8":
            print(f"[ASR-CPU] Unsupported ASR_CPU_COMPUTE_TYPE={env}; forcing int8")
        return "int8"

    @staticmethod
    def _load_config() -> Dict[str, Any]:
        try:
            cfg_path = Path(__file__).resolve().parents[1] / "config" / "config.json"
            if cfg_path.exists():
                with cfg_path.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
        except Exception:
            pass
        return {}

    def _configure_ffmpeg_path(self) -> None:
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
        if self._wx_model is None:
            try:
                import whisperx  # type: ignore
            except Exception as e:
                raise RuntimeError(f"whisperx is not installed: {e}")

            model_id = self.model_size_or_path
            print(f"[ASR-CPU] Loading WhisperX: {model_id}")
            print(f"[ASR-CPU] Device: {self.device}, Compute type: {self._compute_type}, Language: {self._language}")
            load_kwargs = dict(
                device=self.device,
                compute_type=self._compute_type,
                vad_model=self._vad,
                vad_method="silero",
                vad_options={"chunk_size": 30},
            )
            if isinstance(self.model_dir, str) and self.model_dir.strip() and os.path.isdir(self.model_dir):
                load_kwargs["download_root"] = self.model_dir
            try:
                self._wx_model = whisperx.load_model(model_id, **load_kwargs)
            except TypeError:
                load_kwargs.pop("download_root", None)
                self._wx_model = whisperx.load_model(model_id, **load_kwargs)

    def _flush_cache(self) -> None:
        if not self._auto_flush:
            return
        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def new_session(self, initial_prompt: Optional[str] = None, file_suffix: Optional[str] = None) -> str:
        sid = str(uuid.uuid4())
        self.sessions[sid] = ASRSession(sid, initial_prompt=initial_prompt, save_audio=self._save_audio, file_suffix=file_suffix)
        return sid

    def append_chunk(self, session_id: str, data: bytes) -> int:
        sess = self.sessions.get(session_id)
        if not sess:
            raise KeyError("invalid session")
        return sess.append(data)

    def _retain_audio_copy(self, wav_path: str) -> None:
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            name = f"asr_{ts}_{os.path.basename(wav_path)}"
            dst = self._temp_audio_dir / name
            shutil.copyfile(wav_path, dst)
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
        try:
            result = self._wx_model.transcribe(audio, batch_size=16, language=self._language)
            return result.get("segments", []), result
        finally:
            self._flush_cache()

    def _format_segments(self, segments: List[Dict[str, Any]]) -> List[str]:
        lines: List[str] = []
        for seg in segments:
            txt = str(seg.get("text", "")).strip()
            if not txt:
                continue
            lines.append(txt)
        return lines

    def transcribe(self, session_id: str) -> Tuple[str, float]:
        sess = self.sessions.get(session_id)
        if not sess:
            raise KeyError("invalid session")
        src_path = sess.finalize_to_file()
        wav_path = self._maybe_ffmpeg_convert(src_path)
        print(f"[ASR-CPU] Transcribe (oneshot): file={wav_path}")
        segs, _res = self._transcribe_internal(wav_path)
        if self._save_audio and os.path.exists(wav_path):
            self._retain_audio_copy(wav_path)
        text = "\n".join(self._format_segments(segs)).strip()
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
                "diarization": "disabled",
            }
        except Exception:
            return {"engine": self.ENGINE_NAME}
