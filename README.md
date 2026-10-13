# FactCheckDAO — steward fix (v3)

**Contract:** [`0x96F7cb98793626100F9322f66C6Fa77497fB18fc`](https://explorer-bradbury.genlayer.com/address/0x96F7cb98793626100F9322f66C6Fa77497fB18fc) on GenLayer Bradbury Testnet (chain 4221)
**Deployed size:** 19,977 bytes — identical to `contracts/factcheck_dao.py` in this repo.

A GenLayer Intelligent Contract for decentralized, source-backed fact-checking. Given an article URL, a committee of AI validators retrieves a real corroborating source from the web and reaches consensus on a verdict — **SUPPORTED**, **REFUTED**, or **INSUFFICIENT** — backed by retrieved evidence, not the model's own knowledge.

---

## The steward's objection

> We cannot accept this submission because validators agree only on a coarse verdict while the contract stores a leader-selected claim, source, and excerpt that the other validators may not have evaluated. The equivalence check must bind the stored claim and evidence trail to validator agreement and avoid finalizing unsupported or transiently unavailable evidence.

---

## The fix

Every validator independently fetches the article, derives the corroborating source page, retrieves it, and judges the claim from the retrieved excerpt. The equivalence check then binds the **evidence itself** to agreement — not just the verdict label:

| Bound value | Why |
|---|---|
| `source_url` | the identical URL every validator retrieved |
| `evidence_hash` | SHA-256 of the retrieved excerpt — validators agree only on **byte-identical** evidence |
| `article_fetched` | the article was actually retrieved |
| `source_retrieved` | the corroborating source was actually retrieved |
| `verdict` | SUPPORTED / REFUTED / INSUFFICIENT |

The principle states explicitly: **if the URL, the evidence hash, or any flag differs, validators do NOT agree — even when the verdict label matches.**

Two supporting changes make that binding meaningful:

1. **`_stable_excerpt()`** — the excerpt is cut at sentence boundaries, not byte offsets, so every validator derives identical text from the same page.
2. **Deterministic source selection** — the corroborating page is derived from the article URL rather than chosen freely by the model, so all validators retrieve the same page.

The stored record — claim, `source_url`, evidence excerpt, `evidence_hash` — comes from the **agreed** result, and the hash is stored on-chain so any observer can re-fetch the URL and verify the evidence independently.

## Nothing finalizes without real evidence

- An unretrievable article reverts and stays PENDING.
- Off-subject evidence (evidence that does not mention the article's subject, derived from the URL) yields `INSUFFICIENT` rather than a verdict resting on unrelated text.

---

## Verified on-chain

`node prove_steward.mjs` → **15/15 assertions pass.**

| Test | Result |
|---|---|
| submit_article commits | PASS — AGREE |
| article is PENDING before resolution | PASS |
| validators reach consensus | PASS — AGREE |
| verdict stored | PASS — INSUFFICIENT |
| article_fetched recorded true | PASS |
| source_fetched recorded true | PASS |
| source_url is the agreed URL | PASS — `https://en.wikipedia.org/wiki/Hello_World_program` |
| claim is grounded in the fetched source | PASS |
| evidence excerpt recorded | PASS |
| **evidence hash recorded on-chain** | PASS — `b01fc088e3ae29c3af1f1a616522f494310b7ce683833081cb89322411b4cb29` |
| evidence relates to the article subject | PASS |
| resolve_count preserved | PASS |
| unreachable article did NOT finalize | PASS — stayed PENDING across 4 attempts |
| no verdict recorded for the unreachable article | PASS |
| count_pending correct | PASS |

The verdict is `INSUFFICIENT` because the excerpt describes what a Hello World program is but does not mention its origin — the contract does not default to SUPPORTED.

---

## Contract layout

- `contracts/factcheck_dao.py` — the contract
- `prove_steward.mjs` — on-chain proof of the steward requirement

Runner: `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` (Bradbury).

---

## Adversarial proof: a rigged leader cannot commit

The submitted contract is `factcheck_dao.py`. A separate variant,
`contracts/factcheck_dao_adversarial.py`, changes **only** `leader_fn`: the
leader returns `SUPPORTED` with a fabricated source URL
(`https://en.wikipedia.org/wiki/Fabricated_Source`), a fabricated excerpt, and
the SHA-256 of that fabrication. Every validator runs the real path and
retrieves the actual page.

Because the equivalence check binds `evidence_hash`, the leader's fabricated
hash cannot match what validators retrieve, so the round must commit nothing.

**Verified on Bradbury** — rigged contract
[`0x71C384848EAdb767CC7f59917D26F161b98c29Ce`](https://explorer-bradbury.genlayer.com/address/0x71C384848EAdb767CC7f59917D26F161b98c29Ce):

| Round | Result | Validator votes |
|---|---|---|
| 1 | AGREE / FINISHED_WITH_ERROR | TIMEOUT, TIMEOUT, **DISAGREE**, **DISAGREE**, **DISAGREE** |
| 2 | IDLE / NOT_VOTED | all NOT_VOTED |
| 3 | AGREE / FINISHED_WITH_ERROR | TIMEOUT, **DISAGREE** ×4 |
| 4 | AGREE / FINISHED_WITH_ERROR | **DISAGREE** ×4, TIMEOUT |
| 5 | AGREE / FINISHED_WITH_ERROR | TIMEOUT, **DISAGREE** ×4 |

Read back from chain after all five rounds:

```
article-1 status : PENDING
verdict stored   : NONE
resolve_count    : 0
```

`node prove_adversarial.mjs` → **5 passed, 0 failed.**

This is the case the steward described: a leader whose claim, source and
excerpt the other validators never evaluated. It is rejected.
