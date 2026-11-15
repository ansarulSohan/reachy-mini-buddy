"""Reachy Buddy: an interactive companion program for Reachy Mini.

Modes
  demo      scripted conversation, so you can watch the interaction (default)
  chat      type to Reachy yourself; it answers with words and body language
  gestures  play every gesture once, labelled
  record    run the demo and save a captioned video of it (see record.py)
  web       dashboards in the browser: Story teller, Mood reader, Finger counter (see web.py)
  story     storyteller for children: sees your face, hears your request, tells a
            1-10 minute story in English, Norsk, Bangla, Hindi or Urdu (see storyteller.py)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .brain import Brain, Step
from .gestures import GESTURES, SCENE_OBJECTS, Body

console = Console()

DEMO_SCRIPT = [
    "Wake up, Reachy!",
    "Hi there!",
    "What's your name?",
    "What do you see on the table?",
    "Look at the duck!",
    "Do you like the apple?",
    "Can you tell me a joke?",
    "I had a bad day today...",
    "You're a good robot!",
    "Are you a robot?",
    "Are you a human?",
    "Boo!",
    "Let's dance!",
    "Thank you, Reachy!",
    "Good night, Reachy.",
]


# ---------------------------------------------------------------------- daemon
def find_daemon(host: Optional[str], port: Optional[int]) -> int:
    """Return the port of a running Reachy Mini daemon (sim or real)."""
    candidates = [port] if port else []
    if not port:
        if os.environ.get("REACHY_PORT"):
            candidates.append(int(os.environ["REACHY_PORT"]))
        candidates += [8000, 8100]
    for p in dict.fromkeys(candidates):
        url = f"http://{host or '127.0.0.1'}:{p}/api/daemon/status"
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                status = json.load(r)
            if "robot_name" in status:
                if status.get("state") not in (None, "running"):
                    console.print(f"[yellow]Daemon on port {p} is '{status.get('state')}'.[/]")
                return p
        except Exception:
            continue
    console.print(Panel(
        "No Reachy Mini daemon found on ports "
        f"{', '.join(map(str, dict.fromkeys(candidates)))}.\n\n"
        "Start the simulator first, in another terminal:\n  [bold]./run_sim.sh[/]\n"
        "(or plug in a real Reachy Mini and run [bold]reachy-mini-daemon[/])",
        title="Can't reach the robot", border_style="red"))
    sys.exit(1)


def daemon_status(host: Optional[str], port: int) -> dict:
    try:
        with urllib.request.urlopen(f"http://{host or '127.0.0.1'}:{port}/api/daemon/status",
                                    timeout=1.5) as r:
            return json.load(r)
    except Exception:
        return {}


def connect(host: Optional[str], port: int, sounds: bool):
    from reachy_mini import ReachyMini

    return ReachyMini(
        host=host or "localhost",
        port=port,
        connection_mode="network" if host else "localhost_only",
        media_backend="default" if sounds else "no_media",
    )


# ---------------------------------------------------------------------- voice
class Voice:
    """Optional text-to-speech through the computer's speakers (macOS `say` / espeak)."""

    def __init__(self, enabled: bool) -> None:
        self.cmd: Optional[list[str]] = None
        self.proc: Optional[subprocess.Popen] = None
        if enabled:
            if shutil.which("say"):
                self.cmd = ["say", "-r", "195"]
            elif shutil.which("espeak"):
                self.cmd = ["espeak", "-s", "165"]
            else:
                console.print("[yellow]--voice: no `say` or `espeak` found, staying silent.[/]")

    def say(self, text: str) -> None:
        if self.cmd:
            self.wait()
            self.proc = subprocess.Popen(self.cmd + [text], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)

    def wait(self, timeout: float = 8.0) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ---------------------------------------------------------------------- conversation
@dataclass
class Captions:
    """What's being said right now (read by the video recorder)."""
    user: str = ""
    robot: str = ""
    gesture: Optional[str] = None


class Conversation:
    def __init__(self, body: Body, voice: Voice, seed: Optional[int] = None,
                 captions: Optional[Captions] = None) -> None:
        self.body = body
        self.voice = voice
        self.brain = Brain(seed)
        self.captions = captions or Captions()
        body.on_gesture = self._on_gesture

    def _on_gesture(self, name: Optional[str]) -> None:
        self.captions.gesture = name

    def user_says(self, text: str, typed: bool = False) -> None:
        self.captions.user, self.captions.robot = text, ""
        if typed:  # demo mode: "type" the line so it reads like a real conversation
            console.print("[bold cyan]You  [/] ", end="")
            for ch in text:
                console.print(ch, end="", style="cyan", highlight=False)
                time.sleep(0.03)
            console.print()
        intent, steps = self.brain.respond(text)
        self.run(steps)

    def run(self, steps: list[Step]) -> None:
        for step in steps:
            if step.say:
                self.captions.robot = step.say
                console.print(f"[bold magenta]Reachy[/] {step.say}", highlight=False)
                self.voice.say(step.say)
            if step.do:
                console.print(f"       [dim]({step.do}{' ' + ' '.join(map(str, step.args)) if step.args else ''})[/]")
                self.body.perform(step.do, *step.args)
        self.voice.wait()


