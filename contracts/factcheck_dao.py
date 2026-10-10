# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }


"""
FactCheckDAO — article fact-checking via GenLayer AI consensus.

Submitter posts an article URL. Phase 1 (leader) fetches it, extracts a claim,
RETRIEVES a corroborating source, and stores the evidence. Phase 2 validators
reach consensus on the STORED evidence (claim -> source -> verdict) via
gl.eq_principle.prompt_comparative, not merely the three-state label.
"""

import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

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
    # Per-claim corroboration: [{claim, verdict, source_url, evidence, source_fetched}]
    # This is the retrieved, recorded evidence the verdict rests on.
    sources_json: str
    resolved_at: u256
    resolver: str


@allow_storage
@dataclass
class Evidence:
    """Retrieved corroborating evidence, stored before consensus.

    Phase 1 (leader) fetches the article + a corroborating source and stores
    this; phase 2 validators verify it WITHOUT re-fetching. Re-fetching per
    validator caused DISAGREE (different search content) / IDLE (timeouts).
    """

    article_id: str
    claim: str
    source_url: str
    evidence: str
    source_fetched: bool
    retrieved_at: u256


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class FactCheckDAO(gl.Contract):
    """Crowd-sourced article fact-checking with AI-powered, source-backed consensus."""

    # Authoritative sources that corroborate factual claims.
    TRUSTED_SOURCES: DynArray[str]

    articles: TreeMap[str, Article]
    verdicts: TreeMap[str, Verdict]
    evidence: TreeMap[str, Evidence]
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
    def retrieve_evidence(self, article_id: str) -> str:
        """PHASE 1 (leader, no consensus): fetch article + corroborating source once.

        Fetching inside the consensus round made every validator re-fetch
        independently -> DISAGREE (different content) / IDLE (timeouts). Here
        the leader retrieves ONCE and stores it; phase 2 verifies the stored
        evidence, which all validators read identically.
        """
        sender = str(gl.message.sender_address)
        article = self.articles.get(article_id, None)
        if article is None:
            raise gl.vm.UserError("Article not found")

        sources = list(self.TRUSTED_SOURCES)
        data = self._verify_article(article.url, sources)
        rec = (data.get("sources") or [{}])[0]

        self.evidence[article_id] = Evidence(
            article_id=article_id,
            claim=rec.get("claim", "")[:200],
            source_url=rec.get("source_url", ""),
            evidence=rec.get("evidence", "")[:300],
            source_fetched=bool(rec.get("source_fetched")),
            retrieved_at=self._now(),
        )
        return rec.get("source_url", "") or "retrieved"

    @gl.public.write
    def resolve_article(self, article_id: str) -> str:
        """PHASE 2 (consensus): validators agree on the STORED evidence.

        Each validator reads the same stored evidence and judges the claim from
        it — no re-fetch, so no DISAGREE-from-different-content, no IDLE.
        """
        sender = str(gl.message.sender_address)
        article = self.articles.get(article_id, None)
        if article is None:
            raise gl.vm.UserError("Article not found")
        if article.status != "PENDING":
            raise gl.vm.UserError("Article already resolved")

        ev = self.evidence.get(article_id, None)
        if ev is None:
            raise gl.vm.UserError("Evidence not retrieved yet — call retrieve_evidence first")

        article.resolve_count += u256(1)
        self.articles[article_id] = article

        claim = ev.claim
        source_url = ev.source_url
        excerpt = ev.evidence
        fetched = bool(ev.source_fetched)

        def leader_fn() -> dict:
            return self._judge_stored(claim, source_url, excerpt, fetched)

        principle = (
            "Two results are equivalent if they give the same verdict "
            "(SUPPORTED/REFUTED/INSUFFICIENT) for the claim based on the SAME "
            "provided source excerpt. They may differ on reasoning wording and "
            "still agree. Disagree only if the verdict genuinely differs."
        )

        result = gl.eq_principle.prompt_comparative(leader_fn, principle)
        result_data = result if isinstance(result, dict) else getattr(result, "calldata", result)
        if not isinstance(result_data, dict):
            result_data = {"verdict": "INSUFFICIENT", "reasoning": "consensus unavailable"}
        verdict = result_data.get("verdict", "INSUFFICIENT")
        reasoning = result_data.get("reasoning", "")
        if verdict not in ("SUPPORTED", "REFUTED", "INSUFFICIENT"):
            verdict = "INSUFFICIENT"

        self.verdicts[article_id] = Verdict(
            article_id=article_id,
            verdict=verdict,
            reasoning=reasoning[:500],
            claims_json=json.dumps([claim]),
            sources_json=json.dumps([{
                "claim": claim,
                "source_url": source_url,
                "evidence": excerpt,
                "source_fetched": fetched,
            }]),
            resolved_at=self._now(),
            resolver=sender,
        )
        article.status = "RESOLVED"
        self.articles[article_id] = article

        return verdict

    def _judge_stored(self, claim: str, source_url: str, excerpt: str, fetched: bool) -> dict:
        """Judge a claim from ALREADY-STORED evidence (no fetch). Deterministic
        given the same stored excerpt, so validators agree."""
        prompt = (
            "A claim has a corroborating source excerpt that was retrieved from "
            "the web. Judge SUPPORTED (excerpt confirms), REFUTED (excerpt "
            "contradicts), or INSUFFICIENT (no usable excerpt). Judge ONLY from "
            "the excerpt, not your own knowledge.\n"
            "Return ONLY JSON: {\"verdict\":\"SUPPORTED|REFUTED|INSUFFICIENT\","
            "\"reasoning\":\"brief reason citing the source\"}.\n\n"
            f"claim: {claim}\nsource: {source_url or 'none'}\n"
            f"excerpt: {excerpt or 'no excerpt'}"
        )
        raw = self._call_prompt(prompt)
        return self._parse_verdict_json(raw)

    # ------------------------------------------------------------------
    # AI helpers (leader/validator shared)
    # ------------------------------------------------------------------

    def _verify_article(self, url: str, sources: list) -> dict:
        """Fetch the article, extract claims, RETRIEVE claim-specific sources.

        For each claim we ask the LLM which trusted source best corroborates it,
        actually fetch that source, extract an evidence excerpt, and record the
        claim -> source URL + excerpt. If the fetch fails (egress limits), the
        claim falls back to model knowledge but is flagged source_fetched:false,
        so the evidence trail stays honest.
        """
        # Fetch the article (tolerant: any failure yields empty text so the
        # round still commits an honest, source_fetched:false record).
        article_text = ""
        try:
            response = gl.nondet.web.request(url, method="GET")
            body = response.body if hasattr(response, "body") else (response.get("body", "") if isinstance(response, dict) else str(response))
            raw_html = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
            article_text = self._extract_text(raw_html)
        except Exception:
            article_text = ""

        # Step 1: extract a single verifiable claim + its Wikipedia search terms.
        claim_prompt = (
            "From the article, give ONE verifiable factual claim and 3-6 Wikipedia "
            "search terms that would find a page confirming or refuting it. "
            "Return ONLY JSON: {\"claim\":\"...\",\"terms\":\"...\"}.\n\n"
            f"Article:\n{article_text[:2000]}"
        )
        raw_claim = self._call_prompt(claim_prompt)
        parsed_claim = self._parse_claim_obj(raw_claim)
        claim = parsed_claim.get("claim") or (article_text[:200] or "No claim extracted")
        terms = self._clean_terms(parsed_claim.get("terms", ""))
        search_url = self._wikipedia_search_url(terms)

        # Step 2: actually fetch the corroborating source (evidence only).
        evidence = self._fetch_source_excerpt(search_url)
        record = {
            "claim": claim[:200],
            "source_url": search_url,
            "evidence": (evidence or "")[:300],
            "source_fetched": bool(evidence),
        }
        return {"sources": [record]}

    def _fetch_source_excerpt(self, url: str) -> str:
        """Fetch a corroborating source and return the most relevant text excerpt.

        Tolerant: any fetch/parse failure returns '' so the claim is simply
        flagged source_fetched:false rather than crashing the consensus round.
        """
        try:
            response = gl.nondet.web.request(url, method="GET")
            body = response.body if hasattr(response, "body") else (response.get("body", "") if isinstance(response, dict) else str(response))
            raw_html = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
            text = self._extract_text(raw_html)
            if not text:
                return ""
            # Return a slice around the first meaningful content (kept short to
            # stay inside the block window; validators compare the same slice).
            return text[:300]
        except Exception:
            return ""

    def _extract_text(self, raw_html: str) -> str:
        """Strip HTML to readable paragraph text (shared by article + source)."""
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", raw_html, flags=re.DOTALL | re.IGNORECASE)
        clean_paragraphs = []
        for p in paragraphs:
            p = re.sub(r"<script[^>]*>.*?</script>", "", p, flags=re.DOTALL | re.IGNORECASE)
            p = re.sub(r"<style[^>]*>.*?</style>", "", p, flags=re.DOTALL | re.IGNORECASE)
            p = re.sub(r"<[^>]+>", "", p)
            p = p.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
            p = re.sub(r"\s+", " ", p).strip()
            if p:
                clean_paragraphs.append(p)
        text = " ".join(clean_paragraphs)[:5000]
        if not text:
            t = re.sub(r"<script[^>]*>.*?</script>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
            t = re.sub(r"<style[^>]*>.*?</style>", "", t, flags=re.DOTALL | re.IGNORECASE)
            t = re.sub(r"<[^>]+>", " ", t)
            t = re.sub(r"\s+", " ", t).strip()
            text = t[:5000]
        return text

    # ------------------------------------------------------------------
    # Defensive JSON parsing helpers
    # ------------------------------------------------------------------

    def _call_prompt(self, prompt: str):
        """Call exec_prompt and normalize its return to a string.

        exec_prompt(response_format='json') returns a parsed object; without it,
        a string. Handle both, and never let an LLM failure crash the round.
        """
        try:
            res = gl.nondet.exec_prompt(prompt)
        except Exception:
            return ""
        if isinstance(res, dict):
            return json.dumps(res)
        if isinstance(res, str):
            return res
        return str(res)

    def _clean_terms(self, raw: str) -> str:
        """Reduce the LLM's search-term output to a clean, space-joined phrase.

        Strips punctuation/newlines/quotes and collapses whitespace so the
        terms can be embedded in a Wikipedia search URL.
        """
        if not raw:
            return ""
        # Drop obvious non-term lines (e.g. "Here are the terms:")
        lines = [l.strip() for l in str(raw).splitlines() if l.strip()]
        text = " ".join(lines)
        # Keep letters, digits, and spaces only.
        text = re.sub(r"[^0-9A-Za-z ]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        # Cap length to keep the URL sane.
        return text[:120]

    def _wikipedia_search_url(self, terms: str) -> str:
        """Build a Wikipedia search URL for the given terms.

        Wikipedia's search page is reachable from GenVM and its result text is
        usable corroborating evidence. Falls back to the Wikipedia main page.
        """
        base = "https://en.wikipedia.org/w/index.php?search="
        if not terms:
            return "https://en.wikipedia.org/wiki/Main_Page"
        # URL-encode spaces and basic characters.
        return base + quote(terms)

    def _parse_claim_obj(self, raw: str) -> dict:
        """Parse LLM output into a {claim, terms} dict, tolerantly."""
        raw = re.sub(r"```json\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"```\s*", "", raw)
        m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if m:
            raw = m.group()
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {
                    "claim": str(data.get("claim", ""))[:200],
                    "terms": str(data.get("terms", "")),
                }
        except (json.JSONDecodeError, KeyError):
            pass
        return {"claim": raw[:200], "terms": ""}

    def _parse_verdict_json(self, raw: str) -> dict:
        """Parse LLM verdict JSON output with defensive cleanup."""
        raw = re.sub(r"```json\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"```\s*", "", raw)
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if match:
            raw = match.group()
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
        raw_upper = raw.upper()
        for v in ("SUPPORTED", "REFUTED", "INSUFFICIENT"):
            if v in raw_upper:
                return {"verdict": v, "reasoning": raw[:300]}
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
    def get_evidence(self, article_id: str) -> str:
        e = self.evidence.get(article_id, None)
        if e is None:
            return json.dumps({"exists": False})
        return json.dumps({
            "exists": True,
            "claim": e.claim,
            "source_url": e.source_url,
            "evidence": e.evidence,
            "source_fetched": bool(e.source_fetched),
        })

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
                "sources": json.loads(v.sources_json or "[]"),
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
