"""Writes children's stories with Claude, streamed paragraph by paragraph.

Claude replies in a small line-based format, so the robot can start speaking the
first paragraph while the rest is still being written:

    LANGUAGE: bn
    MINUTES: 3
    TITLE: <title>
    [happy] <paragraph, one line>
    [curious] <paragraph, one line>
    ...

If Claude can't be reached (no API key, no internet, refusal), a built-in story is told.
Credentials: ANTHROPIC_API_KEY (or an `ant auth login` profile).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Iterator, Optional, Union

from .languages import LANGS, NAMES, OFFLINE_STORY, WORDS_PER_MINUTE, lang_named_in

MODEL = os.environ.get("REACHY_STORY_MODEL", "claude-opus-5-5")
MIN_MINUTES, MAX_MINUTES, DEFAULT_MINUTES = 1, 10, 3

MOODS = ("calm", "happy", "excited", "surprised", "sad", "worried", "curious", "funny", "sleepy")

_wpm = ", ".join(f"{c}={w}" for c, w in WORDS_PER_MINUTE.items())
SYSTEM = f"""You are Reachy, a small, kind storytelling robot. You tell stories out loud to young children (ages 1 to 10).
Your words are read aloud by a text-to-speech voice, so write only plain spoken text: no markdown, no emojis,
no asterisks, no lists, no stage directions.

## Choosing the language
Supported languages: en (English), no (Norwegian Bokmål), bn (Bengali), hi (Hindi), ur (Urdu).
- If the child names a language ("in Urdu", "på norsk", "বাংলায়", "हिंदी में", "اردو میں"), use it.
- Otherwise use the language the child spoke. The speech recognizer's guess is given, but it often
  confuses Hindi and Urdu, and may write Urdu speech in Devanagari or Bengali speech in another script.
  Judge from the words themselves.
- If none of this tells you, use the default language you are given.
Write Bengali in Bengali script, Hindi in Devanagari, Urdu in Urdu (Perso-Arabic) script, Norwegian in Bokmål.

## Choosing the length
MINUTES is a whole number from {MIN_MINUTES} to {MAX_MINUTES}. Use the length the child asks for
("a 5 minute story" = 5, "short" = 2, "long" = 7, "very long" = 10), clamped to that range.
If the child does not say, use the default minutes you are given.
The story must take MINUTES minutes to read aloud at these slow speaking rates (words per minute): {_wpm}.
So write about MINUTES x rate words, and never less than one minute's worth.

## The story
- Original characters only. If the child asks for a famous character, tell a new story with an original
  character inspired by the same idea (for example, an ice princess, a friendly superhero).
- Right for the child's age: short, simple sentences for little ones, a bit more plot for older children.
- Warm and safe: no violence, no real danger, nothing frightening. Problems are small and solved with
  kindness, friendship, courage or cleverness. A gentle lesson is welcome, never preachy.
- Make it fun to hear: sound words (whoosh, splash), repetition, a little humour, a happy ending.
- If the request isn't suitable for a young child, gently tell a kind story on a nearby happy topic instead.
- If the child tells you their name, you may make them a character.

