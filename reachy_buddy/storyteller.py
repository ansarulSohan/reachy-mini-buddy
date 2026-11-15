"""Story mode: Reachy Mini tells children's stories in English, Norsk, Bangla, Hindi and Urdu.

Flow
  1. Waits, asleep, until it sees a face.
  2. Wakes up, greets the child and asks which story they want.
  3. Listens, works out the language (Whisper) and what the story should be about.
  4. Claude writes the story (1-10 minutes) while Reachy is already telling it, paragraph
     by paragraph, with a neural voice in that language.
  5. While telling, Reachy keeps looking at the child's face, bobs with its words and acts
     out each paragraph's mood. If the child walks away, it pauses and waits.
  6. Asks if the child wants another story; otherwise says goodbye and goes back to sleep.

Pipeline (three threads, so there is no silence between paragraphs):
  writer (Claude stream) -> paragraphs -> TTS -> audio clips -> main thread (speak + move)
"""

from __future__ import annotations

import math
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

from rich.console import Console

from .gestures import REST, Body, antennas, head
from .languages import LANGS, NAMES, is_no, lang_named_in, phrase
from .speech import TTS, Clip
from .story_writer import MAX_MINUTES, Meta, Paragraph, StoryWriter

console = Console()

# Pose offsets per mood while speaking: head mm/deg, antennas (left, right) deg,
# bob = how much the head bounces with the voice, flap = antenna wiggle with the voice.
MOOD_POSE = {
    "calm":      dict(z=0, pitch=0, roll=0, x=0, al=REST, ar=REST, bob=1.0, flap=10),
    "happy":     dict(z=4, pitch=-6, roll=0, x=0, al=0, ar=0, bob=1.6, flap=25),
    "excited":   dict(z=7, pitch=-10, roll=0, x=3, al=-10, ar=-10, bob=2.2, flap=35),
    "surprised": dict(z=9, pitch=-12, roll=0, x=-5, al=-5, ar=-5, bob=1.2, flap=8),
    "sad":       dict(z=-7, pitch=14, roll=5, x=0, al=95, ar=95, bob=0.5, flap=4),
    "worried":   dict(z=-3, pitch=6, roll=-7, x=-3, al=55, ar=55, bob=0.7, flap=6),
    "curious":   dict(z=2, pitch=-3, roll=-14, x=6, al=0, ar=40, bob=1.0, flap=12),
    "funny":     dict(z=3, pitch=-5, roll=10, x=0, al=40, ar=-10, bob=1.8, flap=30),
    "sleepy":    dict(z=-5, pitch=10, roll=4, x=0, al=70, ar=70, bob=0.4, flap=3),
    "listening": dict(z=3, pitch=-4, roll=-8, x=8, al=0, ar=0, bob=0.0, flap=0),
    "thinking":  dict(z=0, pitch=-14, roll=-8, x=0, al=REST, ar=REST, bob=0.0, flap=0, yaw=18),
}
# Short gestures acted out after some paragraphs (each at most once every few paragraphs).
MOOD_GESTURE = {"funny": "laugh", "excited": "happy", "surprised": "surprised"}


