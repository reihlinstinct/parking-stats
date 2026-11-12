'use strict';

const SERVICE = '/twirp/github.actions.results.api.v1.ArtifactService/ListArtifacts';

function requestContext(env) {
  const origin = new URL(env.ACTIONS_RESULTS_URL);
  if (origin.protocol !== 'https:' || origin.username || origin.password ||
      origin.port || origin.search || origin.hash ||
      !origin.hostname.endsWith('.actions.githubusercontent.com')) {
    throw new Error('Unexpected artifact service origin');
  }
  const token = env.ACTIONS_RUNTIME_TOKEN;
  const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString());
  const scope = String(claims.scp).split(' ').filter(s => s.startsWith('Actions.Results:'));
  if (scope.length !== 1) throw new Error('Invalid artifact service context');
  const parts = scope[0].split(':');
  if (parts.length !== 3 || !parts.slice(1).every(p => /^[a-zA-Z0-9-]+$/.test(p))) {
    throw new Error('Invalid artifact backend IDs');
  }
  return {
    url: new URL(SERVICE, origin), token,
    body: JSON.stringify({workflowRunBackendId: parts[1], workflowJobRunBackendId: parts[2]})
  };
}

async function waitForArtifact(list, id, name, sleep, attempts = 7) {
  if (!/^[1-9][0-9]*$/.test(id) || !/^github-pages-[1-9][0-9]*$/.test(name) ||
      !Number.isInteger(attempts) || attempts < 1 || attempts > 7) {
    throw new Error('Invalid Pages artifact parameters');
  }
  for (let attempt = 0; attempt < attempts; attempt++) {
    const payload = await list();
    // Protobuf JSON may omit an empty repeated field.
    const artifacts = payload.artifacts === undefined ? [] : payload.artifacts;
    if (!Array.isArray(artifacts) || artifacts.some(a => !a || typeof a !== 'object')) {
      throw new Error('Invalid artifact metadata');
    }
    const matches = artifacts.filter(a => a.name === name);
    if (matches.length) {
      const artifact = matches[0];
      const actualId = String(artifact.databaseId ?? artifact.database_id);
      const size = String(artifact.size);
      if (matches.length !== 1 || actualId !== id || !/^[1-9][0-9]*$/.test(size)) {
        throw new Error('Ambiguous or mismatched Pages artifact');
      }
      return;
    }
    if (attempt + 1 < attempts) await sleep(5000);
  }
  throw new Error('Pages artifact metadata not visible within 30 seconds');
}

async function main() {
  const context = requestContext(process.env);
  const list = async () => {
    const response = await fetch(context.url, {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10000),
      headers: {'Authorization': `Bearer ${context.token}`, 'Content-Type': 'application/json'},
      body: context.body
    });
    if (!response.ok) throw new Error('Artifact service request failed');
    const result = await response.json();
    if (!result || typeof result !== 'object' || Array.isArray(result)) {
      throw new Error('Invalid artifact service response');
    }
    return result;
  };
  await waitForArtifact(list, process.env['INPUT_ARTIFACT-ID'],
    process.env['INPUT_ARTIFACT-NAME'], ms => new Promise(resolve => setTimeout(resolve, ms)));
  console.log('Exact Pages upload is visible; deployment may proceed once.');
}

module.exports = {requestContext, waitForArtifact};
if (require.main === module) {
  main().catch(() => {
    // Never print runtime credentials, decoded scopes or response bodies.
    console.error('Pages artifact readiness failed; deployment not started');
    process.exitCode = 1;
  });
}