# ---------------------------------------------------------------------- modes
def run_demo(conv: Conversation, loop: bool = False, pause: float = 1.4,
             on_line: Optional[Callable[[int, str], None]] = None) -> None:
    body = conv.body
    console.rule("[bold]Reachy Mini - interaction demo")
    body.rest_pose()  # start asleep so the demo can wake it up
    time.sleep(1.0)
    while True:
        for i, line in enumerate(DEMO_SCRIPT):
            if on_line:
                on_line(i, line)
            conv.user_says(line, typed=True)
            time.sleep(pause)  # idle breathing plays here
        if not loop:
            break
        time.sleep(2.0)
    conv.captions.user = conv.captions.robot = ""


def run_chat(conv: Conversation) -> None:
    body = conv.body
    examples = ("hi · what's your name? · what do you see? · look at the duck · tell me a joke · "
                "let's dance · I had a bad day · boo! · good night")
    console.print(Panel(
        f"Type anything and Reachy will answer with words and body language.\n"
        f"Try: [cyan]{examples}[/]\n"
        f"Commands: [green]/help[/] list gestures · [green]/<gesture>[/] e.g. /dance or /look_at apple · "
        f"[green]/quit[/]", title="Chat with Reachy Mini", border_style="magenta"))
    if body.asleep:
        body.wake_up()
    else:
        body.neutral()
    while True:
        try:
            text = console.input("[bold cyan]You   [/]").strip()
        except (EOFError, KeyboardInterrupt):
            text = "/quit"
        if not text:
            continue
        if text.startswith("/"):
            cmd, *args = text[1:].split()
            if cmd in ("quit", "exit", "q"):
                break
            if cmd == "help":
                print_gestures()
                continue
            if cmd in GESTURES:
                try:
                    body.perform(cmd, *args)
                except (KeyError, TypeError) as e:
                    console.print(f"[red]{e}[/]")
                continue
            console.print(f"[red]Unknown command /{cmd}. Type /help.[/]")
            continue
        conv.user_says(text)
    console.print("[magenta]Reachy[/] Bye! Going to sleep...")
    body.sleep()


def run_gestures(body: Body) -> None:
    if body.asleep:
        body.wake_up()
    for name in GESTURES:
        if name in ("neutral", "rest_pose", "wake_up", "sleep"):
            continue
        args = ()
        if name == "look_at":
            for obj in SCENE_OBJECTS:
                console.print(f"[bold]look_at {obj}[/]")
                body.look_at(obj)
            continue
        console.print(f"[bold]{name}[/]  [dim]{GESTURES[name]}[/]")
        body.perform(name, *args)
        time.sleep(0.6)
    body.sleep()


def print_gestures() -> None:
    table = Table(title="Gestures", show_header=True, header_style="bold")
    table.add_column("command", style="green")
    table.add_column("what it does")
    for name, desc in GESTURES.items():
        table.add_row(f"/{name}" + (" <duck|apple|croissant>" if name == "look_at" else ""), desc)
    console.print(table)


def run_story(mini, body: Body, args, simulated: bool) -> None:
    from .senses import ComputerMic, Ears, Eyes, RobotMic, Transcriber
    from .speech import TTS, ComputerSpeaker, RobotSpeaker
    from .story_writer import StoryWriter
    from .storyteller import StoryOptions, StoryTeller, resolve_lang

    def pick(choice: str) -> str:  # robot hardware on a real robot, the computer's in the simulator
        return ("computer" if simulated else "robot") if choice == "auto" else choice

    cam, mic_src, spk = pick(args.camera), pick(args.mic), pick(args.speaker)
    if spk == "browser":
        raise SystemExit("--speaker browser only works in web mode.")
    eyes = ears = mic = None
    if cam != "none":
        try:
            eyes = Eyes(cam, mini, webcam_index=args.webcam)
        except Exception as e:
            console.print(f"[yellow]Camera ({cam}) unavailable: {e}. Face detection is off.[/]")
    if mic_src != "none" and not args.type:
        try:
            mic = ComputerMic() if mic_src == "computer" else RobotMic(mini)
            ears = Ears(mic, Transcriber(args.stt_model))
        except Exception as e:
            console.print(f"[yellow]Microphone ({mic_src}) unavailable: {e}. Type your requests instead.[/]")
    speaker = ComputerSpeaker() if spk == "computer" else RobotSpeaker(mini)
    writer = StoryWriter(offline=args.offline)
    if writer.error:
        console.print(f"[yellow]{writer.error} Using the built-in story for now.[/]")
    opts = StoryOptions(lang=resolve_lang(args.lang) or "en", force_lang=resolve_lang(args.lang),
                        minutes=args.minutes, age=args.age, request=args.request, once=args.once,
                        presence=not args.no_presence, typed=args.type or ears is None)
    console.print(Panel(
        f"camera: [bold]{cam if eyes else 'off'}[/] · microphone: [bold]{mic_src if ears else 'typed'}[/] · "
        f"speaker: [bold]{spk}[/] · stories: [bold]{'Claude ' + writer.model if writer.client else 'built-in'}[/]",
        title="Reachy Mini storyteller", border_style="magenta"))
    teller = StoryTeller(body, speaker, TTS(speaker.sr), writer, eyes, ears, opts)
    try:
        teller.run()
    finally:
        teller.performer.close()
        speaker.close()
        for dev in (eyes, mic):
            if dev:
                dev.close()


