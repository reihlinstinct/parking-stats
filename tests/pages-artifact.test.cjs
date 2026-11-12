'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {waitForArtifact, requestContext} = require('../.github/actions/pages-artifact-readiness/index.cjs');
const artifact = {databaseId: '123', name: 'github-pages-1', size: '42'};

test('same-service visibility lag resolves with exact upload', async () => {
  let reads = 0;
  const waits = [];
  await waitForArtifact(async () => ++reads === 1 ? {} : {artifacts: [artifact]},
    '123', 'github-pages-1', async ms => waits.push(ms));
  assert.equal(reads, 2);
  assert.deepEqual(waits, [5000]);
});
test('missing metadata has seven reads and six waits only', async () => {
  let reads = 0, waits = 0;
  await assert.rejects(waitForArtifact(async () => {reads++; return {artifacts: []};},
    '123', 'github-pages-1', async () => waits++));
  assert.equal(reads, 7);
  assert.equal(waits, 6);
});
test('wrong ID, duplicate, zero size and malformed listings fail immediately', async () => {
  for (const artifacts of [[{...artifact, databaseId: '124'}], [artifact, artifact],
    [{...artifact, size: '0'}], [null], 'bad']) {
    let waits = 0, reads = 0;
    await assert.rejects(waitForArtifact(async () => {reads++; return {artifacts};},
      '123', 'github-pages-1', async () => waits++));
    assert.equal(reads, 1);
    assert.equal(waits, 0);
  }
});
test('previous attempt does not satisfy readiness', async () => {
  await assert.rejects(waitForArtifact(async () => ({artifacts: [artifact]}),
    '123', 'github-pages-2', async () => {}, 1));
});
test('network failure is not retried', async () => {
  let reads = 0;
  await assert.rejects(waitForArtifact(async () => {reads++; throw new Error('synthetic');},
    '123', 'github-pages-1', async () => {}));
  assert.equal(reads, 1);
});
test('invalid expected inputs fail before request', async () => {
  let reads = 0;
  for (const args of [['0', 'github-pages-1', 7], ['123', '../bad', 7],
    ['123', 'github-pages-1', 8]]) {
    await assert.rejects(waitForArtifact(async () => {reads++;}, args[0], args[1], async () => {}, args[2]));
  }
  assert.equal(reads, 0);
});
test('runner context is confined to Actions origin and backend IDs', () => {
  const token = 'synthetic.' + Buffer.from(JSON.stringify({
    scp: 'Actions.Results:run-123:job-456'
  })).toString('base64url') + '.synthetic';
  const env = {ACTIONS_RUNTIME_TOKEN: token, ACTIONS_RESULTS_URL: 'https://results-receiver.actions.githubusercontent.com/'};
  const context = requestContext(env);
  assert.equal(context.url.href, env.ACTIONS_RESULTS_URL + 'twirp/github.actions.results.api.v1.ArtifactService/ListArtifacts');
  assert.deepEqual(JSON.parse(context.body), {workflowRunBackendId: 'run-123', workflowJobRunBackendId: 'job-456'});
  for (const url of ['http://results-receiver.actions.githubusercontent.com/',
    'https://example.invalid/', 'https://actions.githubusercontent.com.attacker.invalid/',
    'https://user:pass@results-receiver.actions.githubusercontent.com/']) {
    assert.throws(() => requestContext({...env, ACTIONS_RESULTS_URL: url}));
  }
  assert.throws(() => requestContext({...env, ACTIONS_RUNTIME_TOKEN: 'synthetic'}));
});
