"""Per-machine memory for profile matching: which pattern named each
aircraft the last time it loaded, and how the user answered when one of
their own patterns collided with a shipped one.

Kept in a small JSON file beside the user config rather than inside it: a
record is not a user choice, must not bump the config's mtime (every
instance reloads its aircraft on that), and must not travel with a config
that is copied, shared or exported.  Only the master instance writes it,
from the main thread.

A collision is remembered per pattern pair, not per aircraft: it is the
two patterns that compete, and the same pair covers every aircraft both
match.  A later defaults.xml that ships a different pattern is a new pair
and asks afresh.

An answer is stored with a fingerprint of the shipped profile as it stood
when it was given.  A decline covers that version of it and lapses when a
later defaults.xml changes what the profile does, so a shipped setting
that would never otherwise reach the user is offered again.  A merge is
permanent: the user is already on the built-in, and later changes to it
reach them without anyone being asked.
"""
import hashlib
import json
import logging
import os
from typing import Optional

import telemffb.globals as G

FILENAME = "match_history.json"
MERGED, DECLINED = "merged", "declined"


def path() -> str:
    """The history file, beside the user config; '' before the config path
    is known (nothing is read or written then)."""
    userconfig = getattr(G, "userconfig_path", "") or ""
    if not userconfig:
        return ""
    return os.path.join(os.path.dirname(os.path.abspath(userconfig)), FILENAME)


def _load() -> dict:
    p = path()
    if not p or not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logging.warning(f"Match history unreadable, starting over: {e}")
        return {}
    # Anything but the shape written here (an older layout, a hand edit)
    # is not worth interpreting: the file is a convenience, not a record.
    if not isinstance(data, dict) or not {"matches", "collisions"} >= set(data):
        return {}
    return data


def _save(data: dict) -> None:
    p = path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, p)


def last_match(sim: str, aircraft_name: str) -> Optional[str]:
    """The pattern that named ``aircraft_name`` on ``sim`` at its last
    load, or None when it has not been seen."""
    if not aircraft_name:
        return None
    return ((_load().get("matches") or {}).get(sim) or {}).get(aircraft_name) or None


def record_match(sim: str, aircraft_name: str, pattern: str) -> None:
    """Remember which pattern named an aircraft.  A record that says what
    the file already says is not rewritten."""
    if not path() or not aircraft_name or not pattern:
        return
    data = _load()
    matches = data.setdefault("matches", {}).setdefault(sim, {})
    if matches.get(aircraft_name) == pattern:
        return
    matches[aircraft_name] = pattern
    data.setdefault("collisions", {})
    _save(data)


def fingerprint(rows: list) -> str:
    """A short digest of a shipped profile, from whatever the caller reads
    off it.  Every row is a tuple of strings; the order they arrive in does
    not matter."""
    if not rows:
        return ""
    text = "\n".join(sorted("|".join(row) for row in rows))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def resolution(sim: str, user_pattern: str, curated_pattern: str,
               shipped: Optional[str] = None) -> Optional[str]:
    """How the user answered for this pair - MERGED or DECLINED - or None
    while the question is still open.

    ``shipped`` is the built-in's fingerprint now.  Pass it and a decline
    given against a different one has lapsed, so the pair reads as open
    again.  A merge holds whatever the built-in has since become."""
    if not user_pattern or not curated_pattern:
        return None
    pairs = (_load().get("collisions") or {}).get(sim) or {}
    answer = (pairs.get(user_pattern) or {}).get(curated_pattern)
    if not isinstance(answer, dict):
        return None
    how = answer.get("how")
    if how == DECLINED and shipped is not None and answer.get("shipped", "") != shipped:
        return None
    return how if how in (MERGED, DECLINED) else None


def resolve(sim: str, user_pattern: str, curated_pattern: str, how: str,
            shipped: str = "") -> None:
    """Record the answer for a pair, so it is not asked again.  ``shipped``
    is the built-in's fingerprint when the answer was given."""
    if not path() or not user_pattern or not curated_pattern or how not in (MERGED, DECLINED):
        return
    data = _load()
    data.setdefault("matches", {})
    pairs = data.setdefault("collisions", {}).setdefault(sim, {}).setdefault(user_pattern, {})
    answer = {"how": how, "shipped": shipped or ""}
    if pairs.get(curated_pattern) == answer:
        return
    pairs[curated_pattern] = answer
    _save(data)


def _declines(data: dict):
    """(sim, user_pattern, curated_pattern) for every recorded decline."""
    for sim, users in (data.get("collisions") or {}).items():
        for user, pairs in users.items():
            for curated, answer in pairs.items():
                if isinstance(answer, dict) and answer.get("how") == DECLINED:
                    yield sim, user, curated


def has_declines() -> bool:
    """Whether any collision has been declined on this machine, which is
    what there is to give back."""
    return any(True for _ in _declines(_load()))


def forget_declines() -> None:
    """Forget every decline, so those offers come back.  A merge is not an
    answer to give back: it changed the configuration, and for a copy it is
    the only thing keeping the still-standing source pattern from being
    offered again.  Which pattern named each aircraft is kept too: that is
    a record of what happened, not a choice the user made."""
    if not path():
        return
    data = _load()
    gone = list(_declines(data))
    if not gone:
        return
    for sim, user, curated in gone:
        del data["collisions"][sim][user][curated]
    data.setdefault("matches", {})
    _save(data)


def reset() -> None:
    """Forget every record, as a config reset does for the config."""
    p = path()
    if p and os.path.isfile(p):
        os.remove(p)
