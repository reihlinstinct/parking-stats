'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const health = require('../public/data/pipeline-health.js');

const NOW = Date.parse('2026-11-18T00:00:00Z');
const hoursAgo = h => NOW - h * 60 * 60 * 1000;

test('design fixture: last success 49 hours old renders stale', () => {
  assert.equal(health.decide({hold: false, apiOk: true,
    lastSuccessMs: hoursAgo(49), nowMs: NOW}), 'stale');
});

test('design fixture: no-op success 1 hour old on unchanged aggregates shows no stale indicator', () => {
  assert.equal(health.decide({hold: false, apiOk: true,
    lastSuccessMs: hoursAgo(1), nowMs: NOW}), 'fresh');
});

test('design fixture: unreachable API shows freshness unknown, never fresh', () => {
  assert.equal(health.decide({hold: false, apiOk: false,
    lastSuccessMs: hoursAgo(1), nowMs: NOW}), 'unknown');
});

test('design fixture: hold marker shows paused regardless of success age', () => {
  assert.equal(health.decide({hold: true, apiOk: true,
    lastSuccessMs: hoursAgo(1), nowMs: NOW}), 'paused');
  assert.equal(health.decide({hold: true, apiOk: true,
    lastSuccessMs: hoursAgo(100), nowMs: NOW}), 'paused');
  assert.equal(health.decide({hold: true, apiOk: false,
    lastSuccessMs: null, nowMs: NOW}), 'paused');
});

test('exactly 48 hours is not stale yet; just past it is', () => {
  assert.equal(health.decide({hold: false, apiOk: true,
    lastSuccessMs: hoursAgo(48), nowMs: NOW}), 'fresh');
  assert.equal(health.decide({hold: false, apiOk: true,
    lastSuccessMs: hoursAgo(48) - 1, nowMs: NOW}), 'stale');
});

test('missing or unparseable success age is unknown', () => {
  assert.equal(health.decide({hold: false, apiOk: true,
    lastSuccessMs: null, nowMs: NOW}), 'unknown');
  assert.equal(health.decide({hold: false, apiOk: true,
    lastSuccessMs: NaN, nowMs: NOW}), 'unknown');
  assert.equal(health.decide({hold: false, apiOk: true,
    lastSuccessMs: NOW + 60000, nowMs: NOW}), 'unknown');
});

test('parseHold is strict: only literal hold true pauses', () => {
  assert.equal(health.parseHold({schema_version: 1, hold: true, since: '2026-11-18', note: 'x'}), true);
  assert.equal(health.parseHold({hold: false}), false);
  assert.equal(health.parseHold({hold: 'yes'}), false);
  assert.equal(health.parseHold(null), false);
  assert.equal(health.parseHold('hold'), false);
  assert.equal(health.parseHold(undefined), false);
});

test('lastSuccessFromRuns takes the newest completed success only', () => {
  const body = {workflow_runs: [
    {status: 'completed', conclusion: 'success', updated_at: '2026-11-17T10:00:00Z'},
    {status: 'completed', conclusion: 'failure', updated_at: '2026-11-17T12:00:00Z'},
    {status: 'in_progress', conclusion: null, updated_at: '2026-11-17T13:00:00Z'},
    {status: 'completed', conclusion: 'success', updated_at: '2026-11-17T11:00:00Z'},
  ]};
  assert.equal(health.lastSuccessFromRuns(body), Date.parse('2026-11-17T11:00:00Z'));
});

test('lastSuccessFromRuns rejects malformed bodies and runs', () => {
  assert.equal(health.lastSuccessFromRuns(null), null);
  assert.equal(health.lastSuccessFromRuns({}), null);
  assert.equal(health.lastSuccessFromRuns('runs'), null);
  assert.equal(health.lastSuccessFromRuns({workflow_runs: 'no'}), null);
  assert.equal(health.lastSuccessFromRuns({workflow_runs: [
    {status: 'completed', conclusion: 'success', updated_at: 'not-a-date'},
    'junk',
    {status: 'completed', conclusion: 'success'},
  ]}), null);
});

test('texts falls back to Hebrew for unknown languages', () => {
  assert.equal(health.texts('fr').paused, health.texts('he').paused);
  assert.equal(health.texts('en-US').paused, 'Automatic publication is paused');
});
