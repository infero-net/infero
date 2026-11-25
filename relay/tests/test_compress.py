"""
Unit tests for relay/agent.py:_maybe_compress_consciousness.

Mirrors src/index.html maybeCompressConsciousness:
- HEAD + bridge + TAIL, bridge reconstructed from filesystem each round
- Dynamic tail = max(MIN_TAIL_FRAC, TAIL_FRAC - bridge)
- FIFO 50 entries in bridge, raw cuts persisted unbounded

Run: python3 -m pytest relay/tests/test_compress.py -v
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_loader import load_agent  # noqa: E402


def _make_worker(infero_dir, consciousness, prompt_tokens, being_id='test_being'):
    mod = load_agent(infero_dir)
    w = mod.GenesisWorker.__new__(mod.GenesisWorker)
    w.being_id = being_id
    w.consciousness = consciousness
    w.metadata = {'cacheName': 'caches/old', 'cachedLength': 1000}
    w.llm_settings = {'compressCfg': {'at': 100, 'head': 0.1, 'tail': 0.7}}  # token thresholds tiny for testing
    w._last_prompt_tokens = prompt_tokens
    w._log = lambda *a, **k: None
    return w


class TestCompress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='infero-compress-test-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_op_when_below_limit(self):
        w = _make_worker(self.tmp, 'short text', prompt_tokens=50)
        before = w.consciousness
        w._maybe_compress_consciousness()
        self.assertEqual(w.consciousness, before)

    def test_first_cut_writes_log_and_bridge(self):
        # Build a transcript ~500 tokens (cpt = chars/tokens). We claim 200 tokens, 4000 chars → cpt=20.
        # With LIMIT=100 tokens: HEAD = 10 tokens * 20 = 200 chars, TAIL ≈ 70 tokens * 20 = 1400 chars,
        # cut should happen because 200+1400 < 4000.
        head_part = 'HEAD' + 'a' * 196
        mid_part = 'M' * 2200 + 'IDDLE' + 'X' * 200
        tail_part = 'TAIL' + 'z' * 1396
        w = _make_worker(self.tmp, head_part + mid_part + tail_part, prompt_tokens=200)
        w._maybe_compress_consciousness()

        # Log file exists
        being_dir = os.path.join(self.tmp, 'beings', w.being_id)
        logs = [f for f in os.listdir(being_dir) if f.startswith('context_log_')]
        self.assertEqual(len(logs), 1, f'expected 1 log, got {logs}')

        # Bridge markers present
        self.assertIn('=== bridge:start ===', w.consciousness)
        self.assertIn('=== bridge:end ===', w.consciousness)
        self.assertRegex(w.consciousness, r'\[gap \d+: .{1,200} \.\.\. .{1,200}\]')

        # HEAD preserved at the start
        self.assertTrue(w.consciousness.startswith('HEAD'),
                        f"head missing: {w.consciousness[:50]!r}")
        # TAIL preserved at the end
        self.assertTrue(w.consciousness.endswith('z' * 100),
                        f"tail missing: {w.consciousness[-50:]!r}")
        # Middle was cut (no 'IDDLE' anymore)
        # (we do allow IDDLE chars in the gap preview if they fell at the boundary)
        # so check raw bytes count went down
        self.assertLess(len(w.consciousness),
                        len(head_part + mid_part + tail_part))
        # Cache invalidated
        self.assertIsNone(w.metadata['cacheName'])
        self.assertEqual(w.metadata['cachedLength'], 0)

    def test_bridge_grows_then_caps_at_50(self):
        being_dir = os.path.join(self.tmp, 'beings', 'test_being')
        os.makedirs(being_dir, exist_ok=True)
        # Pre-seed 55 fake context_log files
        import time
        for i in range(55):
            ts_ms = 1700000000000 + i
            with open(os.path.join(being_dir, f'context_log_{ts_ms}.txt'), 'w') as f:
                f.write(f'entry-{i:03d} ' + 'body' * 50)
        w = _make_worker(self.tmp,
                         'HEAD' + 'a' * 200 + 'MIDDLE' + 'm' * 3000 + 'TAIL' + 'z' * 1400,
                         prompt_tokens=200)
        w._maybe_compress_consciousness()

        # After compress: 56 files on disk
        logs = sorted(f for f in os.listdir(being_dir) if f.startswith('context_log_'))
        self.assertEqual(len(logs), 56)

        # But bridge only references the last 50
        gap_lines = re.findall(r'\[gap (\d+):', w.consciousness)
        self.assertEqual(len(gap_lines), 50)
        # Oldest in bridge is entry index 6 (we dropped 0..5)
        self.assertNotIn('1700000000000', w.consciousness)
        self.assertNotIn('1700000000005', w.consciousness)
        self.assertIn('1700000000006', w.consciousness)

    def test_idempotent_repeated_compress_strips_old_bridge(self):
        # First compress
        w = _make_worker(self.tmp,
                         'HEAD' + 'a' * 200 + 'M' * 3000 + 'TAIL' + 'z' * 1400,
                         prompt_tokens=200)
        w._maybe_compress_consciousness()
        first = w.consciousness
        n_starts_1 = first.count('=== bridge:start ===')
        self.assertEqual(n_starts_1, 1)

        # Inflate consciousness: append fresh text and bump tokens above limit again
        w.consciousness = first + ('\n\n**Digital Being - [later]**\n' + 'q' * 2500)
        w._last_prompt_tokens = 200

        w._maybe_compress_consciousness()
        # Still exactly one bridge marker (old one stripped, new one rebuilt)
        self.assertEqual(w.consciousness.count('=== bridge:start ==='), 1)
        self.assertEqual(w.consciousness.count('=== bridge:end ==='), 1)
        # And there are now 2 gap entries
        gap_lines = re.findall(r'\[gap (\d+):', w.consciousness)
        self.assertEqual(len(gap_lines), 2)


    def test_bridge_regex_ignores_incidental_marker_mentions(self):
        # Test the regex directly: incidental delimiter mentions in chat (without a real
        # [gap ...] line between them) must not match. Only real bridge blocks should strip.
        rx = load_agent(self.tmp).GenesisWorker._BRIDGE_RE

        incidental = (
            'Some chat about === bridge:start === and === bridge:end ===.\n'
            'Being explains: between them goes a list of [gap ts: head ... tail].\n'
        )
        self.assertIsNone(rx.search(incidental), 'incidental mention must NOT match')

        real = (
            'prefix\n=== bridge:start ===\n'
            '[gap 1700000000001: head text ... tail text]\n'
            '[gap 1700000000002: a ... b]\n'
            '=== bridge:end ===\nsuffix'
        )
        m = rx.search(real)
        self.assertIsNotNone(m, 'real bridge must match')
        # After substitution, only prefix + suffix survive (with \n\n between)
        cleaned = rx.sub('\n\n', real)
        self.assertIn('prefix', cleaned)
        self.assertIn('suffix', cleaned)
        self.assertNotIn('[gap 1700000000001', cleaned)


if __name__ == '__main__':
    unittest.main()
