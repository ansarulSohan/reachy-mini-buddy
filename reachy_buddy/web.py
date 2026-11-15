"""Web dashboards for Reachy Mini: Live companion, Story teller, Mood reader, Finger counter.

    python -m reachy_buddy web          # then open http://127.0.0.1:8080

The browser is Reachy's eyes and ears: it runs MediaPipe face and hand tracking on the
webcam (or on Reachy's own camera) and listens to the microphone. This server is the
brain and the body: it decides how Reachy reacts, makes it talk and move, and streams
a live 3D view of the robot back to the page.

Live behaviour (the Companion):
  * "Hi Reachy"             -> wakes up (from sleep)
  * show 1-10 fingers       -> says how many
  * "tell me a story ..."   -> tells a story (Claude writes it)
  * your expression changes -> asks about it ("You look sad. What happened?")
  * you leave the camera    -> "Where did you go?"; after a minute alone it naps
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, Request
import base64
import os

from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .gestures import REST, Body, antennas, head
from .languages import INTENTS, LANGS, NAMES, fingers_phrase, intent_of, phrase
from .senses import SR, Face, Transcriber
from .speech import TTS, BrowserSpeaker, ComputerSpeaker, RobotSpeaker
from .story_writer import StoryWriter
from .storyteller import Performer, StoryOptions, StoryTeller, console, resolve_lang

HERE = Path(__file__).parent / "web"
MOODS = ("happy", "sad", "surprised", "angry", "confused")
MOOD_ACT = {  # robot gesture, speaking mood
    "happy": ("happy", "happy"),
    "sad": ("sad", "sad"),
    "surprised": ("surprised", "surprised"),
    "angry": ("shake_no", "worried"),
    "confused": ("confused", "curious"),
}
# Whisper sometimes "hears" these in silence or noise.
HALLUCINATIONS = re.compile(r"^(thank you\.?|thanks for watching.*|you|\.+|subtitles.*|please subscribe.*)$",
                            re.IGNORECASE)
VIEW_CAMERA = dict(lookat=(0.0, 0.0, 0.21), distance=0.8, azimuth=160.0, elevation=-10.0)


class RemoteEyes:
    """Face positions sent by the browser; same interface as senses.Eyes."""

    source = "computer"

    def __init__(self) -> None:
        self.face: Optional[Face] = None
        self.frames = 0
        self.error: Optional[str] = None

    def update(self, x: Optional[float], y: Optional[float], size: Optional[float]) -> None:
        self.frames += 1
        if x is not None and y is not None:
            self.face = Face(float(x), float(y), float(size or 0), time.monotonic())

    def current(self, max_age: float = 0.6) -> Optional[Face]:
        f = self.face
        return f if f and time.monotonic() - f.t < max_age else None

    def seconds_without_face(self) -> float:
        f = self.face
        return time.monotonic() - f.t if f else float("inf")

    def wait_for_face(self, hold: float = 1.0, timeout: Optional[float] = None, stop=None) -> bool:
        t_end = None if timeout is None else time.monotonic() + timeout
        since = None
        while t_end is None or time.monotonic() < t_end:
            if stop and stop():
                return False
            if self.current(0.5):
                since = since or time.monotonic()
                if time.monotonic() - since >= hold:
                    return True
            else:
                since = None
            time.sleep(0.05)
        return False

    def close(self) -> None:
        pass


class EventLog:
    def __init__(self, size: int = 500) -> None:
        self.items: deque[dict] = deque(maxlen=size)
        self.n = 0
        self.lock = threading.Lock()

    def add(self, kind: str, **data) -> None:
        with self.lock:
            self.n += 1
            self.items.append({"id": self.n, "kind": kind, "t": time.time(), **data})

    def since(self, after: int) -> list[dict]:
        with self.lock:
            return [e for e in self.items if e["id"] > after]


# ====================================================================== body
class Robot:
    """Runs one thing at a time: a story, or a short reaction."""

    def __init__(self, mini, body: Body, speaker, writer: StoryWriter, stt: Transcriber,
                 simulated: bool) -> None:
        self.mini, self.body, self.speaker, self.writer, self.stt = mini, body, speaker, writer, stt
        self.simulated = simulated
        self.eyes = RemoteEyes()
        self.events = EventLog()
        self.tts = TTS(speaker.sr)
        self.performer = Performer(body, self.eyes)
        self.teller = StoryTeller(body, speaker, self.tts, writer, self.eyes, None,
                                  StoryOptions(once=True, presence=True),
                                  performer=self.performer, on_event=self.events.add)
        self.busy: Optional[str] = None  # "story" | "reaction" | None
        self.lock = threading.Lock()
        self.last_reaction = 0.0

    @property
    def asleep(self) -> bool:
        return self.body.asleep

    def run(self, kind: str, fn, wake: bool = True) -> bool:
        """Start a job in the background; False if Reachy is already busy."""
        with self.lock:
            if self.busy:
                return False
            self.busy = kind
        self.events.add("busy", busy=kind)

        def job():
            try:
                if wake and self.body.asleep:
                    self.wake_up()
                fn()
            except Exception as e:
                console.print(f"[red]{kind} failed: {e}[/]")
                self.events.add("warn", text=f"{kind} failed: {e}")
            finally:
                if not self.body.asleep:
                    self.performer.start()  # keep watching the child between jobs
                    self.performer.mood = "calm"
                self.busy = None
                self.last_reaction = time.monotonic()
                self.events.add("busy", busy=None, asleep=self.body.asleep)

        threading.Thread(target=job, name=kind, daemon=True).start()
        return True

    # ------------------------------------------------------------------ building blocks
    def say(self, text: str, lang: str, mood: str = "happy") -> None:
        self.teller.say(text, lang, mood=mood)

    def gesture(self, name: str) -> None:
        self.performer.stop()
        self.body.perform(name)

    def wake_up(self) -> None:
        self.performer.stop()
        self.body.wake_up()
        self.events.add("state", asleep=False)
        self.performer.start()

    def go_to_sleep(self) -> None:
        self.performer.stop()
        self.body.sleep()
        self.events.add("state", asleep=True)

    def flick_antennas(self, n: int) -> None:
        """Count with the antennas: one flick per finger."""
        b = self.body
        self.performer.stop()
        with b._busy("count"):
            for i in range(n):
                left = i % 2 == 0
                b.goto(head(z=4, roll=6 if left else -6),
                       antennas(-30 if left else REST, REST if left else -30), 0.16)
                b.goto(head(z=2), antennas(REST), 0.14)
            b.neutral(0.3)

    # ------------------------------------------------------------------ jobs
    def tell(self, request: str, heard: Optional[str], lang: Optional[str],
             minutes: Optional[int] = None, age: Optional[str] = None) -> bool:
        def go():
            t = self.teller
            t.opts.force_lang, t.opts.minutes, t.opts.age = lang, minutes, age
            t.lang = lang or heard or t.lang
            self.events.add("story_start", request=request)
            t.tell(request, heard)
        return self.run("story", go)

    def react_mood(self, mood: str, lang: str) -> bool:
        gesture, speak_mood = MOOD_ACT[mood]

        def go():
            self.events.add("mood", mood=mood)
            self.gesture(gesture)
            self.say(phrase(f"mood_{mood}", lang), lang, mood=speak_mood)
        return self.run("reaction", go)

    def react_fingers(self, n: int, lang: str) -> bool:
        def go():
            self.events.add("fingers", count=n)
            if n > 0:
                self.flick_antennas(n)
            self.say(fingers_phrase(n, lang), lang, mood="happy")
        return self.run("reaction", go)

    def stop(self) -> None:
        self.teller.stop()

    def transcribe(self, data: bytes, lang: Optional[str]) -> tuple[str, Optional[str]]:
        pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-f", "f32le",
                              "-ac", "1", "-ar", str(SR), "-"], input=data, capture_output=True,
                             check=True).stdout
        audio = np.frombuffer(pcm, dtype=np.float32).copy()
        if len(audio) < SR * 0.3:
            return "", None
        return self.stt.transcribe(audio, lang)


# ====================================================================== brain
class Companion:
    """Decides, from what the browser sees and hears, how Reachy reacts by itself."""

    FINGERS_HOLD = 1.0   # s a finger count must be steady before Reachy says it
    MOOD_HOLD = 1.5      # s an expression must be steady before Reachy asks about it
    AWAY_ASK = 5.0       # s without a face before "where did you go?"
    AWAY_NAP = 60.0      # s alone before Reachy naps

    def __init__(self, robot: Robot) -> None:
        self.robot = robot
        self.auto = True
        self.lang_setting: Optional[str] = None  # None = follow the child's language
        self.lang = "en"
        self.mood, self.mood_since, self.mood_asked, self.mood_asked_t = "neutral", 0.0, None, 0.0
        self.fingers, self.fingers_since, self.fingers_said = 0, 0.0, 0
        self.seen_someone = False
        self.away_asked = False
        self.streak_start: Optional[float] = None  # when the current run of face sightings began
        self.expect_reply_until = 0.0
        self.offer_story_until = 0.0
        self.offer_mood: Optional[str] = None
        self.lock = threading.Lock()
        threading.Thread(target=self._ticker, name="companion", daemon=True).start()

    def speak_lang(self) -> str:
        return self.lang_setting or self.lang

    # ------------------------------------------------------------------ inputs
    def perceive(self, face: Optional[dict], mood: Optional[str], fingers: Optional[int]) -> None:
        now = time.monotonic()
        if face:
            self.robot.eyes.source = "robot" if face.get("source") == "robot" else "computer"
            self.robot.eyes.update(face.get("x"), face.get("y"), face.get("size"))
            self.seen_someone = True
        if mood in MOODS or mood == "neutral":
            if mood != self.mood:
                self.mood, self.mood_since = mood, now
        if fingers is not None:
            fingers = max(0, min(10, int(fingers)))
            if fingers != self.fingers:
                self.fingers, self.fingers_since = fingers, now
        self.update()

    def hear(self, text: str, lang: Optional[str]) -> str:
        """React to one utterance. Returns what Reachy decided to do."""
        text = (text or "").strip()
        if not text or HALLUCINATIONS.match(text):
            return "ignored"
        if lang in LANGS and not self.lang_setting:
            self.lang = lang
        intent = intent_of(text)
        named = bool(INTENTS["name"].search(text))
        r = self.robot
        r.events.add("heard", text=text, lang=lang, intent=intent)
        t_end = time.monotonic() + 12  # a short reaction is finishing: answer right after it
        while r.busy == "reaction" and time.monotonic() < t_end:
            time.sleep(0.1)
        if r.busy:
            return "busy"
        L, now = self.speak_lang(), time.monotonic()

        if r.asleep:
            if intent == "story":
                return "story" if r.tell(text, lang, self.lang_setting) else "busy"
            if intent == "wake" or named:
                def wake():
                    r.gesture("hello")
                    r.say(phrase("awake", L), L)
                r.run("reaction", wake)
                return "wake"
            return "ignored"  # asleep: only "hi Reachy" wakes it

        if intent == "story":
            return "story" if r.tell(text, lang, self.lang_setting) else "busy"
        if intent == "yes" and now < self.offer_story_until:
            self.offer_story_until = 0
            hint = (f"(The child seemed {self.offer_mood} a moment ago. Tell a gentle, cheerful story "
                    f"that fits.)") if self.offer_mood else ""
            return "story" if r.tell(hint, L, self.lang_setting or L) else "busy"
        if intent == "sleep":
            def sleep():
                r.say(phrase("good_night", L), L, mood="sleepy")
                r.go_to_sleep()
            r.run("reaction", sleep, wake=False)
            return "sleep"
        if intent == "fingers":
            self.fingers_said = self.fingers
            r.react_fingers(self.fingers, L)
            return "fingers"
        if now < self.expect_reply_until:  # the child answered "what happened?"
            self.expect_reply_until = 0

            def thanks():
                r.gesture("comfort")
                r.say(phrase("thanks_feelings", L), L, mood="calm")
                self.offer_story_until = time.monotonic() + 25
            r.run("reaction", thanks)
            return "thanks"
        if intent == "wake":
            def hi():
                r.gesture("hello")
                r.say(phrase("hi_again", L), L)
            r.run("reaction", hi)
            return "hello"
        if named:
            r.run("reaction", lambda: r.say(phrase("help", L), L, mood="curious"))
            return "help"
        return "ignored"

    # ------------------------------------------------------------------ rules
    def _ticker(self) -> None:
        while True:
            time.sleep(0.25)
            try:
                self.update()
            except Exception as e:
                console.print(f"[red]companion: {e}[/]")

    def update(self) -> None:
        if not self.lock.acquire(blocking=False):
            return
        try:
            self._update()
        finally:
            self.lock.release()

    def _update(self) -> None:
        r, now = self.robot, time.monotonic()
        here = r.eyes.current(0.6) is not None
        if here:
            self.streak_start = self.streak_start or now
        else:
            self.streak_start = None
        if self.fingers == 0 and now - self.fingers_since > 1.5:
            self.fingers_said = 0  # hands down: the same number can be said again
        if self.mood == "neutral" and now - self.mood_since > 3.0:
            self.mood_asked = None  # face relaxed: the same mood can be asked about again

        if not self.auto or r.busy or r.asleep or now - r.last_reaction < 2.0:
            return
        L = self.speak_lang()
        gone = r.eyes.seconds_without_face()

        # 1. Presence: did the child leave, or come back?
        if self.away_asked:
            if here and self.streak_start and now - self.streak_start > 0.8:
                self.away_asked = False

                def back():
                    r.gesture("happy")
                    r.say(phrase("welcome_back", L), L)
                r.run("reaction", back)
            elif gone > self.AWAY_NAP:
                self.away_asked = False

                def nap():
                    r.say(phrase("nobody", L), L, mood="sleepy")
                    r.go_to_sleep()
                r.run("reaction", nap, wake=False)
            return
        if self.seen_someone and self.AWAY_ASK < gone < float("inf"):
            self.away_asked = True

            def where():
                r.say(phrase("where", L), L, mood="worried")
                r.gesture("look_around")
            r.run("reaction", where)
            return
        if not here:
            return

        # 2. Fingers (they win over the face: holding up a hand changes the expression too)
        if (self.fingers > 0 and now - self.fingers_since >= self.FINGERS_HOLD
                and self.fingers != self.fingers_said):
            self.fingers_said = self.fingers
            r.react_fingers(self.fingers, L)
            return

        # 3. Expression changed: ask about it
        if (self.fingers == 0 and self.mood in MOODS and now - self.mood_since >= self.MOOD_HOLD
                and self.mood != self.mood_asked and now - self.mood_asked_t > 8.0):
            self.mood_asked, self.mood_asked_t = self.mood, now
            self.offer_mood = self.mood
            self.expect_reply_until = now + 25
            r.react_mood(self.mood, L)

    def state(self) -> dict:
        r = self.robot
        return {"busy": r.busy, "asleep": r.asleep, "auto": self.auto, "lang": self.speak_lang(),
                "lang_setting": self.lang_setting, "mood": self.mood, "fingers": self.fingers,
                "face": r.eyes.current(0.6) is not None}


# ====================================================================== 3D view
class RobotView:
    """Renders the robot (following its real joint positions) for the web page."""

    def __init__(self, mini, scene: str) -> None:
        self.mini, self.scene = mini, scene
        self.jpeg: Optional[bytes] = None
        self.cond = threading.Condition()
        self.viewers = 0
        self.stop = threading.Event()
        self.error: Optional[str] = None

    def run_forever(self, fps: float = 20.0) -> None:
        """Runs on the main thread (OpenGL on macOS needs it)."""
        import cv2

        from .record import Mirror

        try:
            # Software rendering (servers, Docker: MUJOCO_GL=egl/osmesa) gets the cheap settings.
            fast = os.environ.get("MUJOCO_GL", "") in ("egl", "osmesa")
            size = (480, 360) if fast else (640, 480)
            mirror = Mirror(self.mini, scene=self.scene, width=size[0], height=size[1],
                            camera=VIEW_CAMERA, fast=fast)
        except Exception as e:
            self.error = f"3D view unavailable: {e}"
            console.print(f"[yellow]{self.error}[/]")
            self.stop.wait()
            return
        last = time.monotonic()
        while not self.stop.is_set():
            now = time.monotonic()
            try:
                mirror.advance(now - last)  # keep the physics in step even with no viewers
                if self.viewers > 0:
                    ok, buf = cv2.imencode(".jpg", mirror.render()[:, :, ::-1],
                                           [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ok:
                        with self.cond:
                            self.jpeg = buf.tobytes()
                            self.cond.notify_all()
            except Exception as e:
                self.error = str(e)
            last = now
            self.stop.wait(max(0.0, 1 / fps - (time.monotonic() - now)))

    def frames(self):
        self.viewers += 1
        try:
            while not self.stop.is_set():
                with self.cond:
                    self.cond.wait(timeout=1.0)
                    jpg = self.jpeg
                if jpg:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        finally:
            self.viewers -= 1


# ====================================================================== app
def create_app(robot: Robot, brain: Companion, view: RobotView) -> FastAPI:
    app = FastAPI(title="Reachy Mini dashboards")
    password = os.environ.get("APP_PASSWORD", "")

    @app.middleware("http")
    async def basic_auth(req: Request, call_next):
        """With APP_PASSWORD set, the browser asks for it once (any user name)."""
        if password and req.url.path != "/healthz":
            ok = False
            auth = req.headers.get("authorization", "")
            if auth.lower().startswith("basic "):
                try:
                    ok = base64.b64decode(auth[6:]).decode().partition(":")[2] == password
                except Exception:
                    ok = False
            if not ok:
                return Response("Password needed", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Reachy Mini"'})
        return await call_next(req)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/audio/{n}")
    def audio(n: int):
        mp3 = robot.speaker.get(n) if isinstance(robot.speaker, BrowserSpeaker) else None
        if mp3 is None:
            return Response(status_code=404)
        return Response(mp3, media_type="audio/mpeg")

    def busy_reply(ok: bool) -> JSONResponse:
        return JSONResponse({"ok": ok, "busy": robot.busy}, status_code=200 if ok else 409)

    def lang_or_default(code: Optional[str]) -> str:
        return resolve_lang(code) if code else brain.speak_lang()

    @app.get("/")
    def index():
        return FileResponse(HERE / "index.html")

    @app.get("/api/status")
    def status():
        return {"simulated": robot.simulated, "claude": robot.writer.client is not None,
                "claude_error": robot.writer.error, "model": robot.writer.model,
                "languages": {c: NAMES[c] for c in LANGS}, "view_error": view.error,
                "browser_audio": isinstance(robot.speaker, BrowserSpeaker), **brain.state()}

    @app.get("/api/events")
    def events(after: int = 0):
        return {"events": robot.events.since(after), **brain.state()}

    @app.post("/api/settings")
    async def settings(req: Request):
        d = await req.json()
        if "auto" in d:
            brain.auto = bool(d["auto"])
        if "lang" in d:
            brain.lang_setting = resolve_lang(d["lang"]) if d["lang"] else None
        return brain.state()

    @app.post("/api/perceive")
    async def perceive(req: Request):
        d = await req.json()
        brain.perceive(d.get("face"), d.get("mood"), d.get("fingers"))
        return {"ok": True}

    @app.post("/api/hear")
    async def hear(req: Request, lang: Optional[str] = None):
        data = await req.body()
        if robot.busy == "story":
            return {"text": "", "action": "busy"}
        try:  # in a worker thread, so the page keeps getting updates meanwhile
            text, heard = await asyncio.to_thread(robot.transcribe, data, resolve_lang(lang) if lang else None)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        action = await asyncio.to_thread(brain.hear, text, heard)
        return {"text": text, "lang": heard, "action": action}

    @app.post("/api/wake")
    def wake():
        if not robot.asleep:
            return busy_reply(True)
        L = brain.speak_lang()

        def go():
            robot.gesture("hello")
            robot.say(phrase("awake", L), L)
        return busy_reply(robot.run("reaction", go))

    @app.post("/api/sleep")
    def sleep():
        if robot.asleep:
            return busy_reply(True)
        L = brain.speak_lang()

        def go():
            robot.say(phrase("good_night", L), L, mood="sleepy")
            robot.go_to_sleep()
        return busy_reply(robot.run("reaction", go, wake=False))

    @app.post("/api/story")
    async def story(req: Request):
        d = await req.json()
        lang = resolve_lang(d.get("lang")) if d.get("lang") else None
        heard = d.get("heard") if d.get("heard") in LANGS else None
        minutes = int(d["minutes"]) if d.get("minutes") else None
        return busy_reply(robot.tell((d.get("request") or "").strip(), heard, lang, minutes,
                                     d.get("age") or None))

    @app.post("/api/stop")
    def stop():
        robot.stop()
        return {"ok": True}

    @app.post("/api/transcribe")
    async def transcribe(req: Request, lang: Optional[str] = None):
        data = await req.body()
        try:
            text, heard = await asyncio.to_thread(robot.transcribe, data, resolve_lang(lang) if lang else None)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return {"text": text, "lang": heard}

    @app.post("/api/mood")
    async def mood(req: Request):
        d = await req.json()
        m = d.get("mood")
        if m not in MOODS:
            return JSONResponse({"error": f"mood must be one of {MOODS}"}, status_code=400)
        brain.mood_asked, brain.mood_asked_t = m, time.monotonic()
        return busy_reply(robot.react_mood(m, lang_or_default(d.get("lang"))))

    @app.post("/api/fingers")
    async def fingers(req: Request):
        d = await req.json()
        n = max(0, min(10, int(d.get("count", 0))))
        brain.fingers_said = n
        return busy_reply(robot.react_fingers(n, lang_or_default(d.get("lang"))))

    @app.get("/api/robot.mjpg")
    def robot_view():
        return StreamingResponse(view.frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/camera.mjpg")
    def camera():
        def frames():
            while True:
                jpg = robot.mini.media.get_frame_jpeg()
                if jpg:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                time.sleep(1 / 15)
        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    return app


def run_web(mini, body: Body, args, simulated: bool) -> None:
    import uvicorn

    spk = args.speaker if args.speaker != "auto" else ("computer" if simulated else "robot")
    speaker = {"computer": ComputerSpeaker, "browser": BrowserSpeaker}.get(spk, lambda: RobotSpeaker(mini))()
    writer = StoryWriter(offline=args.offline)
    if writer.error:
        console.print(f"[yellow]{writer.error} Stories will use the built-in story.[/]")
    robot = Robot(mini, body, speaker, writer, Transcriber(args.stt_model), simulated)
    if isinstance(speaker, BrowserSpeaker):
        speaker.events = robot.events
    brain = Companion(robot)
    view = RobotView(mini, "minimal" if simulated else "empty")
    if not body.asleep:
        body.rest_pose()  # start asleep: "Hi Reachy" wakes it up

    server = uvicorn.Server(uvicorn.Config(create_app(robot, brain, view), host=args.web_host,
                                           port=args.web_port, log_level="warning"))
    threading.Thread(target=server.run, name="web", daemon=True).start()
    url = f"http://127.0.0.1:{args.web_port}"
    console.print(f"[bold green]Reachy Mini dashboards:[/] {url}   (Ctrl+C to quit)")
    console.print("[dim]Reachy is asleep. Say \"Hi Reachy\" in the browser to wake it up.[/]")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        view.run_forever()  # main thread: renders the 3D view
    except KeyboardInterrupt:
        pass
    finally:
        view.stop.set()
        server.should_exit = True
        robot.stop()
        robot.performer.close()
        speaker.close()
