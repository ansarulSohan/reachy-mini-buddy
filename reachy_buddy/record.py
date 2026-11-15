"""Record the interaction demo as a captioned video.

The simulator's own 3D window can't be screen-captured from a script, so this
loads the same MuJoCo scene in a second, off-screen instance ("mirror"), feeds it
the robot's live joint positions from the daemon, and renders it from a fixed
camera with the conversation overlaid as subtitles.

Usage:  python -m reachy_buddy record [--out recordings/reachy_demo.mp4]
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
import threading
import time
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import reachy_mini

if TYPE_CHECKING:
    from .app import Captions, Conversation

W, H, FPS = 1280, 720, 25  # final video size
SUB_H = 116  # subtitle strip under the 3D view, so captions never hide the scene
SCENE_H = H - SUB_H
CAMERA = dict(lookat=(0.28, 0.0, 0.05), distance=1.05, azimuth=125.0, elevation=-22.0)


class Mirror:
    """Off-screen copy of the simulator that follows the real daemon's joints."""

    def __init__(self, mini, scene: str = "minimal", width: int = W, height: int = SCENE_H,
                 camera: dict = CAMERA, fast: bool = False) -> None:
        root = files(reachy_mini).joinpath("descriptions/reachy_mini/mjcf")
        self.model = mujoco.MjModel.from_xml_path(str(root / "scenes" / f"{scene}.xml"))
        self.model.opt.timestep = 0.002
        self.data = mujoco.MjData(self.model)
        names = [self.model.actuator(i).name for i in range(self.model.nu)]
        self.addr = [int(self.model.joint(n).qposadr[0]) for n in names]
        self.mini = mini

        q = self._targets()
        self.data.qpos[self.addr] = q
        self.data.ctrl[:] = q
        # Same start-up as the daemon: settle the closed-chain head before
        # enabling the robot's own collision geoms.
        unnamed = [i for i in range(self.model.ngeom) if self.model.geom_contype[i]
                   and mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, i) is None]
        for i in unnamed:
            self.model.geom_contype[i] = self.model.geom_conaffinity[i] = 0
        mujoco.mj_forward(self.model, self.data)
        for _ in range(200):
            mujoco.mj_step(self.model, self.data)
        for i in unnamed:
            self.model.geom_contype[i] = self.model.geom_conaffinity[i] = 1
        for _ in range(200):
            mujoco.mj_step(self.model, self.data)

        # fast: no anti-aliasing, shadows or reflections (~5x faster on a server without a GPU)
        self.fast = fast
        if fast:
            self.model.vis.quality.offsamples = 0
        self.renderer = mujoco.Renderer(self.model, height=height, width=width)
        self.cam = mujoco.MjvCamera()
        self.cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        self.cam.lookat[:] = camera["lookat"]
        self.cam.distance = camera["distance"]
        self.cam.azimuth = camera["azimuth"]
        self.cam.elevation = camera["elevation"]

    def _targets(self) -> np.ndarray:
        head, ants = self.mini.get_current_joint_positions()
        # Daemon convention: MuJoCo antenna joints are the negated SDK values.
        return np.array(list(head) + [-a for a in ants])

    def advance(self, dt: float) -> None:
        self.data.ctrl[:] = self._targets()
        for _ in range(max(1, min(60, round(dt / self.model.opt.timestep)))):
            mujoco.mj_step(self.model, self.data)

    def render(self) -> np.ndarray:
        self.renderer.update_scene(self.data, self.cam)
        if self.fast:
            for flag in (mujoco.mjtRndFlag.mjRND_SHADOW, mujoco.mjtRndFlag.mjRND_REFLECTION):
                self.renderer.scene.flags[flag] = 0
        return self.renderer.render()


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for path, index in (("/System/Library/Fonts/Helvetica.ttc", 1 if bold else 0),
                        ("/System/Library/Fonts/SFNS.ttf", 0),
                        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
                         else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0)):
        try:
            return ImageFont.truetype(path, size, index=index)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


