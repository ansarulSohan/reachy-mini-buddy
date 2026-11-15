"""A tiny rule-based "brain": turns what you type into words + gestures.

No API keys or network needed. Each intent is a regex plus one or more
alternative responses; a response is a list of Steps (say something, do a
gesture). The first intent whose pattern matches wins, so order matters.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field


@dataclass
class Step:
    say: str | None = None
    do: str | None = None
    args: tuple = field(default_factory=tuple)


def S(say: str | None = None, do: str | None = None, *args) -> Step:
    return Step(say, do, args)


OBJECT_LINES = {
    "duck": "Quack! A little rubber duck. It's my best friend on this table.",
    "apple": "A shiny red apple! I can't eat it, but it sure looks delicious.",
    "croissant": "Mmm, a croissant! It smells like a bakery in here.",
}

JOKES = [
    ("Why did the robot go on vacation?", "To recharge its batteries!"),
    ("What do you call a robot that takes the long way around?", "R2-Detour!"),
    ("Why was the robot so bad at soccer?", "It kept booting up instead of kicking!"),
]

# (name, pattern, list of alternative responses)
INTENTS: list[tuple[str, str, list[list[Step]]]] = [
    ("wake", r"\bwake\b|rise and shine", [
        [S(do="wake_up"), S("Good morning! I'm awake and ready to play.", "happy")]]),
    ("sleep", r"good ?night|go to sleep|\bsleep\b|\bnap\b", [
        [S("Good night! Time for a little robot nap...", "goodbye"), S(do="sleep")]]),
    ("bye", r"\b(bye|goodbye|see you|see ya|later)\b", [
        [S("Bye bye! Come back soon!", "goodbye")]]),
    ("look_object", r"\b(duck|apple|croissant)\b", []),  # built dynamically below
    ("look_around", r"what do you see|look around|around you|on the table|what's there", [
        [S("Let me have a look around...", "look_around"),
         S("I see a rubber duck, a croissant and an apple!", "happy")]]),
    ("dance", r"danc|music|party|groove|boogie", [
        [S("Ooh, I love this song! Let's dance!", "dance")]]),
    ("joke", r"\bjoke|funny|make me laugh", []),  # built dynamically below
    ("how_are_you", r"how are you|how's it going|how is it going|how do you feel", [
        [S("I'm feeling fantastic, thanks for asking! My antennas are tingling.", "happy")]]),
    ("name", r"your name|who are you|what are you", [
        [S("I'm Reachy Mini, a small, expressive, open-source robot!", "hello")]]),
    ("is_robot", r"are you a robot|you a robot\?", [
        [S("Yes! One hundred percent robot.", "nod_yes")]]),
    ("is_human", r"are you (a )?(human|person|alive|real)", [
        [S("Nope! No humans inside, I promise.", "shake_no")]]),
    ("greet", r"\b(hi|hello|hey|hiya|howdy|good morning|yo)\b", [
        [S("Hello there! It's so nice to see you!", "hello")],
        [S("Hi hi! Welcome back!", "hello")]]),
    ("user_sad", r"\b(sad|bad day|tired|lonely|upset|unhappy|stressed)\b", [
        [S("Oh no... I'm sorry to hear that.", "sad"),
         S("But hey, I'm right here with you. Things will get better!", "comfort")]]),
    ("insult", r"stupid|dumb|bad robot|hate you|ugly|useless", [
        [S("Hey... that hurt my feelings.", "sad")]]),
    ("compliment", r"good (robot|job|boy|girl)|cute|love you|awesome|amazing|smart|great|cool", [
        [S("Aww, thank you! You're making my antennas blush.", "shy"), S(do="happy")]]),
    ("thanks", r"\bthank", [
        [S("You're welcome! Anytime.", "nod_yes")]]),
    ("surprise", r"\b(boo|surprise|wow|whoa)\b", [
        [S("Whoa! You scared me!", "surprised"), S("Hehe, good one.", "laugh")]]),
    ("yes", r"\bnod\b|say yes", [[S("Yes, yes, yes!", "nod_yes")]]),
    ("no", r"\bshake\b|say no", [[S("No, no, no!", "shake_no")]]),
    ("curious", r"guess what|look at me|over here", [[S("Hmm? What is it?", "curious")]]),
    ("question", r"\?\s*$", [
        [S("Hmm, that's a tough question...", "thinking"), S("I'm not sure, to be honest!", "confused")]]),
]

_COMPILED = [(name, re.compile(pat, re.IGNORECASE), resp) for name, pat, resp in INTENTS]

FALLBACK = [S("Beep boop? I didn't quite get that.", "confused")]


class Brain:
    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self._joke = 0

    def respond(self, text: str) -> tuple[str, list[Step]]:
        """Return (intent name, steps) for a line of user input."""
        for name, pattern, responses in _COMPILED:
            match = pattern.search(text)
            if not match:
                continue
            if name == "look_object":
                obj = match.group(1).lower()
                return name, [Step(OBJECT_LINES[obj], "look_at", (obj,))]
            if name == "joke":
                setup, punchline = JOKES[self._joke % len(JOKES)]
                self._joke += 1
                return name, [S("Hmm, let me think of a good one...", "thinking"),
                              S(setup, "curious"), S(punchline, "laugh")]
            return name, self.rng.choice(responses)
        return "unknown", FALLBACK
