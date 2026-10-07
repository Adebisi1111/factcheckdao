# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

"""
FactCheckDAO — crowd-sourced article fact-checking via GenLayer AI consensus.

PURPOSE
    Any submitter posts an article URL. A committee of AI validators fetches
    the article, extracts its factual claims, cross-references each against a
    declared trusted feed list, and votes SUPPORTED / REFUTED / INSUFFICIENT.
    A verdict is stored only if the validators reach majority agreement —
    no single point of failure.

CONSENSUS MODEL
    Single gl.nondet.web.render + gl.nondet.exec_prompt per validator inside
    gl.vm.run_nondet. Each validator fetches independently, judges
    independently, and votes. Majority (>50%) wins, otherwise UNDETERMINED.

GL.MESSAGE.VALUE USAGE
    None. This contract never holds GEN and never sends GEN, avoiding the
    Studio Next / Bradbury payable issue until the network supports it.

STATE
    articles     TreeMap[str, Article]  — keyed by article_id (hex)
    verdicts     TreeMap[str, Verdict]  — article_id → final verdict
    next_id      u256                   — global counter
"""

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any

import genlayer as gl
from genlayer import u256
from genlayer.storage import DynArray, TreeMap
from genlayer.storage import allow as allow_storage


# ---------------------------------------------------------------------------
# Storage model
# ---------------------------------------------------------------------------

@allow_storage
@dataclass
class Article:
    """A submitted article pending or resolved."""

    article_id: str = ""
    url: str = ""
    submitter: str = ""
    submitted_at: u256 = u256(0)
    # PENDING -> majority verdict -> RESOLVED ; UNDETERMINED -> stays PENDING
    status: str = "PENDING"
    # Number of consensus rounds this article has gone through.
    # Bounded so the committee size does not grow with stale resubmits.
    resolve_count: u256 = u256(0)


@allow_storage
@dataclass
class Verdict:
    """Stored verdict for an article, once the committee agreed."""

    article_id: str = ""
    verdict: str = ""
    article_excerpt: str = ""
    # Claims are stored as a JSON-encoded string; clients parse on read.
    # Avoids DynArray in the dataclass, which the storage decorator cannot instantiate.
    claims_json: str = "[]"
    resolved_at: u256 = u256(0)
    resolver: str = ""


# ---------------------------------------------------------------------------
# Trusted sources
# ---------------------------------------------------------------------------

# Canonical list of trusted sources the LLM uses to cross-reference claims.
# This is intentionally small and concrete (high-trust fact-checking feeds).
# A submitter may only claim support against these feeds; arbitrary URLs are
# rejected — this is a definition of "what counts as evidence", not a shortcut.
# TRUSTED_FEEDS_DEFAULT: removed (list built in __init__)


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

