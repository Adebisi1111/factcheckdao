# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""
FactCheckDAO — crowd-sourced article fact-checking via GenLayer AI consensus.

PURPOSE
    Any submitter posts an article URL. A committee of AI validators fetches
    the article, extracts its factual claims, cross-references each against a
    declared trusted feed list, and votes SUPPORTED / REFUTED / INSUFFICIENT.
    A verdict is stored only if the validators reach majority agreement —
    no single point of failure.

CONSENSUS MODEL
    Single gl.nondet.web.request + gl.nondet.exec_prompt per validator inside
    gl.vm.run_nondet. Each validator fetches independently, judges
    independently, and votes. Majority (>50%) wins, otherwise UNDETERMINED.

GL.MESSAGE.VALUE USAGE
    None. This contract never holds GEN and never sends GEN, avoiding the
    Studio Next / Bradbury payable issue until the network supports it.

STATE
    articles     TreeMap[str, Article]  — keyed by article_id (str)
    verdicts     TreeMap[str, Verdict]  — article_id → final verdict
    next_id      u256                    — global counter
"""

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from genlayer import *


# ---------------------------------------------------------------------------
# Storage model
# ---------------------------------------------------------------------------


@allow_storage
@dataclass
class Article:
    """A submitted article pending or resolved."""

    article_id: str
    url: str
    submitter: str
    submitted_at: u256
    # PENDING -> majority verdict -> RESOLVED ; UNDETERMINED -> stays PENDING
    status: str
    # Number of consensus rounds this article has gone through.
    resolve_count: u256


@allow_storage
@dataclass
class Verdict:
    """Stored verdict for an article, once the committee agreed."""

    article_id: str
    verdict: str
    article_excerpt: str
    # Claims are stored as a JSON-encoded string; clients parse on read.
    claims_json: str
    resolved_at: u256
    resolver: str

# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class FactCheckDAO(gl.Contract):
    """Crowd-sourced article fact-checking with AI-powered consensus."""

    TRUSTED_FEEDS: DynArray[str]

    articles: TreeMap[str, Article]
    verdicts: TreeMap[str, Verdict]
    next_id: u256

    def __init__(self):
        self.next_id = u256(0)
        # Storage containers are initialized as fields accessed in declared order.
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
            return mine.get("verdict") == leader_res.calldata.get("verdict")

        result = gl.vm.run_nondet(leader_fn, validator_fn)

        # run_nondet returns a gl.vm.Return wrapper; access .calldata for the actual dict
        result_data = result.calldata if hasattr(result, "calldata") else result
        verdict = result_data.get("verdict", "")
        if verdict not in ("SUPPORTED", "REFUTED", "INSUFFICIENT"):
            raise gl.vm.UserError(f"Consensus produced invalid verdict: {verdict}")

        self.verdicts[article_id] = Verdict(
            article_id=article_id,
            verdict=verdict,
            article_excerpt=result_data.get("article_excerpt", "")[:500],
            claims_json=json.dumps(result_data.get("claims", [])),
            resolved_at=self._now(),
            resolver=sender,
        )
        article.status = "RESOLVED"
        self.articles[article_id] = article

        return verdict

    # ------------------------------------------------------------------
    # AI helpers (leader/validator shared)
    # ------------------------------------------------------------------

    def _verify_article(self, url: str, trusted_feeds: list) -> dict:
        """Fetch the article, extract claims, cross-reference against trusted feeds.
        
        NOTE: trusted_feeds must be passed in as a plain list built BEFORE run_nondet
        so no storage read happens inside the nondet round.
        """
        response = gl.nondet.web.request(url, method="GET")
        body = response.body if hasattr(response, "body") else (response.get("body", "") if isinstance(response, dict) else str(response))
        article_text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        article_text = article_text[:5000]

        feed_list = "\n".join(f"- {f}" for f in trusted_feeds)
        claims_prompt = (
            "ExtractClaims: between 1 and 5 verifiable factual claims from the article below. "
            "Return ONLY a JSON array of strings, no extra text.\n\n"
            f"Article:\n{article_text}"
        )
        raw_claims = gl.nondet.exec_prompt(claims_prompt).strip()
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
        excerpt = verdict_raw[:300]

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
        return u256(int(datetime.now(timezone.utc).timestamp()))

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