class Overlay:
    """Draws the title tag and current gesture on the 3D view, subtitles in a strip below it."""

    def __init__(self, width: int = W, height: int = H, sub_h: int = SUB_H) -> None:
        s = height / 720
        self.w, self.h, self.sub_h, self.s = width, height, sub_h, s
        self.f_tag = _font(int(20 * s), bold=True)
        self.f_name = _font(int(25 * s), bold=True)
        self.f_text = _font(int(25 * s))

    def draw(self, frame: np.ndarray, cap: "Captions") -> bytes:
        img = Image.new("RGBA", (self.w, self.h), (12, 14, 22, 255))
        img.paste(Image.fromarray(frame).convert("RGBA"), (0, 0))
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        s, pad = self.s, int(18 * self.s)

        title = "REACHY MINI  ·  MuJoCo simulation"
        tw = d.textlength(title, font=self.f_tag)
        d.rounded_rectangle((pad, pad, pad + tw + 2 * pad, pad + int(40 * s)), radius=int(20 * s),
                            fill=(15, 18, 28, 190))
        d.text((2 * pad, pad + int(9 * s)), title, font=self.f_tag, fill=(235, 238, 245, 255))

        if cap.gesture:
            g = cap.gesture.replace("_", " ")
            gw = d.textlength(g, font=self.f_tag)
            tri = int(12 * s)  # "playing" triangle, drawn (fonts may lack the glyph)
            x1, y0 = self.w - pad, pad
            x0 = x1 - gw - 3 * pad - tri
            d.rounded_rectangle((x0, y0, x1, y0 + int(40 * s)), radius=int(20 * s),
                                fill=(255, 196, 61, 235))
            cy, tx = y0 + int(20 * s), x0 + pad
            d.polygon([(tx, cy - tri // 2), (tx, cy + tri // 2), (tx + tri, cy)], fill=(25, 20, 5, 255))
            d.text((tx + tri + pad // 2, y0 + int(9 * s)), g, font=self.f_tag, fill=(25, 20, 5, 255))

        lines: list[tuple[str, str, tuple]] = []
        wrap = int(80 * self.w / 1280)
        if cap.user:
            lines.append(("You", textwrap.shorten(cap.user, wrap, placeholder="..."),
                          (110, 205, 255, 255)))
        if cap.robot:
            chunks = textwrap.wrap(cap.robot, wrap)[: 3 - len(lines)]
            for i, chunk in enumerate(chunks):
                lines.append(("Reachy" if i == 0 else "", chunk, (255, 140, 220, 255)))
        lh = int(34 * s)
        y = self.h - self.sub_h + (self.sub_h - lh * max(2, len(lines))) // 2
        for who, text, color in lines:
            if who:
                d.text((2 * pad, y), who, font=self.f_name, fill=color)
            d.text((2 * pad + int(108 * s), y), text, font=self.f_text, fill=(245, 245, 250, 255))
            y += lh
        return Image.alpha_composite(img, layer).convert("RGB").tobytes()


def _gif_from_mp4(mp4: Path, ffmpeg: str) -> Optional[Path]:
    gif = mp4.with_suffix(".gif")
    vf = ("fps=12,scale=640:-1:flags=lanczos,split[a][b];"
          "[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5")
    r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(mp4), "-vf", vf, str(gif)])
    return gif if r.returncode == 0 else None


def record_demo(mini, conv: "Conversation", out: str = "recordings/reachy_demo.mp4") -> None:
    from .app import console, run_demo

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("record needs ffmpeg (macOS: `brew install ffmpeg`).")
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    mirror = Mirror(mini)
    overlay = Overlay()
    enc = subprocess.Popen(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "21", "-preset", "medium",
         "-movflags", "+faststart", str(out_path)],
        stdin=subprocess.PIPE)

    done = threading.Event()
    error: list[BaseException] = []

    def worker() -> None:
        try:
            run_demo(conv)
            time.sleep(1.5)  # let the last pose settle on camera
        except BaseException as e:  # surfaced after the video is closed
            error.append(e)
        finally:
            done.set()

    console.print(f"[dim]Recording to {out_path} ...[/]")
    threading.Thread(target=worker, name="demo", daemon=True).start()
    t0 = last = time.monotonic()
    frames = 0
    try:
        while not done.is_set():
            now = time.monotonic()
            mirror.advance(now - last)
            last = now
            due = int((now - t0) * FPS) + 1
            if due > frames:
                frame = overlay.draw(mirror.render(), conv.captions)
                for _ in range(due - frames):  # duplicate if we fell behind: keeps real time
                    enc.stdin.write(frame)
                frames = due
            time.sleep(0.003)
    finally:
        enc.stdin.close()
        enc.wait()
    console.print(f"[green]Saved video:[/] {out_path}  ({frames / FPS:.0f} s)")
    gif = _gif_from_mp4(out_path, ffmpeg)
    if gif:
        console.print(f"[green]Saved GIF:[/]   {gif}")
    if error:
        raise error[0]
