"""How a model pattern is matched against an aircraft name, and how
matches rank.

A pattern is a prefix-anchored regex, or an exact string, matched against
the name the sim reports (the TITLE on MSFS).  When several patterns match,
the most specific one names the aircraft and its settings are applied last:

1. an exact pin beats a pattern: ``^Name$`` can match nothing but Name.
   Bare ``Name`` is not one - matching is anchored at the start only, so
   it takes Name and anything after it, exactly as ``Name.*`` does, and
   the two rank alike;
2. then the longer run of literal text the pattern pins down, so
   ``737-600.*`` beats ``737.*``;
3. then the pattern that pins that text down at the start, so ``C-17.*``
   beats ``.*C-17.*`` - both require the same four characters, but only one
   requires them first;
4. otherwise the shipped file wins: a tie between a pattern of the user's
   and a curated one goes to defaults.xml, so a newly shipped profile is
   noticed rather than shadowed; within one file the earlier entry wins.

Specificity is inferred from the pattern's syntax, which is a proxy for what
its author meant, so it is measured on what the pattern REQUIRES: a leading
``^`` adds nothing, a trailing ``$`` is what makes a pin exact, and a leading
wildcard or optional group is skipped rather than counted, since neither
constrains anything on its own.
"""
import re
from typing import Iterable, Optional, Sequence, Tuple, TypeVar

_META = re.compile(r"[.^$*+?{}\[\]\\|()]")
#: Constructs that can open a pattern without requiring anything of the name:
#: ``.*``/``.+``, an optional group ``(...)?`` and an optional class ``[...]?``.
#: Ranking a pattern on what follows them beats ranking it on nothing.
_LEADING_OPTIONAL = re.compile(r"^(?:\.[*+]|\([^)]*\)[?*]|\[[^\]]*\][?*])+")

T = TypeVar("T")


def pattern_matches(pattern: str, name: str) -> bool:
    """Whether ``pattern`` claims the aircraft ``name``: an exact match, or
    a regex match from the start; a bad regex matches nothing."""
    pattern = (pattern or "").strip()
    if not pattern or not name:
        return False
    if pattern == name:
        return True
    try:
        return re.match(pattern, name) is not None
    except re.error:
        return False


def literal_prefix_len(regex: str) -> int:
    m = _META.search(regex)
    return len(regex) if m is None else m.start()


def strip_anchors(pattern: str) -> str:
    """A pattern without its anchors.  Matching is anchored at the start
    anyway, so a leading ``^`` says nothing, and a trailing ``$`` pins the
    end rather than widening anything - counting either as a metacharacter
    would rank ``^Name$``, the most specific pattern there is, below every
    wildcard."""
    p = (pattern or "").strip()
    if p.startswith("^"):
        p = p[1:]
    if p.endswith("$") and not p.endswith("\\$"):
        p = p[:-1]
    return p


def pins_end(pattern: str) -> bool:
    """Whether the pattern ends in a real ``$``, so it can match nothing
    beyond the text it names.  Matching is anchored at the start already,
    so this is the one anchor that changes what a pattern claims."""
    p = (pattern or "").strip()
    return p.endswith("$") and not p.endswith("\\$")


def same_claim(a: str, b: str) -> bool:
    """Whether two patterns name the same aircraft: the same text once the
    anchors and a trailing ``.*`` are set aside, since matching runs from
    the start and ``Name`` already takes anything after it."""
    def claim(p):
        core = strip_anchors(p)
        return core[:-2] if core.endswith(".*") and not core.endswith("\\.*") else core
    return claim(a) == claim(b)


def required_literal(pattern: str) -> Tuple[str, bool]:
    """The run of literal text a pattern pins down, and whether it pins it
    down at the start.  ``Name.*`` requires 'Name' first; ``.*Name.*``
    requires the same text but anywhere, which constrains less."""
    core = strip_anchors(pattern)
    rest = _LEADING_OPTIONAL.sub("", core)
    return rest[:literal_prefix_len(rest)], rest == core


def specificity(pattern: str, name: str) -> Tuple[int, int, int]:
    """Rank of a matching pattern; higher wins.  Meaningless for a pattern
    that does not match, so test ``pattern_matches`` first."""
    core = strip_anchors(pattern)
    literal, at_start = required_literal(pattern)
    exact = core == (name or "") and pins_end(pattern)
    return (int(exact), len(literal), int(at_start))


def rank_matches(candidates: Iterable[Tuple[str, T]], name: str) -> list:
    """The matching ``(pattern, payload)`` pairs in ascending specificity,
    document order preserved among equals, so that applying them in this
    order lets the most specific speak last."""
    matched = [(p, payload) for p, payload in candidates if pattern_matches(p, name)]
    return sorted(matched, key=lambda item: specificity(item[0], name))


def best_pattern(patterns: Sequence[str], name: str) -> Optional[str]:
    """The most specific matching pattern, the earliest in ``patterns`` on a tie."""
    ranked = rank_matches(((p, i) for i, p in enumerate(patterns)), name)
    if not ranked:
        return None
    top = specificity(ranked[-1][0], name)
    for p, _ in ranked:
        if specificity(p, name) == top:
            return p
    return ranked[-1][0]


def first_match(patterns: Sequence[str], name: str) -> Optional[str]:
    """The rule before ranking: the first matching pattern in document order."""
    for p in patterns:
        if pattern_matches(p, name):
            return p
    return None
