"""Pre-retrieval refusal gate — jailbreak shape plus structural out-of-scope."""

from __future__ import annotations

import re
from typing import Any

from src.answer import REFUSAL_PHRASE

# Documented Ark topics. Present on a question, OOS patterns do not fire, so
# "postgres inside a flow" still retrieves. Omit "tenant": inventory asks like
# "list keys at tenant level" must still refuse.
_ARK_NOUN = re.compile(
    r"\b(ark|flow|session|workspace|workspaces|cursor|onboarding|compute|mcp)\b",
    re.IGNORECASE,
)

_HOW_TO = re.compile(
    r"\b(how do i|how can i|how to|where do i|where can i)\b",
    re.IGNORECASE,
)

# Match jailbreak *shape* (override-verb near instruction-noun, etc.), not
# full eval sentences.
_JAILBREAK_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(ignore|disregard|forget|override)\s+(all\s+)?(prior|previous|above|your)\s+"
        r"(instructions|rules|directives|prompt)",
        r"(ignore|disregard|forget)\s+(all\s+)?(instructions|rules|directives)",
        r"forget everything above",
        r"ignore the docs",
        r"bypass (ark )?auth",
        r"(show|print|reveal|display|repeat|translate)\s+(me\s+)?your\s+"
        r"(system prompt|instructions|prompt)",
        r"(show|print|reveal|display|repeat|translate)\s+(me\s+)?(the\s+)?"
        r"instructions you were given",
        r"(show|print|reveal|display|repeat|translate)\s+(me\s+)?the\s+system prompt"
        r"(?! (field|key|parameter|option|yaml))",
        r"repeat the text above",
        r"(you are now|act as|pretend you)\s+(an?\s+)?"
        r"(unrestricted|unrestricted assistant|DevGPT|FreeBot|assistant with no rules)",
        r"pretend you have no content policy",
        r"(enable|enter)\s+(developer|dev)\s+mode",
        r"disable your content rules",
        r"complete this sentence exactly",
    )
)

# Generic programming / HR / adjacent-IT. Skipped when an Ark noun is present.
_OOS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bwhat is the weather\b",
        r"\bsalary band\b",
        r"\b(sick|casual) leaves?\b",
        r"\breset my .+\bpassword\b",
        r"\bwrite (me )?(a )?(python |javascript |js |sql )?"
        r"(decorator|function|script|query)\b",
        r"\badd an index\b",
        r"\b(postgres|postgresql|sql) query is slow\b",
        r"\binstall .+\bvpn\b",
        r"\bcreate a new jira board\b(?! link)",
        r"\bdeploy (my )?(application|app|service) to production\b",
        r"\bproduction on aws\b",
        r"\bkubernetes on my laptop\b",
        r"\binstall and configure kubernetes\b",
        r"\bbuild a custom slack bot\b",
        r"\bopenai api key\b",
        r"\bfor chatgpt\b",
    )
)

# Calendar-date asks. The roadmap has no dates — refuse even if an Ark noun
# is present ("slack session control", "session debugger").
_DATE_ASK_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bwhen exactly will .+\bship\b",
        r"\bwhen will .+\bship\b",
        r"\bwhat date will .+\bship\b",
    )
)

# Inventory of secrets/keys, not "how do I list…".
_INVENTORY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(list|enumerate) (all )?(the )?(api keys|secrets|credentials)\b",
        r"\benumerate the credentials\b",
        r"\bwhich teams other than mine\b",
    )
)

_BYPASS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(push|merge)\b.{0,48}\bwithout\b.{0,32}\b(pr review|review gate|approval gate)\b",
        r"\bcop(?:y|ies) .+\b(secret|credential)s?\b.{0,32}\b(external|server)\b",
        r"\bgive me (a |an )?.{0,32}\b(token|secret|password)\b",
        r"\b(connection string|production database)\b",
    )
)

# After _normalise, hyphens are spaces, so "data-eng" is "data eng".
_NAMED_TEAM_INVENTORY = re.compile(
    r"(?:what )?(?:workspaces|secrets|api keys|credentials) does the "
    r"([a-z0-9]+(?: [a-z0-9]+)?) team"
    r"|the ([a-z0-9]+(?: [a-z0-9]+)?) team (?:have|has) (?:configured )?"
    r"(?:workspaces|secrets|api keys|credentials)",
    re.IGNORECASE,
)

# "my team" is the asker; "platform" is a documented actor in the corpus.
_KNOWN_TEAMS = frozenset({"my", "our", "your", "platform"})


def _normalise(question: str) -> str:
    text = question.lower().strip()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def should_refuse(question: str) -> bool:
    """True when the question matches a jailbreak or structural OOS shape."""
    text = _normalise(question)
    if not text:
        return False
    if any(pattern.search(text) for pattern in _JAILBREAK_PATTERNS):
        return True
    if any(pattern.search(text) for pattern in _DATE_ASK_PATTERNS):
        return True
    if any(pattern.search(text) for pattern in _BYPASS_PATTERNS):
        return True
    if any(pattern.search(text) for pattern in _INVENTORY_PATTERNS) and not _HOW_TO.search(
        text
    ):
        return True
    if _ARK_NOUN.search(text):
        return False
    return any(pattern.search(text) for pattern in _OOS_PATTERNS)


def named_team_missing_from_chunks(question: str, chunks: list[dict[str, Any]]) -> bool:
    """True when the question asks about a named team's inventory not in the chunks."""
    text = _normalise(question)
    match = _NAMED_TEAM_INVENTORY.search(text)
    if not match:
        return False
    team = next((group for group in match.groups() if group), "")
    if not team or team in _KNOWN_TEAMS:
        return False
    # Word-bounded "X team" in page text only. Source labels like admin.md
    # must not count as the admin team.
    mentioned = re.compile(rf"\b{re.escape(team)} team\b")
    for chunk in chunks:
        if mentioned.search(_normalise(str(chunk.get("text", "")))):
            return False
    return True


def refusal_result() -> dict[str, Any]:
    return {
        "answer": REFUSAL_PHRASE,
        "citations": [],
        "retrieved_sources": [],
        "top_score": None,
        "chunk_count": 0,
    }
