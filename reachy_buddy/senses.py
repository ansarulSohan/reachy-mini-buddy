"""Reachy's senses for storytelling: eyes (face detection) and ears (listening + speech-to-text).

Eyes  : OpenCV YuNet face detector on the robot's camera or the computer's webcam.
Ears  : microphone (robot or computer) -> simple voice-activity detection -> Whisper.
        Whisper runs locally (mlx-whisper on Apple Silicon, faster-whisper elsewhere), and
        its language detection is limited to the five story languages.
"""

from __future__ import annotations

import queue
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
CACHE = Path.home() / ".cache" / "reachy_buddy"
SR = 16000  # speech-to-text sample rate


# ====================================================================== eyes
@dataclass
class Face:
    x: float  # -1 (image left) .. +1 (image right), from the image centre
    y: float  # -1 (top) .. +1 (bottom)
    size: float  # face width / image width
    t: float  # time.monotonic() of the frame


class Eyes:
    """Background face detector. `source` is "robot" (head camera) or "computer" (webcam)."""

    def __init__(self, source: str, mini=None, webcam_index: int = 0, hz: float = 10.0) -> None:
        import cv2

        self.cv2, self.source, self.mini, self.hz = cv2, source, mini, hz
        self.face: Optional[Face] = None
        self.frames = 0
        self.error: Optional[str] = None
        self._cap = None
        if source == "computer":
            self._cap = cv2.VideoCapture(webcam_index)
            if not self._cap.isOpened():
                raise RuntimeError(f"cannot open webcam {webcam_index} (camera permission for the terminal?)")
        self._det = cv2.FaceDetectorYN.create(str(_yunet_model()), "", (320, 240), 0.75, 0.3, 50)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="eyes", daemon=True)
        self._thread.start()

    def _read(self) -> Optional[np.ndarray]:
        if self._cap is not None:
            ok, frame = self._cap.read()
            return frame if ok else None
        return self.mini.media.get_frame()

    def _loop(self) -> None:
        cv2 = self.cv2
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                frame = self._read()
                if frame is not None:
                    self.frames += 1
                    h, w = frame.shape[:2]
                    s = 320 / w
                    small = cv2.resize(frame, (320, int(h * s)))
                    self._det.setInputSize((small.shape[1], small.shape[0]))
                    _, faces = self._det.detect(small)
                    if faces is not None and len(faces):
                        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4]
                        sw, sh = small.shape[1], small.shape[0]
                        cx, cy = (fx + fw / 2) / sw, (fy + fh / 2) / sh
                        self.face = Face(2 * cx - 1, 2 * cy - 1, fw / sw, t0)
            except Exception as e:
                self.error = str(e)
            time.sleep(max(0.0, 1 / self.hz - (time.monotonic() - t0)))

    # ------------------------------------------------------------------ queries
    def current(self, max_age: float = 0.6) -> Optional[Face]:
        f = self.face
        return f if f and time.monotonic() - f.t < max_age else None

    def seconds_without_face(self) -> float:
        f = self.face
        return time.monotonic() - f.t if f else float("inf")

    def wait_for_face(self, hold: float = 1.0, timeout: Optional[float] = None,
                      stop: Optional[Callable[[], bool]] = None) -> bool:
        """Block until a face has been seen continuously for `hold` seconds."""
        t_end = None if timeout is None else time.monotonic() + timeout
        since = None
        while t_end is None or time.monotonic() < t_end:
            if stop and stop():
                return False
            if self.current(0.4):
                since = since or time.monotonic()
                if time.monotonic() - since >= hold:
                    return True
            else:
                since = None
            time.sleep(0.05)
        return False

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        if self._cap is not None:
            self._cap.release()


def _yunet_model() -> Path:
    path = CACHE / "face_detection_yunet_2023mar.onnx"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        print("[eyes] downloading the YuNet face detector (230 kB)...")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(YUNET_URL, tmp)
        tmp.rename(path)
    return path


# ====================================================================== ears
class ComputerMic:
    def __init__(self) -> None:
        import sounddevice as sd

        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=SR // 20,
                                     callback=lambda d, *_: self.q.put(d[:, 0].copy()))
        self.stream.start()

    def flush(self) -> None:
        while not self.q.empty():
            self.q.get_nowait()

    def read(self, timeout: float = 0.5) -> Optional[np.ndarray]:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()


class RobotMic:
    """Reachy Mini's microphone array, through the SDK."""

    def __init__(self, mini) -> None:
        self.media = mini.media
        self.sr = self.media.get_input_audio_samplerate()
        self.media.start_recording()

    def flush(self) -> None:
        for _ in range(200):
            if self.media.get_audio_sample() is None:
                break

    def read(self, timeout: float = 0.5) -> Optional[np.ndarray]:
        t_end = time.monotonic() + timeout
        while time.monotonic() < t_end:
            s = self.media.get_audio_sample()
            if s is not None and len(s):
                s = s.mean(axis=1) if s.ndim == 2 else s
                if self.sr and self.sr != SR:  # simple resample
                    n = int(len(s) * SR / self.sr)
                    s = np.interp(np.linspace(0, len(s) - 1, n), np.arange(len(s)), s)
                return s.astype(np.float32)
            time.sleep(0.01)
        return None

    def close(self) -> None:
        try:
            self.media.stop_recording()
        except Exception:
            pass


