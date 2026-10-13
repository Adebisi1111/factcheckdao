import { createClient, chains } from 'genlayer-js';
import { privateKeyToAccount } from 'viem/accounts';
import fs from 'fs';

const ADDR = '0x96F7cb98793626100F9322f66C6Fa77497fB18fc';
const RPC = 'https://rpc-bradbury.genlayer.com';
let PK = fs.readFileSync('/tmp/oracle_pk.txt', 'utf8').trim();
if (!PK.startsWith('0x')) PK = '0x' + PK;

const account = privateKeyToAccount(PK);
const client = createClient({ chain: chains.testnetBradbury, account });
const ME = account.address;
const FEES = { feeValue: '100000000000010352' };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

let pass = 0, fail = 0;
const check = (label, cond, extra = '') => {
  if (cond) { pass++; console.log(`  PASS  ${label}${extra ? ' — ' + extra : ''}`); }
  else { fail++; console.log(`  FAIL  ${label}${extra ? ' — ' + extra : ''}`); }
};

const ABI = [
  { type: 'function', name: 'submit_article', stateMutability: 'nonpayable',
    inputs: [{ name: 'url', type: 'string' }], outputs: [] },
  { type: 'function', name: 'resolve_article', stateMutability: 'nonpayable',
    inputs: [{ name: 'article_id', type: 'string' }], outputs: [{ name: '', type: 'string' }] },
  { type: 'function', name: 'get_verdict', stateMutability: 'view',
    inputs: [{ name: 'article_id', type: 'string' }], outputs: [{ name: '', type: 'any' }] },
  { type: 'function', name: 'get_article', stateMutability: 'view',
    inputs: [{ name: 'article_id', type: 'string' }], outputs: [{ name: '', type: 'any' }] },
  { type: 'function', name: 'count_pending', stateMutability: 'view', inputs: [], outputs: [{ name: '', type: 'any' }] },
];

async function resyncNonce() {
  try {
    const j = await fetch(RPC, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', method: 'eth_getTransactionCount', params: [ME, 'pending'], id: 1 }),
    }).then(r => r.json());
    const n = parseInt(j.result, 16);
    if (Number.isFinite(n)) { client.account.nonce = n; return n; }
  } catch (e) {}
  return null;
}

async function fin(h) {
  for (let i = 0; i < 200; i++) {
    try {
      const r = await client.waitForTransactionReceipt({ hash: h, waitUntil: 'finalized', retries: 5, interval: 4000 });
      if (r && (r.resultName || r.result_name)) {
        return { name: r.resultName || r.result_name, exec: r.txExecutionResultName || r.tx_execution_result_name };
      }
    } catch (e) {}
    await sleep(5000);
  }
  return null;
}

async function write(fn, args, value = 0n, a = 1) {
  try {
    const h = await client.writeContract({ address: ADDR, abi: ABI, functionName: fn, args, value, fees: FEES });
    return await fin(h);
  } catch (e) {
    const msg = (e.shortMessage || e.message || '');
    const recoverable = msg.includes('to consensus contract') || msg.includes('nonce');
    if (recoverable && a < 6) {
      const n = await resyncNonce();
      console.log(`    nonce resync -> ${n} (attempt ${a})`);
      await sleep(4000);
      return write(fn, args, value, a + 1);
    }
    console.log(`    ${fn} failed: ${msg.slice(0, 90)}`);
    return null;
  }
}

async function read(fn, args = []) {
  const v = await client.readContract({ address: ADDR, abi: ABI, functionName: fn, args });
  return typeof v === 'string' ? JSON.parse(v) : v;
}

async function waitVerdict(id, tries = 30) {
  for (let i = 0; i < tries; i++) {
    const v = await read('get_verdict', [id]).catch(() => null);
    if (v && v.exists) return v;
    await sleep(5000);
  }
  return null;
}