# ---------------------------------------------------------------------- entry
def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m reachy_buddy", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", nargs="?", default="demo",
                        choices=["demo", "chat", "gestures", "record", "story", "web"])
    parser.add_argument("--host", help="robot/daemon host for a remote (wireless) robot")
    parser.add_argument("--port", type=int, help="daemon port (default: auto-detect 8000/8100)")
    parser.add_argument("--voice", action="store_true", help="speak replies out loud (macOS `say`)")
    parser.add_argument("--no-sound", action="store_true", help="skip robot sound effects / media")
    parser.add_argument("--loop", action="store_true", help="demo: repeat forever")
    parser.add_argument("--out", default="recordings/reachy_demo.mp4", help="record: output video")
    st = parser.add_argument_group("story mode")
    st.add_argument("--lang", help="story: fix the language (en, no, bn, hi, ur); default: the child's")
    st.add_argument("--minutes", type=int, choices=range(1, 11), metavar="1-10",
                    help="story: fix the length; default: what the child asks for, else 3")
    st.add_argument("--age", help="story: the child's age, e.g. 4")
    st.add_argument("--request", help='story: skip listening, e.g. "a story about a brave little cat"')
    st.add_argument("--once", action="store_true", help="story: tell one story, then exit")
    st.add_argument("--type", action="store_true", help="story: type requests instead of speaking")
    st.add_argument("--camera", choices=["auto", "robot", "computer", "none"], default="auto")
    st.add_argument("--mic", choices=["auto", "robot", "computer", "none"], default="auto")
    st.add_argument("--speaker", choices=["auto", "robot", "computer", "browser"], default="auto",
                    help="where Reachy's voice plays; 'browser' (web mode) plays it in the web page")
    st.add_argument("--webcam", type=int, default=0, help="story: webcam index for --camera computer")
    st.add_argument("--stt-model", help="story: Whisper model (default: large-v3-turbo on Apple "
                                        "Silicon, small elsewhere)")
    st.add_argument("--offline", action="store_true", help="story: don't use Claude; built-in story")
    st.add_argument("--no-presence", action="store_true",
                    help="story: don't pause when the child leaves the camera view")
    wb = parser.add_argument_group("web mode (dashboards: story teller, mood reader, finger counter)")
    wb.add_argument("--web-port", type=int, default=8080, help="web: port for the dashboards")
    wb.add_argument("--web-host", default="127.0.0.1",
                    help="web: address to listen on (0.0.0.0 on a server or in Docker)")
    wb.add_argument("--no-browser", action="store_true", help="web: don't open a browser")
    args = parser.parse_args(argv)

    port = find_daemon(args.host, args.port)
    console.print(f"[dim]Connecting to Reachy Mini daemon on {args.host or 'localhost'}:{port}...[/]")
    simulated = bool(daemon_status(args.host, port).get("simulation_enabled"))
    sounds = not args.no_sound and args.mode != "record"
    with connect(args.host, port, sounds) as mini:
        body = Body(mini, sounds=sounds)
        body.idle(True)
        voice = Voice(args.voice)
        conv = Conversation(body, voice, seed=7 if args.mode in ("demo", "record") else None)
        try:
            if args.mode == "demo":
                run_demo(conv, loop=args.loop)
            elif args.mode == "chat":
                run_chat(conv)
            elif args.mode == "gestures":
                run_gestures(body)
            elif args.mode == "record":
                from .record import record_demo
                record_demo(mini, conv, args.out)
            elif args.mode == "story":
                run_story(mini, body, args, simulated)
            elif args.mode == "web":
                from .web import run_web
                run_web(mini, body, args, simulated)
        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted - returning to neutral.[/]")
            body.idle(False)
            try:
                body.neutral()
            except Exception:
                pass
        finally:
            body.idle(False)
            body.close()