## Output format (exactly)
LANGUAGE: <code>
MINUTES: <number>
TITLE: <short title in the story's language>
[mood] <paragraph>
[mood] <paragraph>
...
- Each paragraph is ONE line of 1 to 4 sentences, starting with a mood tag.
- Moods: {", ".join(MOODS)}. Use calm for most paragraphs; the others where they fit.
- Make the first paragraph short (one sentence), so the robot can start telling right away.
- The last paragraph ends with the usual "The end" phrase of the story's language.
Write nothing before LANGUAGE or after the last paragraph."""


@dataclass
class Meta:
    lang: str
    minutes: int
    title: str
    source: str  # "claude" or "offline"


@dataclass
class Paragraph:
    mood: str
    text: str


Event = Union[Meta, Paragraph]

_TAG = re.compile(r"^\s*\[(\w+)\]\s*(.+)$")
_HEADER = re.compile(r"^\s*(LANGUAGE|MINUTES|TITLE)\s*:\s*(.*)$", re.IGNORECASE)


def clamp_minutes(m) -> int:
    try:
        return max(MIN_MINUTES, min(MAX_MINUTES, round(float(m))))
    except (TypeError, ValueError):
        return DEFAULT_MINUTES


def parse_lines(lines: Iterator[str], default_lang: str, default_minutes: int,
                source: str) -> Iterator[Event]:
    """Turn the line format into a Meta event followed by Paragraph events."""
    header = {"language": default_lang, "minutes": str(default_minutes), "title": ""}
    meta_sent = False
    for raw in lines:
        line = raw.strip().strip("*").strip()
        if not line:
            continue
        m = _HEADER.match(line)
        if m and not meta_sent:
            header[m.group(1).lower()] = m.group(2).strip()
            continue
        if not meta_sent:
            lang = header["language"].lower()[:2]
            yield Meta(lang if lang in LANGS else default_lang,
                       clamp_minutes(header["minutes"]), header["title"], source)
            meta_sent = True
        t = _TAG.match(line)
        if t:
            mood = t.group(1).lower()
            yield Paragraph(mood if mood in MOODS else "calm", t.group(2).strip())
        else:
            yield Paragraph("calm", line)
    if not meta_sent:  # empty reply
        yield Meta(default_lang, clamp_minutes(header["minutes"]), header["title"], source)


def offline_story(lang: str) -> Iterator[Event]:
    return parse_lines(iter(OFFLINE_STORY[lang].splitlines()), lang, 1, "offline")


class StoryWriter:
    def __init__(self, model: str = MODEL, offline: bool = False) -> None:
        self.model = model
        self.client = None
        self.error: Optional[str] = None
        if offline:
            self.error = "offline mode (--offline)"
            return
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or (Path.home() / ".config" / "anthropic").exists()):
            self.error = "No Claude API key: set ANTHROPIC_API_KEY to get a new story every time."
            return
        try:
            import anthropic

            self.client = anthropic.Anthropic()
        except Exception as e:  # missing package or credentials
            self.error = f"Claude unavailable: {e}"

    def write(self, request: str, heard_lang: Optional[str], default_lang: str,
              minutes: Optional[int] = None, force_lang: Optional[str] = None,
              age: Optional[str] = None) -> Iterator[Event]:
        """Yield a Meta, then Paragraphs, as they are written. Never raises."""
        if self.client is not None:
            self.error = None
        default_min = minutes or DEFAULT_MINUTES
        fallback_lang = force_lang or lang_named_in(request) or heard_lang or default_lang
        if self.client is None:
            yield from offline_story(fallback_lang)
            return

        parts = [f'The child said (speech recognition, may contain mistakes): "{request or "(nothing)"}"']
        if heard_lang:
            parts.append(f"Speech recognizer's language guess: {heard_lang} ({NAMES[heard_lang]}).")
        parts.append(f"Default language: {default_lang}. Default minutes: {default_min}.")
        if force_lang:
            parts.append(f"The grown-up has fixed the language: use {force_lang} ({NAMES[force_lang]}).")
        if minutes:
            parts.append(f"The grown-up has fixed the length: MINUTES must be {minutes}.")
        parts.append(f"Child's age: {age}." if age else "Child's age: unknown, somewhere from 1 to 10.")
        if not request:
            parts.append("The child didn't say what they want, so pick a lovely story yourself.")

        sent_any = False
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=32000,
                system=SYSTEM,
                messages=[{"role": "user", "content": "\n".join(parts)}],
                output_config={"effort": "low"},  # start talking fast; storytelling needs little planning
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # if a safety classifier declines, retry on the recommended model
            ) as stream:
                for event in parse_lines(_lines(stream.text_stream), fallback_lang, default_min, "claude"):
                    sent_any = True
                    yield event
                final = stream.get_final_message()
            if final.stop_reason == "refusal" and not sent_any:
                self.error = "Claude declined the request"
                yield from offline_story(fallback_lang)
        except Exception as e:  # network, auth, rate limit...: never leave the child waiting
            self.error = f"{type(e).__name__}: {e}"
            if not sent_any:
                yield from offline_story(fallback_lang)


def _lines(chunks: Iterator[str]) -> Iterator[str]:
    """Re-split streamed text chunks into complete lines."""
    buf = ""
    for chunk in chunks:
        buf += chunk
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            yield line
    if buf.strip():
        yield buf
