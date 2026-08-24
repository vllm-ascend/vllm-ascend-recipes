import assert from 'node:assert/strict';
import test from 'node:test';
import { modelCardKey } from '../../src/lib/model-card-key';

test('uses the recipe URL to distinguish cards with the same model ID', () => {
  const cards = [
    {
      hf_id: 'Qwen/Qwen3-30B-A3B',
      url: '/Qwen/Qwen3-30B-A3B',
    },
    {
      hf_id: 'Qwen/Qwen3-30B-A3B',
      url: '/Qwen/template2_non_pd',
    },
  ];

  const keys = cards.map(modelCardKey);

  assert.equal(new Set(keys).size, cards.length);
});