# ====================================================================== motion
class Performer:
    """Owns the motors while Reachy talks or listens: voice bob, mood pose, face tracking."""

    MAX_YAW, MAX_PITCH = 40.0, 15.0

    def __init__(self, body: Body, eyes=None) -> None:
        self.body, self.eyes = body, eyes
        self.mood = "calm"
        self.clip: Optional[Clip] = None
        self.clip_t0 = 0.0
        self._want = threading.Event()
        self._holding = threading.Event()
        self._quit = threading.Event()
        self._cut = threading.Event()
        self._state = self._neutral()
        self._track = [0.0, 0.0]  # yaw, pitch (deg) toward the face
        self._track_target = [0.0, 0.0]
        self._last_face_t = 0.0
        threading.Thread(target=self._run, name="performer", daemon=True).start()

    @staticmethod
    def _neutral() -> dict:
        return dict(z=0.0, pitch=0.0, roll=0.0, x=0.0, al=REST, ar=REST, bob=0.0, flap=0.0, yaw=0.0)

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        if not self._want.is_set():
            self._want.set()
            self._holding.wait(timeout=10)

    def stop(self) -> None:
        """Release the motors (e.g. to play a gesture). Leaves the head where it is."""
        self._want.clear()
        t_end = time.monotonic() + 2
        while self._holding.is_set() and time.monotonic() < t_end:
            time.sleep(0.01)

    def close(self) -> None:
        self.stop()
        self._quit.set()

    def speak(self, clip: Clip, speaker) -> None:
        """Play a clip and move with it (blocking; `cut()` ends it early)."""
        self.start()
        self._cut.clear()
        self.clip, self.clip_t0 = clip, time.monotonic()
        speaker.start(clip)
        while time.monotonic() - self.clip_t0 < clip.duration + 0.05:
            if self._cut.wait(0.02):
                speaker.stop()
                break
        self.clip = None

    def cut(self) -> None:
        self._cut.set()

    # ------------------------------------------------------------------ loop
    def _run(self) -> None:
        while not self._quit.is_set():
            if not self._want.is_set():
                time.sleep(0.02)
                continue
            with self.body._busy("storytelling"):  # waits for any running gesture
                self._holding.set()
                self._state = self._neutral()  # every gesture ends in neutral
                last = time.monotonic()
                while self._want.is_set() and not self._quit.is_set():
                    now = time.monotonic()
                    self._step(now, now - last)
                    last = now
                    time.sleep(0.02)
                self._holding.clear()

    def _update_tracking(self, now: float, dt: float) -> None:
        face = self.eyes.current() if self.eyes else None
        if face and face.t != self._last_face_t:
            self._last_face_t = face.t
            dyaw, dpitch = -face.x * 35.0, face.y * 22.0  # ~70 x 45 deg field of view
            if self.eyes.source == "robot":  # camera moves with the head: correct the error
                self._track_target = [self._track[0] + 0.6 * dyaw, self._track[1] + 0.6 * dpitch]
            else:  # fixed webcam next to the robot: point at the face directly
                self._track_target = [dyaw, dpitch]
        elif self.eyes and self.eyes.seconds_without_face() > 3.0:
            self._track_target = [0.0, 0.0]  # nobody there: look straight ahead
        self._track_target[0] = max(-self.MAX_YAW, min(self.MAX_YAW, self._track_target[0]))
        self._track_target[1] = max(-self.MAX_PITCH, min(self.MAX_PITCH, self._track_target[1]))
        a = 1 - math.exp(-dt / 0.45)
        for i in range(2):
            self._track[i] += a * (self._track_target[i] - self._track[i])

    def _step(self, now: float, dt: float) -> None:
        self._update_tracking(now, dt)
        target = dict(self._neutral(), **MOOD_POSE.get(self.mood, MOOD_POSE["calm"]))
        a = 1 - math.exp(-dt / 0.35)  # glide between moods
        for k, v in target.items():
            self._state[k] += a * (v - self._state[k])
        s = self._state

        lvl = self.clip.level(now - self.clip_t0) if self.clip else 0.0
        t = now
        yaw_face, pitch_face = self._track
        sway = 3.0 * math.sin(2 * math.pi * 0.23 * t)  # slow storyteller sway
        pose = head(
            x=s["x"],
            z=s["z"] + 2.0 * math.sin(2 * math.pi * 0.2 * t) + s["bob"] * 3.0 * lvl,
            roll=s["roll"] + sway,
            pitch=s["pitch"] + pitch_face - s["bob"] * 3.0 * lvl * math.sin(2 * math.pi * 1.6 * t),
            yaw=s["yaw"] + yaw_face + 3.0 * math.sin(2 * math.pi * 0.09 * t),
        )
        wig = s["flap"] * lvl * math.sin(2 * math.pi * 3.0 * t)
        try:
            self.body.mini.set_target(head=pose, antennas=antennas(s["al"] + wig, s["ar"] - wig),
                                      body_yaw=math.radians(0.5 * yaw_face))
        except Exception:
            pass  # a dropped frame is harmless


# ====================================================================== session
@dataclass
class StoryOptions:
    lang: str = "en"  # greeting / default language
    force_lang: Optional[str] = None
    minutes: Optional[int] = None
    age: Optional[str] = None
    request: Optional[str] = None  # skip listening: tell this story
    once: bool = False
    presence: bool = True  # pause when the child walks away
    typed: bool = False  # type requests instead of speaking


