"""Cheap spoken-language safeguard for the English-only v0.1 product.

Whisper profiles ``fast`` (``base.en``) and ``balanced`` (``small.en``) are
English-only models: fed a non-English video they emit English-looking garbage
with high confidence and no warning. This module only inspects the transcript
that was already produced — no extra model, no download, no extra pass — and
answers one question: "is this probably not English?".

The detector is deliberately conservative. A false warning is cheap, but a
wrong *pass* is what the safeguard exists to prevent, so several independent
signals must agree before a transcript is flagged:

* a high share of non-Latin script characters (CJK, Cyrillic, Arabic, …),
* diacritics and non-ASCII letters that English does not use,
* a high share of words that are neither English stopwords nor stopword
  neighbours, measured over enough words to be meaningful,
* a very small vocabulary with almost no English function words.

All thresholds are documented next to the constants so they can be retuned
from the report table in ``docs/QA_REPORT.md``.
"""

import logging
from dataclasses import dataclass

from openclip.analyze.scoring import STOPWORDS
from openclip.core.models import Transcript

log = logging.getLogger(__name__)

#: Characters outside Latin-1 that English transcripts practically never
#: contain; their presence is strong evidence of another script.
_NON_LATIN_RANGES = (
    (0x0370, 0x03FF),  # Greek
    (0x0400, 0x04FF),  # Cyrillic
    (0x0590, 0x05FF),  # Hebrew
    (0x0600, 0x06FF),  # Arabic
    (0x0900, 0x097F),  # Devanagari
    (0x0E00, 0x0E7F),  # Thai
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
)

#: Above this share of non-Latin characters in a transcript of meaningful
#: length, the language is not English (a stray emoji or name never reaches it).
_NON_LATIN_SHARE = 0.10

#: Above this share of non-ASCII letters (accented Latin) the text is unlikely
#: to be English. Spanish/French/German transcripts land far above it.
_NON_ASCII_LETTER_SHARE = 0.06

#: Above this share of words missing from the English stopword neighbourhood,
#: the transcript is probably not English. English speech sits near 0.55-0.75
#: depending on topic; the gap for other languages is wide.
_UNKNOWN_WORD_SHARE = 0.90

#: High-frequency function words per language, grouped in clusters. A single
#: cluster crossing :data:`_FOREIGN_STOPWORD_SHARE` is evidence of that language;
#: mixing clusters does not add up, so stray loanwords never accumulate.
#: Only words that are not also common English function words are listed.
_FOREIGN_STOPWORDS: dict[str, frozenset[str]] = {
    "es": frozenset(
        """
        que los las una unos unas por para con esto esta este eso esos pero muy
        como cuando donde porque tambi\u00e9n hay ser tiene tienes hacer todo todos
        nada algo gente puede quiero s\u00ed se\u00f1or aqu\u00ed est\u00e1 est\u00e1n somos
        """.split()
    ),
    "fr": frozenset(
        """
        les des une dans pour avec nous vous est pas sur plus tout tous fait
        faire quand parce aussi \u00eatre avoir cette ces dont mais elle ils elles
        qui quoi tr\u00e8s m\u00eame comme sans deux jour tr\u00e8s chose
        """.split()
    ),
    "de": frozenset(
        """
        der die das und nicht ist mit sich auch auf ein eine einen einem einer
        zu von im den dem des wird werden hat haben dass wenn weil aber noch
        nur sehr kann muss soll viel mehr schon immer jeder jede jedes
        """.split()
    ),
    "it": frozenset(
        """
        che non per sono della delle degli anche questo questa quello quella
        molto quando perch\u00e9 essere avere fatto fare pi\u00f9 tutti tutte loro
        """.split()
    ),
    "pt": frozenset(
        """
        n\u00e3o uma um para com isso este esta esse essa muito quando porque
        tamb\u00e9m tem fazer todo todos gente pode quero aqui est\u00e1 s\u00e3o
        """.split()
    ),
    "nl": frozenset(
        """
        het een van niet zijn dat met voor ook maar nog veel kan moet worden
        deze dit hebben heeft altijd iedereen omdat toen dan
        """.split()
    ),
}

#: Share of one language's function words above which that language is likely.
_FOREIGN_STOPWORD_SHARE = 0.08

#: Never judge on fewer content words than this: short clips are too noisy.
_MIN_WORDS = 40

#: A foreign function-word cluster may fire on shorter text than that, but
#: needs at least this many words and this many cluster hits.
_MIN_FOREIGN_WORDS = 12
_MIN_FOREIGN_HITS = 3

