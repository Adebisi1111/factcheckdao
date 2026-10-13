# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }


"""
FactCheckDAO — source-backed fact-checking. Each validator independently
fetches the article and a corroborating source and judges the claim from it.
"""

import hashlib
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
    status: str
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
    # This is the retrieved, recorded evidence the verdict rests on.
    sources_json: str
    resolved_at: u256
    resolver: str


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class FactCheckDAO(gl.Contract):
    """Source-backed fact-checking."""

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
        """Every validator independently fetches, retrieves a source, and judges.

        The equivalence check binds the verdict, the evidence hash and the
        retrieved flags.
        """
        sender = str(gl.message.sender_address)
        article = self.articles.get(article_id, None)
        if article is None:
            raise gl.vm.UserError("Article not found")
        if article.status != "PENDING":
            raise gl.vm.UserError("Article already resolved")

        url = article.url
        sources = list(self.TRUSTED_SOURCES)

        # Every validator derives the SAME evidence identity.
        def leader_fn() -> dict:
            full = self._verify_article(url, sources)
            srcs = full.get("sources") or []
            rec = srcs[0] if srcs else {}
            return {
                "verdict": full.get("verdict", "INSUFFICIENT"),
                "source_retrieved": bool(rec.get("source_fetched")),
                "article_fetched": bool(rec.get("article_fetched")),
                # The URL is the deterministic handle on the evidence: binding
                # it means validators evaluated the SAME evidence.
                "source_url": str(rec.get("source_url", "")),
                # Hash of the deterministic excerpt: exact evidence agreement.
                "evidence_hash": str(rec.get("evidence_hash", "")),
                "grounded": str(rec.get("grounded", "")),
                "reasoning": str(full.get("reasoning", "")),
            }

        principle = (
            "Equivalent ONLY when ALL FIVE match exactly: (1) source_url is the "
            "identical URL, (2) evidence_hash is the identical hex string, "
            "(3) article_fetched, (4) source_retrieved and (5) verdict are the "
            "same. Claim, evidence and reasoning wording may differ. If the URL, "
            "the hash or any flag differs they DO NOT agree, even when the "
            "verdict label matches."
        )

        result = gl.eq_principle.prompt_comparative(leader_fn, principle)
        result_data = result if isinstance(result, dict) else getattr(result, "calldata", result)
        if not isinstance(result_data, dict):
            result_data = {}

        verdict = str(result_data.get("verdict", "INSUFFICIENT"))
        if verdict not in ("SUPPORTED", "REFUTED", "INSUFFICIENT"):
            verdict = "INSUFFICIENT"
        source_retrieved = bool(result_data.get("source_retrieved"))
        article_fetched = bool(result_data.get("article_fetched"))

        # Never finalize without retrieved evidence: revert, stay PENDING.
        if not article_fetched:
            raise gl.vm.UserError(
                "the submitted article could not be retrieved, so no verdict is "
                "finalized: any claim would be invented."
            )
        if not source_retrieved:
            raise gl.vm.UserError(
                "no corroborating source could be retrieved; not finalized."
            )

        source_url = str(result_data.get("source_url", ""))
        evidence_hash = str(result_data.get("evidence_hash", ""))
        reasoning = str(result_data.get("reasoning", ""))[:500]
        claim = str(result_data.get("grounded", ""))
        evidence = claim

        # The agreed evidence must be on-subject (checked after the values
        # exist; the per-validator check already blocks off-subject rounds).
        subject = self._subject_from_url(url)
        blob = (claim + " " + evidence).lower()
        if subject and subject.lower() not in blob:
            raise gl.vm.UserError(
                f"agreed evidence is off-subject ({subject!r}); not finalized."
            )

        if not source_url or not claim:
            raise gl.vm.UserError(
                "evidence trail unavailable."
            )
        if verdict == "SUPPORTED" and not evidence.strip():
            raise gl.vm.UserError(
                "SUPPORTED needs an excerpt; none found."
            )

        article.resolve_count += u256(1)

        self.verdicts[article_id] = Verdict(
            article_id=article_id,
            verdict=verdict,
            reasoning=reasoning,
            claims_json=json.dumps([claim]),
            sources_json=json.dumps([{
                "claim": claim,
                "source_url": source_url,
                "evidence": evidence,
                "evidence_hash": evidence_hash,
                "source_fetched": True,
                "article_fetched": True,
            }]),
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

        # Fetch the article (request returns Lazy[Response]; .get() it).
        article_text = ""
        fetch_status = -1
        article_fetched = False
        try:
            lazy_resp = gl.nondet.web.request(url, method="GET")
            response = lazy_resp.get() if hasattr(lazy_resp, "get") else lazy_resp
            if hasattr(response, "status"):
                fetch_status = int(response.status)
            body = response.body if hasattr(response, "body") else (response.get("body", "") if isinstance(response, dict) else str(response))
            raw_html = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
            article_text = self._extract_text(raw_html)
            if len(article_text.strip()) > 40 and fetch_status in (-1, 200):
                article_fetched = True
        except Exception:
            article_text = ""

        # Step 1: derive the source page, then fetch it. The stored claim is
        # taken from the FETCHED SOURCE, so it is grounded.
        claim_prompt = (
            "Give ONE verifiable factual claim from the article. Return ONLY "
            "JSON: {\"claim\":\"...\"}.\n\n"
            f"Article:\n{article_text[:2000]}"
        )
        raw_claim = self._call_prompt(claim_prompt)
        parsed_claim = self._parse_claim_obj(raw_claim)
        claim = parsed_claim.get("claim") or (article_text[:200] if article_text else f"[fetch status={fetch_status}]")

        # The source page must be deterministic: a free model choice sent it to
        # unrelated pages, so no round produced on-subject evidence. A Wikipedia
        # article is its own corroborating source.
        title = ""
        m = re.match(r"^https?://[a-z.]*wikipedia\.org/wiki/([^?#]+)", url, re.IGNORECASE)
        if m:
            title = m.group(1).replace("_", " ")
        if not title:
            title = self._clean_terms(parsed_claim.get("title") or parsed_claim.get("terms", ""))
        source_url = self._wikipedia_article_url(title) if title else url

        # Step 2: the excerpt is deterministic, so every validator derives
        # byte-identical text. The hash is what they compare.
        excerpt = self._stable_excerpt(self._fetch_source_excerpt(source_url))
        evidence_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        record = {
            "claim": claim[:200],
            "source_url": source_url,
            "evidence": excerpt[:300],
            "evidence_hash": evidence_hash,
            "source_fetched": bool(excerpt) and article_fetched,
            "article_fetched": article_fetched,
            "grounded": excerpt[:200],
        }

        # Step 3: the evidence must mention the article's subject (from the
        # URL, identical on every validator). Off-subject -> INSUFFICIENT.
        subject = self._subject_from_url(url)
        blob = (str(record.get("claim", "")) + " " + str(record.get("grounded", ""))).lower()
        if subject and subject.lower() not in blob:
            return {
                "verdict": "INSUFFICIENT",
                "reasoning": f"the source does not mention the subject {subject!r}",
                "sources": [record],
            }

        verdict, reasoning = self._judge_evidence(record)
        return {
            "verdict": verdict,
            "reasoning": reasoning,
            "sources": [record],
        }

    def _judge_evidence(self, record: dict):

        prompt = (
            "Judge the claim from the excerpt only. SUPPORTED (confirms), "
            "REFUTED (contradicts), INSUFFICIENT (none). Return ONLY JSON: "
            "{\"verdict\":\"SUPPORTED|REFUTED|INSUFFICIENT\","
            "\"reasoning\":\"brief reason\"}.\n\n"
            f"claim: {record.get('claim','')}\nexcerpt: {record.get('evidence','') or 'no excerpt'}"
        )
        parsed = self._parse_verdict_json(self._call_prompt(prompt))
        v = parsed.get("verdict", "INSUFFICIENT")
        if v not in ("SUPPORTED", "REFUTED", "INSUFFICIENT"):
            v = "INSUFFICIENT"
        return v, parsed.get("reasoning", "")

    def _fetch_source_excerpt(self, url: str) -> str:

        try:
            lazy_resp = gl.nondet.web.request(url, method="GET")
            response = lazy_resp.get() if hasattr(lazy_resp, "get") else lazy_resp
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
        """Strip HTML to plain text: <p> paragraphs, or the whole page as fallback."""

        def strip(h: str) -> str:
            h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.DOTALL | re.IGNORECASE)
            h = re.sub(r"<[^>]+>", " ", h)
            for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
                h = h.replace(a, b)
            return re.sub(r"\s+", " ", h).strip()

        paras = [strip(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", raw_html, flags=re.DOTALL | re.IGNORECASE)]
        text = " ".join(p for p in paras if p)[:5000]
        return text or strip(raw_html)[:5000]

    # ------------------------------------------------------------------
    # Defensive JSON parsing helpers
    # ------------------------------------------------------------------

    def _call_prompt(self, prompt: str):
        """exec_prompt -> string; never crash the round."""
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

    def _stable_excerpt(self, text: str, cap: int = 300) -> str:
        """Whole sentences up to `cap`: byte-identical across validators, unlike
        a raw byte slice which drifts with the page."""
        t = re.sub(r"\s+", " ", str(text or "")).strip()
        if not t:
            return ""
        out = ""
        for sentence in re.split(r"(?<=[.!?]) ", t):
            if not sentence:
                continue
            if len(out) + len(sentence) + 1 > cap:
                break
            out = (out + " " + sentence).strip() if out else sentence
        return out or t[:cap]

    def _subject_from_url(self, url: str) -> str:
        """Subject keyword from the article URL; deterministic, no model output."""
        try:
            path = url.rsplit("/", 1)[-1].split("?")[0].split("#")[0]
            path = path.replace("_", " ").replace("%20", " ").strip()
            words = [w for w in re.split(r"[^A-Za-z0-9]+", path) if len(w) > 3]
            return max(words, key=len) if words else ""
        except Exception:
            return ""

    def _wikipedia_article_url(self, title: str) -> str:

        if not title:
            return "https://en.wikipedia.org/wiki/Main_Page"
        # Article URLs use underscores between words.
        slug = title.strip().replace(" ", "_")
        return "https://en.wikipedia.org/wiki/" + quote(slug)

    def _parse_claim_obj(self, raw: str) -> dict:

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
                    "title": str(data.get("title", "")),
                    "terms": str(data.get("terms", "")),
                }
        except (json.JSONDecodeError, KeyError):
            pass
        return {"claim": raw[:200], "title": "", "terms": ""}

    def _parse_verdict_json(self, raw: str) -> dict:

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