class Transcriber:
    """Local Whisper, limited to the five story languages."""

    ALLOWED = {"en": "en", "no": "no", "nn": "no", "bn": "bn", "hi": "hi", "ur": "ur"}

    def __init__(self, model: Optional[str] = None) -> None:
        self.backend = None
        self.model_name = model
        try:
            import mlx_whisper  # noqa: F401  (Apple Silicon)

            self.backend = "mlx"
            self.model_name = model or "mlx-community/whisper-large-v3-turbo"
        except ImportError:
            self.backend = "faster"
            self.model_name = model or "small"
        self._model = None
        # MLX arrays live on the thread that created them, so one worker thread
        # loads the model and runs every transcription.
        from concurrent.futures import ThreadPoolExecutor

        self._worker = ThreadPoolExecutor(1, thread_name_prefix="stt")
        self._worker.submit(self._load)

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            if self.backend == "mlx":
                import mlx.core as mx
                from mlx_whisper.transcribe import ModelHolder

                self._model = ModelHolder.get_model(self.model_name, mx.float16)
            else:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self.model_name, device="auto", compute_type="int8")
        except Exception as e:
            print(f"[stt] could not load {self.model_name}: {e}")

    def transcribe(self, audio: np.ndarray, lang: Optional[str] = None) -> tuple[str, Optional[str]]:
        """Return (text, language code) for 16 kHz mono float32 audio."""
        return self._worker.submit(self._transcribe, audio, lang).result()

    def _transcribe(self, audio: np.ndarray, lang: Optional[str]) -> tuple[str, Optional[str]]:
        self._load()
        if self._model is None:
            raise RuntimeError("speech-to-text model failed to load")
        if self.backend == "mlx":
            import mlx.core as mx
            import mlx_whisper
            from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim

            if lang is None:
                mel = log_mel_spectrogram(audio, n_mels=self._model.dims.n_mels, padding=N_SAMPLES)
                _, probs = self._model.detect_language(pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16))
                lang = self._best(probs)
            r = mlx_whisper.transcribe(audio, path_or_hf_repo=self.model_name, language=lang,
                                       condition_on_previous_text=False)
            return r["text"].strip(), lang
        if lang is None:
            _, _, all_probs = self._model.detect_language(audio)
            lang = self._best(dict(all_probs))
        segments, _ = self._model.transcribe(audio, language=lang, beam_size=5)
        return " ".join(s.text for s in segments).strip(), lang

    def _best(self, probs: dict[str, float]) -> str:
        score: dict[str, float] = {}
        for code, ours in self.ALLOWED.items():
            score[ours] = score.get(ours, 0.0) + probs.get(code, 0.0)
        return max(score, key=score.get)


class Ears:
    """Listen for one utterance (energy-based voice activity detection), then transcribe it."""

    def __init__(self, mic, transcriber: Transcriber) -> None:
        self.mic, self.stt = mic, transcriber

    def listen(self, wait: float = 8.0, max_len: float = 15.0, silence: float = 1.2,
               on_speech: Optional[Callable[[], None]] = None) -> Optional[np.ndarray]:
        """Return the audio of one utterance, or None if nobody spoke within `wait` seconds."""
        self.mic.flush()
        noise = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.3:  # measure the room's noise floor
            c = self.mic.read()
            if c is not None:
                noise.append(float(np.sqrt(np.mean(c ** 2))))
        floor = float(np.median(noise)) if noise else 0.003
        thresh = max(0.012, floor * 3.0)

        pre: list[np.ndarray] = []  # keep ~0.3 s before the onset
        speech: list[np.ndarray] = []
        started, quiet, t_start = False, 0.0, time.monotonic()
        while True:
            c = self.mic.read()
            now = time.monotonic()
            if c is None:
                if not started and now - t_start > wait:
                    return None
                continue
            loud = float(np.sqrt(np.mean(c ** 2))) > thresh
            dt = len(c) / SR
            if not started:
                pre = (pre + [c])[-6:]
                if loud:
                    started, speech = True, list(pre)
                    if on_speech:
                        on_speech()
                elif now - t_start > wait:
                    return None
                continue
            speech.append(c)
            quiet = 0.0 if loud else quiet + dt
            total = sum(len(s) for s in speech) / SR
            if quiet >= silence or total >= max_len:
                audio = np.concatenate(speech)
                return audio if total - quiet > 0.25 else None

    def hear(self, **kw) -> tuple[str, Optional[str]]:
        audio = self.listen(**kw)
        if audio is None:
            return "", None
        return self.stt.transcribe(audio)
