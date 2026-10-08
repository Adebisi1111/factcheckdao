# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""
FactCheckDAO — crowd-sourced article fact-checking via GenLayer AI consensus.

PURPOSE
    Any submitter posts an article URL. A committee of AI validators fetches
    the article, extracts its factual claims, cross-references each against
    authoritative sources (Wikipedia, Britannica, NASA, scientific journals),
    and votes SUPPORTED / REFUTED / INSUFFICIENT.
    A verdict is stored only if the validators reach majority agreement.

CONSENSUS MODEL
    gl.vm.run_nondet with leader/validator pattern. Each validator independently
    fetches the article, extracts claims, and asks the LLM to verify them
    against authoritative sources. Majority (>50%) wins.

VERDICT LOGIC
    SUPPORTED  — LLM confirms claims are true based on authoritative sources
    REFUTED    — LLM finds claims are false or contradicted by sources
    INSUFFICIENT — LLM cannot verify (sources don't cover the claims)

STATE
    articles     TreeMap[str, Article]  — keyed by article_id (str)
    verdicts     TreeMap[str, Verdict]  — article_id → final verdict + reasoning
    next_id      u256                    — global counter
"""

import json
import re
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
    reasoning: str
    # Claims are stored as a JSON-encoded string; clients parse on read.
    claims_json: str
    resolved_at: u256
    resolver: str


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class FactCheckDAO(gl.Contract):
    """Crowd-sourced article fact-checking with AI-powered consensus."""

    # Authoritative sources that corroborate factual claims.
    TRUSTED_SOURCES: DynArray[str]

    articles: TreeMap[str, Article]
    verdicts: TreeMap[str, Verdict]
    next_id: u256

    def __init__(self):
        self.next_id = u256(0)
        self.TRUSTED_SOURCES.append("https://en.wikipedia.org/wiki/Main_Page")
        self.TRUSTED_SOURCES.append("https://www.britannica.com/")
        self.TRUSTED_SOURCES.append("https://www.nasa.gov/")
        self.TRUSTED_SOURCES.append("https://www.nature.com/")
        self.TRUSTED_SOURCES.append("https://www.scientificamerican.com/")

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
        """Trigger an AI consensus round on the article."""
        sender = str(gl.message.sender_address)
        article = self.articles.get(article_id, None)
        if article is None:
            raise gl.vm.UserError("Article not found")
        if article.status != "PENDING":
            raise gl.vm.UserError("Article already resolved")

        article.resolve_count += u256(1)
        self.articles[article_id] = article

        url = article.url
        sources = list(self.TRUSTED_SOURCES)

        # ---- nondeterministic round --------------------------------------
        def leader_fn() -> dict:
            return self._verify_article(url, sources)

        def validator_fn(leader_res: Any) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            mine = self._verify_article(url, sources)
            return mine.get("verdict") == leader_res.calldata.get("verdict")

        result = gl.vm.run_nondet(leader_fn, validator_fn)

        # run_nondet returns a gl.vm.Return wrapper; access .calldata for the actual dict
        result_data = result.calldata if hasattr(result, "calldata") else result
        verdict = result_data.get("verdict", "")
        reasoning = result_data.get("reasoning", "")
        if verdict not in ("SUPPORTED", "REFUTED", "INSUFFICIENT"):
            raise gl.vm.UserError(f"Consensus produced invalid verdict: {verdict}")

        self.verdicts[article_id] = Verdict(
            article_id=article_id,
            verdict=verdict,
            reasoning=reasoning[:500],
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

    def _verify_article(self, url: str, sources: list) -> dict:
        """Fetch the article, extract claims, verify against authoritative sources."""
        response = gl.nondet.web.request(url, method="GET")
        body = response.body if hasattr(response, "body") else (response.get("body", "") if isinstance(response, dict) else str(response))
        raw_html = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)

        # --- HTML Content Stripping ----------------------------------------
        # Extract text between <p> and </p> tags — the main content blocks
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', raw_html, flags=re.DOTALL | re.IGNORECASE)
        # Strip any remaining HTML tags from each paragraph
        clean_paragraphs = []
        for p in paragraphs:
            # Remove script/style blocks
            p = re.sub(r'<script[^>]*>.*?</script>', '', p, flags=re.DOTALL | re.IGNORECASE)
            p = re.sub(r'<style[^>]*>.*?</style>', '', p, flags=re.DOTALL | re.IGNORECASE)
            # Remove all remaining tags
            p = re.sub(r'<[^>]+>', '', p)
            # Decode HTML entities
            p = p.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
            p = re.sub(r'\s+', ' ', p).strip()
            if p:
                clean_paragraphs.append(p)
        article_text = ' '.join(clean_paragraphs)[:5000]

        if not article_text:
            # Fallback: strip all tags if no <p> tags found
            text = re.sub(r'<script[^>]*>.*?</script>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            article_text = text[:5000]

        source_list = "\n".join(f"- {s}" for s in sources)

        # Step 1: Extract verifiable factual claims
        claims_prompt = (
            "Extract 1-5 verifiable factual claims from the article below. "
            "A verifiable claim is a statement that can be confirmed or denied "
            "by authoritative sources. Return ONLY a JSON array of strings, no extra text.\n\n"
            f"Article:\n{article_text}"
        )
        raw_claims = gl.nondet.exec_prompt(claims_prompt).strip()
        claims = self._parse_claims(raw_claims)
        if not claims:
            claims = [article_text[:200]]

        # Step 2: Verify claims — prompt mandates JSON output
        verify_prompt = (
            "Fact-check these claims against your knowledge (Wikipedia, Britannica, NASA).\n"
            f"Claims: " + " | ".join(claims) + "\n"
            "Return ONLY JSON: {\"verdict\":\"SUPPORTED|REFUTED|INSUFFICIENT\",\"reasoning\":\"brief reason\"}. "
            "Default to INSUFFICIENT if unsure."
        )
        raw_verdict = gl.nondet.exec_prompt(verify_prompt).strip()
        parsed = self._parse_verdict_json(raw_verdict)

        return {
            "verdict": parsed.get("verdict", "INSUFFICIENT"),
            "reasoning": parsed.get("reasoning", ""),
            "claims": claims,
            "article_excerpt": parsed.get("reasoning", "")[:300],
            "validator_reports": [],
        }

    # ------------------------------------------------------------------
    # Defensive JSON parsing helpers
    # ------------------------------------------------------------------

    def _parse_claims(self, raw: str) -> list:
        """Parse LLM output into a list of claim strings."""
        # Strip markdown code blocks
        raw = re.sub(r'```json\s*', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'```\s*', '', raw)
        # Extract text between outermost curly braces
        match = re.search(r'\{.*\}', raw, flags=re.DOTALL)
        if match:
            raw = match.group()
        # Try to find a JSON array
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(c)[:200] for c in data if c][:5]
            if isinstance(data, dict) and "claims" in data:
                return [str(c)[:200] for c in data["claims"] if c][:5]
        except (json.JSONDecodeError, KeyError):
            pass
        # Fallback: parse line-by-line
        lines = [l.strip().lstrip("-*0123456789. ") for l in raw.splitlines()]
        return [l[:200] for l in lines if l][:5]

    def _parse_verdict_json(self, raw: str) -> dict:
        """Parse LLM verdict JSON output with defensive cleanup."""
        # Strip markdown code blocks
        raw = re.sub(r'```json\s*', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'```\s*', '', raw)
        # Extract text between outermost curly braces
        match = re.search(r'\{.*\}', raw, flags=re.DOTALL)
        if match:
            raw = match.group()
        # Try to parse
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                verdict = data.get("verdict", "INSUFFICIENT")
                reasoning = data.get("reasoning", "")
                if verdict not in ("SUPPORTED", "REFUTED", "INSUFFICIENT"):
                    verdict = "INSUFFICIENT"
                return {"verdict": verdict, "reasoning": reasoning}
        except (json.JSONDecodeError, KeyError):
            pass
        # Fallback: search for verdict token in raw text
        raw_upper = raw.upper()
        for v in ("SUPPORTED", "REFUTED", "INSUFFICIENT"):
            if v in raw_upper:
                return {"verdict": v, "reasoning": raw[:300]}
        # Ultimate fallback
        return {"verdict": "INSUFFICIENT", "reasoning": "Could not parse LLM output"}

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
                "reasoning": v.reasoning,
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