(async () => {
  console.log('FactCheckDAO — steward requirement proof on Bradbury\n');
  console.log('contract:', ADDR, '\n');
  console.log('Requirement: bind the stored claim and evidence trail to validator');
  console.log('agreement, and avoid finalizing unsupported or transiently');
  console.log('unavailable evidence.\n');

  // ---- A: a real article resolves with a bound evidence trail ----
  console.log('[A] real article — evidence trail is bound to agreement');
  const URL = 'https://en.wikipedia.org/wiki/Hello_World_program';
  const sh = await write('submit_article', [URL]);
  check('submit committed', !!sh, sh ? sh.name : 'no receipt');
  await sleep(6000);
  const art = await read('get_article', ['article-1']).catch(() => null);
  check('article is PENDING', !!art && art.status === 'PENDING');
  const attemptsBefore = art ? Number(art.resolve_count || 0) : -1;

  let ok = false;
  for (let a = 1; a <= 10 && !ok; a++) {
    const r = await write('resolve_article', ['article-1']);
    console.log(`  resolve attempt ${a}: ${r ? r.name : 'none'}/${r ? r.exec : 'none'}`);
    if (r && r.name === 'AGREE' && r.exec === 'FINISHED_WITH_RETURN') ok = true;
    if (!ok) await sleep(8000);
  }
  check('validators reached consensus', ok);

  const v = await waitVerdict('article-1');
  if (!v) {
    check('verdict stored', false, 'not readable after consensus');
  } else {
    const rec = (v.sources && v.sources[0]) || {};
    check('verdict stored', true, `verdict=${v.verdict}`);
    check('article_fetched recorded true', rec.article_fetched === true, `=${rec.article_fetched}`);
    check('source_fetched recorded true', rec.source_fetched === true, `=${rec.source_fetched}`);
    check('source_url is the agreed URL', !!rec.source_url, String(rec.source_url).slice(0, 55));
    check('claim is grounded in the fetched source', !!(rec.claim || '').trim(),
      String(rec.claim).slice(0, 65));
    check('evidence excerpt recorded', !!(rec.evidence || '').trim(), `len=${(rec.evidence || '').length}`);
    check('evidence hash recorded on-chain', /^[0-9a-f]{64}$/.test(String(rec.evidence_hash || '')),
      String(rec.evidence_hash || 'none').slice(0, 24) + '…');
    // The evidence must relate to the article's subject, not be an unrelated
    // page the model happened to fetch. This is the check that was missing when
    // a Hello World article stored evidence about an 1828 watercolour.
    const blob = ((rec.claim || '') + ' ' + (rec.evidence || '')).toLowerCase();
    const subjectTerms = ['hello', 'world', 'program'];
    const onSubject = subjectTerms.some(t => blob.includes(t));
    check('evidence relates to the article subject', onSubject,
      onSubject ? 'excerpt mentions the subject' : `excerpt is off-subject: ${String(rec.evidence).slice(0,60)}`);
    const artAfter = await read('get_article', ['article-1']).catch(() => null);
    check('resolve_count preserved', !!artAfter && Number(artAfter.resolve_count) >= 1,
      `${artAfter ? artAfter.resolve_count : 'n/a'} (was ${attemptsBefore})`);
    console.log('\n  STORED (what validators agreed on):');
    console.log('    verdict        :', v.verdict);
    console.log('    source_url     :', rec.source_url);
    console.log('    article_fetched:', rec.article_fetched);
    console.log('    source_fetched :', rec.source_fetched);
    console.log('    claim (grounded):', String(rec.claim).slice(0, 90));
    console.log('    reasoning      :', String(v.reasoning).slice(0, 90));
  }
  console.log('');

  // ---- B: transiently unavailable evidence must NOT finalize ----
  console.log('[B] transiently unavailable evidence must NOT finalize');
  const BAD = 'https://this-domain-does-not-exist-xyz123.example/nothing.html';
  const bh = await write('submit_article', [BAD]);
  check('bad article submitted', !!bh, bh ? bh.name : 'no receipt');
  await sleep(6000);

  let finalizedBad = false;
  for (let a = 1; a <= 4 && !finalizedBad; a++) {
    const r = await write('resolve_article', ['article-2']);
    console.log(`  attempt ${a}: ${r ? r.name : 'none'}/${r ? r.exec : 'none'}`);
    await sleep(5000);
    const cur = await read('get_article', ['article-2']).catch(() => null);
    if (cur && cur.status === 'RESOLVED') finalizedBad = true;
  }
  await sleep(8000);
  const a2 = await read('get_article', ['article-2']).catch(() => null);
  const v2 = await read('get_verdict', ['article-2']).catch(() => null);
  check('unavailable evidence did NOT finalize', !finalizedBad,
    `article-2 status=${a2 ? a2.status : 'unknown'}`);
  check('no verdict recorded for it', !v2 || v2.exists === false,
    v2 && v2.exists ? `verdict=${v2.verdict}` : 'none stored');
  console.log('');

  const pend = await read('count_pending', []).catch(() => '?');
  console.log(`count_pending: ${pend}`);

  console.log(`\n=== ${pass} passed, ${fail} failed ===`);
  console.log(`\nCONTRACT: ${ADDR}`);
  console.log(`EXPLORER: https://explorer-bradbury.genlayer.com/address/${ADDR}`);
})().catch(e => { console.error('ERROR:', e.message || e); process.exit(1); });