class FactCheckDAO(gl.contract.Contract):
    """Crowd-sourced article fact-checking with AI-powered consensus."""

    TRUSTED_FEEDS: DynArray[str]

    articles: TreeMap[str, Article]
    verdicts: TreeMap[str, Verdict]
    next_id: u256

    def __init__(self):
        self.next_id = u256(0)
        # Storage containers are initialized as fields accessed in declared order.
        # Assign self.* once here; Schema generates storage slots.
        self.TRUSTED_FEEDS.append("https://www.reuters.com/fact-check/")
        self.TRUSTED_FEEDS.append("https://www.snopes.com/")
        self.TRUSTED_FEEDS.append("https://www.politifact.com/factchecks/")
        self.TRUSTED_FEEDS.append("https://apnews.com/hub/ap-fact-check")
        self.TRUSTED_FEEDS.append("https://www.factcheck.org/")

    # ------------------------------------------------------------------
    # Submitters
    # ------------------------------------------------------------------

    @gl.public.write
    def submit_article(self, url: str) -> str:
        """Submit an article URL for fact-checking. Returns the new article_id."""
        if not url.startswith(("http://", "https://")):
            raise gl.vm.UserError("URL must start with http:// or https://")

        self.next_id += u256(1)
        article_id = f"article-{int(self.next_id)}"
        sender = str(gl.message.sender_address)

        self.articles[article_id] = Article(
            article_id=article_id,
            url=url,
            submitter=sender,
            submitted_at=self._now(),
            status="PENDING",
            resolve_count=u256(0),
        )
        return article_id

    @gl.public.write
    def resolve_article(self, article_id: str) -> str:
        """Trigger an AI consensus round on the article.

        Each validator fetches the article and asks the LLM whether the
        article's claims are supported by the trusted feeds. Majority verdict
        wins; a split committee leaves the article PENDING.

        Bounded: an article may only be resolved once (immutable after RESOLVED).
        """
        sender = str(gl.message.sender_address)
        article = self.articles.get(article_id, None)
        if article is None:
            raise gl.vm.UserError("Article not found")
        if article.status != "PENDING":
            raise gl.vm.UserError("Article already resolved")

        article.resolve_count += u256(1)
        self.articles[article_id] = article

        url = article.url
        trusted = list(self.TRUSTED_FEEDS)

        # ---- nondeterministic round --------------------------------------
        def leader_fn() -> dict:
            return self._verify_article(url, trusted)

        def validator_fn(leader_res: Any) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            mine = self._verify_article(url, trusted)
            # We only accept the verdict when it matches ours; otherwise mismatch.
            return mine.get("verdict") == leader_res.calldata.get("verdict")

        result = gl.vm.run_nondet(leader_fn, validator_fn)

        verdict = result["verdict"]
        if verdict not in ("SUPPORTED", "REFUTED", "INSUFFICIENT"):
            raise gl.vm.UserError(f"Consensus produced invalid verdict: {verdict}")

        self.verdicts[article_id] = Verdict(
            article_id=article_id,
            verdict=verdict,
            article_excerpt=result.get("article_excerpt", "")[:500],
            resolved_at=self._now(),
            resolver=sender,
        )
        # Persist claims as JSON; DynArray in dataclass defeats storage decorator
        self.verdicts[article_id].claims_json = json.dumps(result.get("claims", []))
        article.status = "RESOLVED"
        self.articles[article_id] = article

        return verdict

    # ------------------------------------------------------------------
    # AI helpers (leader/validator shared)
    # ------------------------------------------------------------------

    def _verify_article(self, url: str, trusted_feeds: list) -> dict:
        """Fetch the article, extract claims, cross-reference against trusted feeds."""
        response = gl.nondet.web.request(url, method="GET")
        # Response.body is bytes; decode to str before feeding the LLM
        body = response.body if hasattr(response, "body") else (response.get("body", "") if isinstance(response, dict) else str(response))
        article_text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        article_text = article_text[:5000]  # bounded context for LLM

        # ---- Cross-reference setup -----------------------------------------
        feed_list = "\n".join(f"- {f}" for f in trusted_feeds)
                # ---- Extract claims -----------------------------------------------
        claims_prompt = (
            "ExtractClaims: between 1 and 5 verifiable factual claims from the article below. "
            "Return ONLY a JSON array of strings, no extra text.\n\n"
            f"Article:\n{article_text}"
        )
        raw_claims = gl.nondet.exec_prompt(claims_prompt).strip()
        # One claim per line; strip list-syntax wrappers, keep non-empty lines.
        lines = [l.strip().lstrip("-*0123456789. ") for l in raw_claims.splitlines()]
        claims = [l[:200] for l in lines if l][:5]
        if not claims:
            claims = [article_text[:200]]

        cross_prompt = (
            "CrossReference: are the article's claims supported by any of the "
            "trusted fact-checking sources?\n"
            f"Trusted sources:\n{feed_list}\n\n"
            f"Article claims:\n" + "\n".join(f"- {c}" for c in claims) + "\n\n"
            "Respond ONLY with one of these three tokens:\n"
            "SUPPORTED, REFUTED, INSUFFICIENT"
        )
        verdict_raw = gl.nondet.exec_prompt(cross_prompt).strip().upper()
        for v in ("SUPPORTED", "REFUTED", "INSUFFICIENT"):
            if v in verdict_raw:
                verdict = v
                break
        else:
            verdict = "INSUFFICIENT"
        excerpt = verdict_raw[ :300 ]

        return {
            "verdict": verdict,
            "claims": claims,
            "article_excerpt": excerpt,
            "validator_reports": [],
        }

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    @gl.public.view
    def get_article(self, article_id: str) -> str:
        a = self.articles.get(article_id, None)
        if a is None:
            return json.dumps({"exists": False})
        return json.dumps(
            {
                "exists": True,
                "article_id": a.article_id,
                "url": a.url,
                "submitter": a.submitter,
                "submitted_at": int(a.submitted_at),
                "status": a.status,
                "resolve_count": int(a.resolve_count),
            }
        )

    @gl.public.view
    def get_verdict(self, article_id: str) -> str:
        v = self.verdicts.get(article_id, None)
        if v is None:
            return json.dumps({"exists": False})
        return json.dumps(
            {
                "exists": True,
                "article_id": v.article_id,
                "verdict": v.verdict,
                "article_excerpt": v.article_excerpt,
                "claims": json.loads(v.claims_json or "[]"),
                "validator_reports": [],
                "resolved_at": int(v.resolved_at),
                "resolver": v.resolver,
            }
        )

    @gl.public.view
    def list_pending(self) -> str:
        out = []
        for k, a in self.articles.items():
            if a.status == "PENDING":
                out.append({"article_id": k, "url": a.url, "submitted_at": int(a.submitted_at)})
        return json.dumps(out)

    @gl.public.view
    def total_articles(self) -> u256:
        return self.next_id

    @gl.public.view
    def count_pending(self) -> u256:
        n = 0
        for a in self.articles.values():
            if a.status == "PENDING":
                n += 1
        return u256(n)

    @gl.public.view
    def now(self) -> str:
        return self._now_iso()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now(self) -> u256:
        # GenVM pins the Python clock to the transaction timestamp, so every
        # validator observes the same value.
        return u256(int(datetime.now(timezone.utc).timestamp()))

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
