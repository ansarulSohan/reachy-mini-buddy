# Reachy Buddy: an interactive companion for Reachy Mini

Reachy Buddy is a Python program for the [Reachy Mini](https://huggingface.co/docs/reachy_mini) desktop robot.
You talk to it, and it answers with words and body language. It moves its head (6-DoF Stewart platform),
both antennas, and its rotating body. Between replies it breathes gently, so it never looks frozen.

The program uses the official `reachy_mini` SDK. It runs unchanged in the MuJoCo simulator and on a real robot.

## Quick start (simulator, no robot needed)

```bash
./setup.sh                                   # once: .venv with Python 3.12 + reachy-mini[mujoco]
./run_sim.sh                                 # terminal 1: opens the 3D simulator window
.venv/bin/python -m reachy_buddy demo        # terminal 2: watch the interaction
```

## Modes

| Command | What happens |
|---|---|
| `python -m reachy_buddy demo` | Plays a scripted 15-line conversation. Reachy wakes up, greets you, looks around the table, stares at the duck, tells a joke, comforts you, gets embarrassed, dances, and goes back to sleep. Add `--loop` to repeat it. |
| `python -m reachy_buddy chat` | You type and Reachy answers. Try `hi`, `what do you see?`, `look at the apple`, `tell me a joke`, `I had a bad day`, `boo!`, `let's dance`, `good night`. Slash commands run gestures directly: `/help`, `/dance`, `/look_at croissant`, `/quit`. |
| `python -m reachy_buddy gestures` | Plays every gesture once, each one labelled. |
| `python -m reachy_buddy record` | Runs the demo and saves a captioned video to `recordings/reachy_demo.mp4`, plus a `.gif`. Needs `ffmpeg`. |

Options:
- `--voice` speaks the replies out loud (macOS `say`, or `espeak` on Linux).
- `--no-sound` turns off the robot's built-in sound effects.
- `--port N` sets the daemon port. By default the program checks ports 8000 and 8100.
- `--host H` connects to a wireless robot over the network.

## Dashboards in the browser: Story teller, Mood reader, Finger counter

```bash
export ANTHROPIC_API_KEY=sk-ant-...          # new stories from Claude (without it: one built-in story)
.venv/bin/python -m reachy_buddy web         # opens http://127.0.0.1:8080
```

The browser is Reachy's eyes and ears: it asks for camera and microphone access by itself, runs face and
hand tracking (MediaPipe), and sends the results to the robot. The page shows a **live 3D view of
Reachy** (it follows the real joint positions, in the simulator or on a real robot) with what it says
as captions.

**Live tab: talk to Reachy hands-free.** Press 🎙 *Start listening* once. After that:

| You | Reachy |
|---|---|
| say **"Hi Reachy"** | wakes up from sleep and greets you (it starts asleep) |
| hold up **1–10 fingers** | says how many, counting with its antennas |
| say **"tell me a story about …"** | tells a story (Claude writes it, 1–10 minutes) |
| change your **expression** | asks about it: "You look sad. What happened?" If you answer, it offers a story, and "yes" starts one. |
| **leave the camera** | "Where did you go?" and "There you are!" when you come back. After a minute alone, it naps. |
| say **"Good night, Reachy"** | goes to sleep |

Voice commands work in all five languages ("Hei Reachy", "রিচি, একটা গল্প বলো", "रीची, कहानी सुनाओ",
"ریچی، کہانی سناؤ"). Reachy answers in your language. For short Bengali sentences, set *Reachy speaks*
to Bangla: Whisper sometimes mistakes them for Hindi. Keep only one dashboard page open, because each
open page sends what its camera sees.

| Other tabs | What happens |
|---|---|
| **Story teller** | Type or say (🎤) what the story should be about. Pick a language (or Auto), the child's age and a length of 1 to 10 minutes. Claude writes the story, and Reachy starts telling it after a few seconds, acting out each paragraph's mood. The text appears as it is told. **Stop** ends it. |
| **Mood reader** | Reachy reads your face as happy, sad, surprised, angry or confused, and says what it sees (in the "Reachy speaks" language) with a matching gesture. Press "Learn my normal face" once, with a relaxed face, for better results. |
| **Finger counter** | Hold up 1 to 10 fingers with one or two hands. Reachy says the number and flicks its antennas once per finger. |

Options: `--web-port N`, `--no-browser`, `--speaker robot|computer`, `--offline`. In the dashboard you can
switch the camera to **Reachy's head camera** (on a real robot).

## Put it on a server (Coolify)

The `Dockerfile` packs everything into one container: a simulated Reachy Mini, the dashboards and speech
recognition. Reachy's voice plays in the visitor's browser.

1. Push this folder to a (private) GitHub repository.
2. In Coolify: **New Resource → your repository → Build Pack: Dockerfile**.
3. **Ports Exposes:** `8080`. **Domain:** e.g. `https://reachy.example.com`. HTTPS is required, because
   browsers only give camera and microphone access to secure pages.
4. **Environment variables:**
   | Name | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | your Claude key (new stories). Without it: one built-in story |
   | `APP_PASSWORD` | a password for the page (any user name). Strongly recommended |
   | `STT_MODEL` | optional: `small` (default) or `medium`, which is better for Bangla/Urdu but slower |
5. **Deploy**, open the domain, log in, and click once on the page to turn on Reachy's voice.

Give it at least 2 CPU cores and 4 GB of RAM. The first build takes a while (it downloads the speech model).
It is one shared robot: everyone who opens the page talks to the same Reachy.

**Your real robot instead of the simulator:** the server must be able to reach the robot's daemon, for
example with [Tailscale](https://tailscale.com) on both. Then set `REACHY_HOST` to the robot's address
(and `REACHY_PORT` if not 8000).

Test the container locally:

```bash
docker build -t reachy-web .
docker run --rm -p 8090:8080 -e APP_PASSWORD=test -e ANTHROPIC_API_KEY reachy-web
# open http://localhost:8090
```

## Storyteller without the browser

```bash
.venv/bin/python -m reachy_buddy story                    # waits for a face, then asks which story
.venv/bin/python -m reachy_buddy story --lang bn --minutes 5 --age 4
.venv/bin/python -m reachy_buddy story --request "a dragon story in Urdu" --once
.venv/bin/python -m reachy_buddy story --type             # type requests instead of speaking
```

- **Languages:** English, Norsk (Bokmål), বাংলা (Bangla), हिंदी (Hindi), اردو (Urdu). By default the
  child's language is used. A language they name ("in Urdu", "på norsk", "বাংলায়") wins, and `--lang` fixes it.
- **Length:** 1 to 10 minutes. "a 5 minute story", "short" and "long" work. `--minutes` fixes it. The
  default is 3. Stories are cut off at 10 minutes at the latest.
- **Devices:** on a real robot it uses Reachy's camera, microphone and speaker. In the simulator it uses
  the computer's. Override with `--camera`, `--mic` and `--speaker`.
- If the child walks away, Reachy pauses and asks where they went (`--no-presence` turns this off).
- The first time, macOS asks your terminal app for camera and microphone permission.

How it works (`reachy_buddy/`):

```
story_writer.py  Claude (claude-opus-5-5) streams the story line by line: LANGUAGE / MINUTES / TITLE,
                 then "[mood] paragraph" lines. Falls back to a built-in story when offline.
speech.py        neural TTS per language (edge-tts; macOS `say` fallback), robot or computer speaker
senses.py        YuNet face detection, voice-activity detection, local Whisper (limited to the 5 languages)
storyteller.py   the session flow + "Performer": head/antenna motion synced to the voice, mood poses,
                 face tracking; writer -> TTS -> playback run in parallel, so there are no long pauses
languages.py     voices, speaking rates, all spoken phrases in 5 languages, the built-in story
web.py, web/     FastAPI server + the three dashboards
```

## Gestures

`hello`, `nod_yes`, `shake_no`, `happy`, `laugh`, `sad`, `comfort`, `surprised`, `curious`, `thinking`,
`confused`, `shy`, `look_around`, `look_at <duck|apple|croissant>`, `dance`, `goodbye`, `wake_up`, `sleep`

## How it works

```
reachy_buddy/
  brain.py     text -> intent (regex rules) -> list of Steps (say X, do gesture Y). No API keys needed.
  gestures.py  Body class: the gestures + a background "breathing" loop that runs between gestures
  app.py       CLI, daemon auto-discovery, demo script, chat REPL, optional text-to-speech
  record.py    off-screen MuJoCo "mirror" that follows the robot's joints and renders a captioned video
```

- Short expressive moves use `goto_target(...)` with `minjerk`, `ease_in_out` or `cartoon` interpolation.
- Rhythmic moves (happy, laugh, dance) are functions of time sent through `set_target(...)` at 50 Hz.
  A smooth envelope makes each one start and end exactly at rest.
- `look_at` uses `look_at_world()` to aim the head at an object on the simulator's table. The body turns
  halfway toward the object and the head turns the rest of the way.
- A lock is shared between gestures and the idle breathing loop, so only one of them controls the motors
  at a time.

## Using a real Reachy Mini

- **Lite (USB):** start `reachy-mini-daemon`, then run `python -m reachy_buddy chat`.
- **Wireless:** the daemon runs on the robot. Run `python -m reachy_buddy chat --host reachy-mini.local`.

The `look_at` targets are the positions of the objects in the simulator scene. On a real table, change
`SCENE_OBJECTS` in `gestures.py` to match where your objects are.

## Troubleshooting

- **Port 8000 is already in use** (for example, by Docker): `run_sim.sh` switches to port 8100
  automatically, and the program finds it there.
- **The first simulator launch is slow:** GStreamer builds its plugin cache once. Later launches are fast.
- **`libgstpython` segfault on macOS:** see the "Troubleshooting" section of the official
  [simulation guide](https://huggingface.co/docs/reachy_mini/platforms/simulation/get_started).
