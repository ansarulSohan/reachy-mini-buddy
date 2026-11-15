"""Reachy's voice: multilingual text-to-speech and audio playback.

TTS: Microsoft neural voices through `edge-tts` (needs internet), falling back to
macOS `say` (or `espeak-ng` on Linux). Playback goes to the robot's speaker or the
computer's speakers. Every clip carries a loudness envelope so the head and
antennas can move in time with the words.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .languages import SAY_VOICES, VOICES

ENV_HZ = 50  # envelope samples per second


@dataclass
class Clip:
    text: str
    pcm: np.ndarray  # float32 mono
    sr: int
    env: np.ndarray  # 0..1 loudness, ENV_HZ per second

    @property
    def duration(self) -> float:
        return len(self.pcm) / self.sr

    def level(self, t: float) -> float:
        i = int(t * ENV_HZ)
        return float(self.env[i]) if 0 <= i < len(self.env) else 0.0


def _decode(path: str, sr: int) -> np.ndarray:
    out = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def _envelope(pcm: np.ndarray, sr: int) -> np.ndarray:
    hop = sr // ENV_HZ
    n = len(pcm) // hop
    if n == 0:
        return np.zeros(1, np.float32)
    rms = np.sqrt(np.mean(pcm[: n * hop].reshape(n, hop) ** 2, axis=1))
    ref = np.percentile(rms, 95) or 1.0
    env = np.clip(rms / ref, 0, 1)
    k = np.ones(4) / 4  # light smoothing
    return np.convolve(env, k, mode="same").astype(np.float32)


class TTS:
    """Text -> Clip in any of the five languages."""

    def __init__(self, sr: int, rate: str = "-10%") -> None:
        if not shutil.which("ffmpeg"):
            raise SystemExit("Storytelling needs ffmpeg (macOS: `brew install ffmpeg`).")
        self.sr, self.rate = sr, rate
        self.tmp = Path(tempfile.mkdtemp(prefix="reachy_tts_"))
        self._n = 0
        self._lock = threading.Lock()
        self.warned = False

    def _path(self, ext: str) -> str:
        with self._lock:
            self._n += 1
            return str(self.tmp / f"{self._n}.{ext}")

    def synth(self, text: str, lang: str) -> Optional[Clip]:
        """Never raises: returns None if no voice could say the text."""
        try:
            return self._synth(text, lang)
        except Exception as e:
            print(f"[tts] could not synthesise speech: {e}")
            return None

    def _synth(self, text: str, lang: str) -> Optional[Clip]:
        path = None
        try:
            path = self._edge(text, lang)
        except Exception as e:
            if not self.warned:
                print(f"[tts] edge-tts failed ({e}); falling back to the system voice.")
                self.warned = True
            path = self._system(text, lang)
        if not path:
            return None
        pcm = _decode(path, self.sr)
        Path(path).unlink(missing_ok=True)
        return Clip(text, pcm, self.sr, _envelope(pcm, self.sr))

    def _edge(self, text: str, lang: str) -> str:
        import edge_tts

        path = self._path("mp3")

        async def run():
            await edge_tts.Communicate(text, VOICES[lang], rate=self.rate).save(path)

        for attempt in range(3):  # the service sometimes drops a request
            try:
                asyncio.run(run())
                if Path(path).stat().st_size > 0:
                    return path
            except Exception:
                if attempt == 2:
                    raise
            time.sleep(0.4)
        raise RuntimeError("empty audio")

    def _system(self, text: str, lang: str) -> Optional[str]:
        if shutil.which("say") and SAY_VOICES.get(lang):
            path = self._path("aiff")
            subprocess.run(["say", "-v", SAY_VOICES[lang], "-r", "160", "-o", path, text], check=True)
            return path
        if shutil.which("espeak-ng"):
            path = self._path("wav")
            code = {"no": "nb"}.get(lang, lang)
            subprocess.run(["espeak-ng", "-v", code, "-s", "140", "-w", path, text], check=True)
            return path
        return None


# ---------------------------------------------------------------------- speakers
class ComputerSpeaker:
    sr = 24000

    def __init__(self) -> None:
        import sounddevice as sd

        self.sd = sd

    def start(self, clip: Clip) -> None:
        self.sd.play(clip.pcm, clip.sr)

    def stop(self) -> None:
        self.sd.stop()

    def close(self) -> None:
        self.stop()


class RobotSpeaker:
    """Streams audio to Reachy Mini's own speaker through the SDK."""

    def __init__(self, mini) -> None:
        self.media = mini.media
        sr = self.media.get_output_audio_samplerate()
        self.sr = sr if sr and sr > 0 else 16000
        self.media.start_playing()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, clip: Clip) -> None:
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._feed, args=(clip,), daemon=True)
        self._thread.start()

    def _feed(self, clip: Clip) -> None:
        chunk, lead = int(0.1 * clip.sr), 0.3  # push 100 ms at a time, ~300 ms ahead
        t0, pushed = time.monotonic(), 0
        while pushed < len(clip.pcm) and not self._stop.is_set():
            if pushed / clip.sr - (time.monotonic() - t0) < lead:
                self.media.push_audio_sample(clip.pcm[pushed: pushed + chunk])
                pushed += chunk
            else:
                time.sleep(0.02)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None
        try:
            self.media.audio.clear_player()
        except Exception:
            pass

    def close(self) -> None:
        self.stop()
        try:
            self.media.stop_playing()
        except Exception:
            pass


class BrowserSpeaker:
    """Sends Reachy's voice to the web page, which plays it (for servers without speakers)."""

    sr = 24000

    def __init__(self, keep: int = 30) -> None:
        self.events = None  # set by the web app: an EventLog with .add(kind, **data)
        self.clips: dict[int, bytes] = {}
        self.keep, self.n = keep, 0
        self.lock = threading.Lock()

    def start(self, clip: Clip) -> None:
        mp3 = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "f32le", "-ar", str(clip.sr), "-ac", "1", "-i", "pipe:0",
             "-b:a", "64k", "-f", "mp3", "pipe:1"],
            input=clip.pcm.astype(np.float32).tobytes(), capture_output=True, check=True).stdout
        with self.lock:
            self.n += 1
            self.clips[self.n] = mp3
            for old in [k for k in self.clips if k <= self.n - self.keep]:
                del self.clips[old]
            n = self.n
        if self.events:
            self.events.add("audio", id=n, duration=clip.duration)

    def get(self, n: int) -> Optional[bytes]:
        with self.lock:
            return self.clips.get(n)

    def stop(self) -> None:
        if self.events:
            self.events.add("audio_stop")

    def close(self) -> None:
        pass
