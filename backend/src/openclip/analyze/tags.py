"""Vocabulary rules shared by the heuristic and LLM hashtag generators.

Hashtags are the only place where OpenClip writes short public-facing topic
labels, so the rules are deliberately conservative: engagement bait, the
product's own name, generic video-platform vocabulary and spoken filler are
never topics and must never become tags. Curated by hand; extend only with
words that carry no topic information.
"""

#: Engagement bait: asks for attention without naming a subject.
ENGAGEMENT_TAGS = frozenset(
    {"fyp", "foryou", "foryoupage", "viral", "trending", "xyzbca", "xyz", "follow", "like"}
)

#: Product and platform meta vocabulary: never a topic of the source video.
META_TAGS = frozenset(
    """
    openclip openclipapp open clip clips clipping clip video videos shorts short
    reels reel tiktok youtube instagram facebook twitter podcast podcasts
    episode episodes channel channels subscribe subscriber creator creators
    content watch watching upload uploads download downloads
    """.split()
)

#: Spoken filler and vague nouns: transcribed often, never a useful topic.
FILLER_TAGS = frozenset(
    """
    um uh erm hmm okay ok yeah yep yup nope right anyway anyways
    basically actually literally gonna wanna kinda sorta
    stuff thing things something somebody someone everybody everyone nobody
    maybe probably definitely really very much many lot lots kind sort bit
    """.split()
)

#: Everything banned from hashtags (flat union for cheap membership tests).
BANNED_TAGS = ENGAGEMENT_TAGS | META_TAGS | FILLER_TAGS


def is_banned_tag(word: str) -> bool:
    """Return True when a lowercase tag word must never be emitted."""
    return word in BANNED_TAGS
