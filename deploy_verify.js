// Deploy FactCheckDAO to Bradbury and run the real submit->resolve->get_verdict workflow.
// Uses the proven genlayer-js SDK pattern (same as ContractJudge).
const { createClient, chains } = require('genlayer-js');
const { privateKeyToAccount } = require('viem/accounts');
const fs = require('fs');

const RPC = 'https://rpc-bradbury.genlayer.com';
const CONTRACT = fs.readFileSync('contracts/factcheck_dao.py', 'utf8');
let PK = fs.readFileSync('/tmp/oracle_pk.txt', 'utf8').trim();
if (!PK.startsWith('0x')) PK = '0x' + PK;

const account = privateKeyToAccount(PK);
const client = createClient({ chain: chains.testnetBradbury, account });
const FEES = { feeValue: '100000000000010352' };

const rr = (r) => r?.resultName || r?.consensusData?.resultName || '';

async function waitFinal(h, label) {
  console.log(`  ${label}: ${h} — waiting...`);
  for (let i = 0; i < 240; i++) {
    const r = await client.waitForTransactionReceipt({ hash: h, waitUntil: 'finalized', retries: 240 });
    const res = rr(r);
    if (res) { console.log(`  ${label}: result=${res}`); return r; }
    await new Promise(x => setTimeout(x, 5000));
  }
  return client.waitForTransactionReceipt({ hash: h, waitUntil: 'finalized', retries: 240 });
}

(async () => {
  console.log('deployer:', account.address);
  console.log('[1] deploying...');
  const txHash = await client.deployContract({ code: CONTRACT });
  const dReceipt = await client.waitForTransactionReceipt({ hash: txHash, waitUntil: 'finalized', retries: 240 });
  const addr = dReceipt.txDataDecoded?.contractAddress;
  console.log('deployed at:', addr, '| status:', dReceipt.status_name, '| result:', rr(dReceipt));
  fs.writeFileSync('deployed_addresses.json', JSON.stringify({
    judge_address: addr, network: 'testnet-bradbury', chain_id: 4221, deployer: account.address,
  }, null, 2));

  const url = 'https://example.com';
  console.log(`[2] submit_article(${url})`);
  const sh = await client.writeContract({ address: addr, functionName: 'submit_article', args: [url], value: 0n, fees: FEES });
  await waitFinal(sh, 'submit_article');
  await new Promise(x => setTimeout(x, 6000));

  console.log('[3] resolve_article (run_nondet: leader fetches, validators validate)');
  let agreed = false;
  for (let a = 1; a <= 8 && !agreed; a++) {
    console.log(`  resolve attempt ${a}...`);
    const rh = await client.writeContract({ address: addr, functionName: 'resolve_article', args: ['article-1'], value: 0n, fees: FEES });
    const r = await client.waitForTransactionReceipt({ hash: rh, waitUntil: 'finalized', retries: 240 });
    const res = rr(r);
    console.log(`    result: ${res}`);
    if (res === 'AGREE') agreed = true;
    if (!agreed) await new Promise(x => setTimeout(x, 8000));
  }
  console.log('consensus agreed:', agreed);

  console.log('[4] get_verdict(article-1)');
  const v = await client.readContract({ address: addr, functionName: 'get_verdict', args: ['article-1'] });
  console.log('VERDICT:', JSON.stringify(v, null, 2));
})().catch(e => { console.error('ERROR:', e.message || e); process.exit(1); });