#: Very small vocabulary plus almost no stopwords is a hallmark of an
#: English-only model transcribing another language.
_MIN_DISTINCT_WORDS = 12
_MIN_STOPWORD_HITS = 2

#: The warning shown to users. Kept short: it lands in a job message and in the
#: CLI output, and it must never stop the run.
LANGUAGE_WARNING = (
    "This video does not look like English. OpenClip v0.1 only targets "
    "English-language videos, so transcripts, captions and clip titles may be "
    "wrong. The 'quality' whisper profile handles more languages."
)


@dataclass(frozen=True)
class LanguageVerdict:
    """Outcome of the transcript language safeguard."""

    probably_english: bool
    """False when the evidence says the speech is probably not English."""

    reason: str | None = None
    """Short machine-facing reason, ``None`` when probably English."""

    @property
    def warning(self) -> str | None:
        """User-facing warning text, or ``None`` when no warning is due."""
        return None if self.probably_english else LANGUAGE_WARNING


def _transcript_text(transcript: Transcript) -> str:
    """Join segment text, preferring words when a segment has no text."""
    parts: list[str] = []
    for segment in transcript.segments:
        if segment.text.strip():
            parts.append(segment.text.strip())
        elif segment.words:
            parts.append(" ".join(w.text for w in segment.words))
    return " ".join(parts)


def _is_non_latin(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _NON_LATIN_RANGES)


def _share(count: int, total: int) -> float:
    return count / total if total else 0.0


def _looks_english(text: str) -> LanguageVerdict:
    """Classify one block of text; see the module docstring for the signals."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return LanguageVerdict(True, None)

    non_latin = sum(1 for c in letters if _is_non_latin(c))
    if _share(non_latin, len(letters)) >= _NON_LATIN_SHARE:
        return LanguageVerdict(False, "non_latin_script")

    non_ascii = sum(1 for c in letters if ord(c) > 127)
    if _share(non_ascii, len(letters)) >= _NON_ASCII_LETTER_SHARE:
        return LanguageVerdict(False, "non_ascii_letters")

    words = [w.strip(".,!?;:\"'()[]").lower() for w in text.split()]
    words = [w for w in words if w and w.isalpha()]
    if len(words) < _MIN_FOREIGN_WORDS:
        return LanguageVerdict(True, None)

    # A concentrated foreign function-word cluster is strong evidence even on
    # a short clip, so it is checked before the length guard below.
    foreign = _foreign_language(words)
    if foreign is not None:
        return LanguageVerdict(False, f"looks_like_{foreign}")

    if len(words) < _MIN_WORDS:
        return LanguageVerdict(True, None)

    stopword_hits = sum(1 for w in words if w in STOPWORDS)
    if len(set(words)) < _MIN_DISTINCT_WORDS and stopword_hits < _MIN_STOPWORD_HITS:
        return LanguageVerdict(False, "no_english_function_words")

    unknown = len(words) - stopword_hits
    if _share(unknown, len(words)) >= _UNKNOWN_WORD_SHARE and stopword_hits < len(words) * 0.05:
        return LanguageVerdict(False, "few_english_stopwords")
    return LanguageVerdict(True, None)


def _foreign_language(words: list[str]) -> str | None:
    """Return the language whose function-word cluster dominates, if any."""
    for language, vocabulary in _FOREIGN_STOPWORDS.items():
        hits = sum(1 for w in words if w in vocabulary)
        if hits >= _MIN_FOREIGN_HITS and _share(hits, len(words)) >= _FOREIGN_STOPWORD_SHARE:
            return language
    return None


def check_transcript_language(transcript: Transcript) -> LanguageVerdict:
    """Return whether ``transcript`` is probably English.

    Never raises: an empty transcript is "not enough evidence", which passes.
    """
    try:
        verdict = _looks_english(_transcript_text(transcript))
    except Exception:  # noqa: BLE001 - a safeguard must never break a job
        log.warning("language check failed; continuing without a verdict", exc_info=True)
        return LanguageVerdict(True, None)
    declared = (transcript.language or "").split("-")[0].strip().lower()
    if verdict.probably_english and declared not in ("", "en"):
        # Whisper reported a non-English language for a text that looks English:
        # trust the report only as a warning, never as a failure.
        return LanguageVerdict(False, f"reported_language_{declared}")
    if verdict.probably_english:
        return verdict
    log.info("Transcript looks non-English (%s); keeping the run alive", verdict.reason)
    return verdict