class StoryTeller:
    def __init__(self, body: Body, speaker, tts: TTS, writer: StoryWriter,
                 eyes=None, ears=None, opts: StoryOptions = StoryOptions(),
                 performer: Optional[Performer] = None,
                 on_event: Optional[Callable[..., None]] = None) -> None:
        self.body, self.speaker, self.tts, self.writer = body, speaker, tts, writer
        self.eyes, self.ears, self.opts = eyes, ears, opts
        self.performer = performer or Performer(body, eyes)
        self.lang = opts.force_lang or opts.lang
        self.on_event = on_event or (lambda kind, **data: None)
        self.stop_event = threading.Event()

    def stop(self) -> None:
        """Stop the story that is being told (from another thread)."""
        self.stop_event.set()
        self.performer.cut()

    # ------------------------------------------------------------------ helpers
    def say(self, text: str, lang: Optional[str] = None, mood: str = "happy") -> None:
        lang = lang or self.lang
        console.print(f"[bold magenta]Reachy[/] [dim]({lang})[/] {text}", highlight=False)
        self.on_event("say", text=text, lang=lang, mood=mood)
        clip = self.tts.synth(text, lang)
        if clip:
            self.performer.mood = mood
            self.performer.speak(clip, self.speaker)

    def gesture(self, name: str) -> None:
        self.performer.stop()
        try:
            self.body.perform(name)
        except Exception as e:
            console.print(f"[dim]gesture {name} failed: {e}[/]")

    def hear(self) -> tuple[str, Optional[str]]:
        """One request from the child: (text, language code or None)."""
        if self.opts.typed or self.ears is None:
            self.performer.stop()
            self.body.idle(True)
            try:
                text = console.input("[bold cyan]Child (type) [/]").strip()
            except EOFError:
                text = ""
            return text, None
        self.performer.start()
        self.performer.mood = "listening"
        console.print("[cyan]  listening...[/]")
        text, lang = self.ears.hear(wait=9.0, on_speech=lambda: console.print("[cyan]  (hearing you)[/]"))
        self.performer.mood = "calm"
        if text:
            console.print(f"[bold cyan]Child[/] [dim]({lang})[/] {text}", highlight=False)
        return text, lang

    def wait_for_child(self) -> None:
        if self.eyes is None:
            if not self.opts.request:
                console.input("[dim]No camera. Press Enter to wake Reachy up...[/]")
            return
        console.print("[dim]Reachy is asleep. Sit in front of the camera to wake it up.[/]")
        t0 = time.monotonic()
        while not self.eyes.wait_for_face(hold=1.0, timeout=1.0):
            if self.eyes.frames == 0 and time.monotonic() - t0 > 6:
                console.print(f"[yellow]The {self.eyes.source} camera sends no images"
                              f"{': ' + self.eyes.error if self.eyes.error else ''}. "
                              "Face detection is off.[/]")
                self.eyes.close()
                self.eyes = self.performer.eyes = None
                return self.wait_for_child()

    def wait_until_gone(self, seconds: float = 4.0) -> None:
        while self.eyes and self.eyes.seconds_without_face() < seconds:
            time.sleep(0.2)

    # ------------------------------------------------------------------ main loop
    def run(self) -> None:
        body = self.body
        if not body.asleep:
            body.rest_pose()
        while True:
            self.wait_for_child()
            body.wake_up()
            self.gesture("hello")
            self.say(phrase("greet", self.lang))
            request, heard = self.opts.request, None
            if request is None:
                self.say(phrase("ask", self.lang), mood="curious")
                request, heard = self.ask_request()
            keep_going = True
            while keep_going:
                self.tell(request or "", heard)
                if self.opts.once:
                    keep_going = False
                    break
                self.gesture("happy")
                self.say(phrase("another", self.lang), mood="curious")
                request, heard = self.hear()
                if not request or is_no(request):
                    keep_going = False
            self.say(phrase("bye", self.lang))
            self.performer.stop()
            body.sleep()
            if self.opts.once:
                return
            self.opts.request = None
            self.wait_until_gone()

    def ask_request(self) -> tuple[str, Optional[str]]:
        for attempt in range(2):
            text, lang = self.hear()
            if text:
                return text, lang
            if attempt == 0:
                self.say(phrase("didnt_hear", self.lang), mood="curious")
        self.say(phrase("i_pick", self.lang))
        return "", None

    # ------------------------------------------------------------------ one story
    def tell(self, request: str, heard: Optional[str]) -> None:
        opts = self.opts
        self.stop_event.clear()
        ack_lang = opts.force_lang or lang_named_in(request) or heard or self.lang
        paragraphs: queue.Queue = queue.Queue()
        clips: queue.Queue = queue.Queue(maxsize=4)
        pool = ThreadPoolExecutor(3, thread_name_prefix="tts")  # network TTS: synthesise ahead in parallel
        done = threading.Event()

        def write() -> None:
            try:
                for ev in self.writer.write(request, heard, self.lang, opts.minutes,
                                            opts.force_lang, opts.age):
                    if done.is_set():
                        break
                    paragraphs.put(ev)
            finally:
                paragraphs.put(None)

        def put(item) -> None:
            while not done.is_set():
                try:
                    clips.put(item, timeout=0.5)
                    return
                except queue.Full:
                    pass

        def speak_prep() -> None:  # TTS runs ahead of playback
            lang = ack_lang
            while True:
                ev = paragraphs.get()
                if ev is None or done.is_set():
                    put(None)
                    return
                if isinstance(ev, Meta):
                    lang = ev.lang
                    put(ev)
                    if ev.title:
                        title = phrase("title", lang, title=ev.title)
                        put((Paragraph("happy", title), pool.submit(self.tts.synth, title, lang)))
                    continue
                put((ev, pool.submit(self.tts.synth, ev.text, lang)))

        threading.Thread(target=write, name="writer", daemon=True).start()
        threading.Thread(target=speak_prep, name="tts", daemon=True).start()

        # Talk while Claude starts writing.
        self.say(phrase("thinking", ack_lang), ack_lang, mood="thinking")
        self.performer.mood = "thinking"

        meta: Optional[Meta] = None
        spoken = 0.0
        last_gesture = -3
        ended_naturally = False
        n = 0
        stopped = False
        try:
            while True:
                try:
                    item = clips.get(timeout=0.2)
                except queue.Empty:
                    if self.stop_event.is_set():
                        stopped = True
                        break
                    continue
                if self.stop_event.is_set():
                    stopped = True
                    break
                if item is None:
                    ended_naturally = True
                    break
                if isinstance(item, Meta):
                    meta = item
                    self.lang = item.lang
                    src = "Claude" if item.source == "claude" else "built-in story"
                    console.rule(f"[bold]{item.title or 'Story'}[/]  [dim]{NAMES[item.lang]} · "
                                 f"{item.minutes} min · {src}[/]")
                    if self.writer.error:
                        console.print(f"[yellow]{self.writer.error}[/]")
                    self.on_event("meta", title=item.title, lang=item.lang, language=NAMES[item.lang],
                                  minutes=item.minutes, source=item.source, error=self.writer.error)
                    continue
                para, fut = item
                clip = fut.result()
                if clip is None:
                    continue
                m = meta.minutes if meta else 3  # allow some overrun, never past 10 minutes
                cap = min(MAX_MINUTES * 60, max(m * 60 * 1.4, m * 60 + 60))
                if spoken + clip.duration > cap and n > 0:
                    break  # too long: wrap up below
                if opts.presence:
                    self.check_presence()
                console.print(f"[dim]{para.mood:>9}[/] {para.text}", highlight=False)
                self.on_event("para", index=n, mood=para.mood, text=para.text,
                              duration=clip.duration, spoken=spoken)
                self.performer.mood = para.mood
                self.performer.speak(clip, self.speaker)
                spoken += clip.duration
                n += 1
                if self.stop_event.is_set():
                    stopped = True
                    break
                g = MOOD_GESTURE.get(para.mood)
                if g and n - last_gesture >= 3:
                    self.gesture(g)
                    last_gesture = n
                else:
                    time.sleep(0.25)  # storyteller's breath between paragraphs
        finally:
            done.set()
            pool.shutdown(wait=False, cancel_futures=True)
            while not clips.empty():  # unblock the TTS thread
                try:
                    clips.get_nowait()
                except queue.Empty:
                    break
        if not stopped and (not ended_naturally
                            or (self.writer.error and n and meta and meta.source == "claude")):
            self.say(phrase("time_up", self.lang), mood="happy")
        console.print(f"[dim]Story told in {spoken / 60:.1f} min ({n} paragraphs).[/]")
        self.on_event("story_end", spoken=spoken, paragraphs=n, stopped=stopped)
        self.performer.mood = "calm"

    def check_presence(self, away: float = 8.0, patience: float = 25.0) -> None:
        """If the child has gone, ask where they went and wait a little."""
        gone = self.eyes.seconds_without_face() if self.eyes else 0.0
        if gone < away or gone == float("inf"):  # here, or the camera never saw anyone
            return
        self.say(phrase("where", self.lang), mood="worried")
        self.gesture("look_around")
        self.performer.start()
        self.performer.mood = "curious"
        if self.eyes.wait_for_face(hold=0.8, timeout=patience, stop=self.stop_event.is_set):
            self.performer.stop()
            self.gesture("happy")
            self.say(phrase("welcome_back", self.lang))


def resolve_lang(code: Optional[str]) -> Optional[str]:
    if code is None:
        return None
    c = code.lower().strip()
    aliases = {"nb": "no", "nn": "no", "norsk": "no", "norwegian": "no", "bangla": "bn",
               "bengali": "bn", "hindi": "hi", "urdu": "ur", "english": "en"}
    c = aliases.get(c, c)
    if c not in LANGS:
        raise SystemExit(f"Unknown language {code!r}. Use one of: {', '.join(LANGS)}")
    return c
