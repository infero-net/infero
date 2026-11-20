"""
Unit tests for relay/agent.py:_build_payload.

Mirrors the rolling-history Anthropic cache layout from src/index.html:
  [stableHead 🚩] [history = all-but-latest turn] [latest turn 🚩] [tail]

Run: python3 -m pytest relay/tests/test_build_payload.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_loader import load_agent  # noqa: E402


def _make_worker(consciousness='', core_mem='', skills=None, realtime='', metadata=None):
    mod = load_agent()
    w = mod.GenesisWorker.__new__(mod.GenesisWorker)
    w.being_id = 'test_being'
    w.consciousness = consciousness
    w.metadata = metadata or {}
    w._last_realtime = realtime
    w._last_prompt_tokens = 0
    w.llm_settings = {}
    w._read_core_mem = lambda: core_mem
    w._read_skills = lambda: skills or []
    w._log = lambda *a, **kw: None
    return w


def _blocks(payload):
    return payload['messages'][0]['content']


def _has_cc(block):
    return 'cache_control' in block


class AnthropicLayout(unittest.TestCase):
    """Rolling-history Anthropic cache: 2 cc breakpoints, unbounded conversation."""

    def test_empty_consciousness(self):
        """No history → only stable_head + tail blocks."""
        w = _make_worker(consciousness='', core_mem='note', skills=[{'name': 's', 'instruction': 'i'}])
        p = w._build_payload('anthropic', 'claude-x', 'sys', False)
        bs = _blocks(p)
        # stable_head (cc) + tail (no cc)
        self.assertEqual(len(bs), 2)
        self.assertTrue(_has_cc(bs[0]))
        self.assertIn('CORE MEMORY', bs[0]['text'])
        self.assertIn('SKILLS', bs[0]['text'])
        self.assertFalse(_has_cc(bs[1]))
        self.assertTrue(bs[1]['text'].startswith('\n\n'))  # tail prefix

    def test_single_turn_is_latest(self):
        """One turn → no history block; that turn is `latest` with cc."""
        w = _make_worker(consciousness='**Digital Being - [2026-01-01 12:00]**\nhello\n')
        p = w._build_payload('anthropic', 'claude-x', 'sys', False)
        bs = _blocks(p)
        # No stable_head (empty), no history, latest (cc), tail (no cc)
        self.assertEqual(len(bs), 2)
        self.assertTrue(_has_cc(bs[0]))
        self.assertIn('hello', bs[0]['text'])
        self.assertFalse(_has_cc(bs[1]))

    def test_multiple_turns_split_correctly(self):
        c = ('**Digital Being - [2026-01-01 12:00]**\nturn 1\n\n'
             '**Digital Being - [2026-01-01 12:01]**\nturn 2\n\n'
             '**Digital Being - [2026-01-01 12:02]**\nturn 3 latest\n')
        w = _make_worker(consciousness=c, core_mem='cm', skills=[{'name': 's', 'instruction': 'i'}])
        p = w._build_payload('anthropic', 'claude-x', 'sys', False)
        bs = _blocks(p)
        # stable_head (cc), history (no cc), latest (cc), tail (no cc)
        self.assertEqual(len(bs), 4)
        self.assertTrue(_has_cc(bs[0]))
        self.assertIn('CORE MEMORY', bs[0]['text'])
        self.assertFalse(_has_cc(bs[1]))
        self.assertIn('turn 1', bs[1]['text'])
        self.assertIn('turn 2', bs[1]['text'])
        self.assertNotIn('turn 3', bs[1]['text'])
        self.assertTrue(_has_cc(bs[2]))
        self.assertIn('turn 3 latest', bs[2]['text'])
        self.assertFalse(_has_cc(bs[3]))

    def test_at_most_two_cache_control_breakpoints(self):
        c = ('**Digital Being - [2026-01-01 12:00]**\nA\n\n'
             '**Digital Being - [2026-01-01 12:01]**\nB\n\n'
             '**Digital Being - [2026-01-01 12:02]**\nC\n\n'
             '**Digital Being - [2026-01-01 12:03]**\nD\n')
        w = _make_worker(consciousness=c, core_mem='cm')
        p = w._build_payload('anthropic', 'claude-x', 'sys', False)
        cc_count = sum(1 for b in _blocks(p) if _has_cc(b))
        self.assertEqual(cc_count, 2)

    def test_history_concatenation_preserves_byte_for_byte(self):
        """history + latest, when concatenated, must equal the original consciousness."""
        c = ('**Digital Being - [2026-01-01 12:00]**\nturn one body\n/self_continue\n\n'
             '**Digital Being - [2026-01-01 12:01]**\nturn two body\n/call_for_trigger\n')
        w = _make_worker(consciousness=c)
        p = w._build_payload('anthropic', 'claude-x', 'sys', False)
        bs = _blocks(p)
        # No stable_head, no realtime → blocks: history, latest, tail
        # (or just latest+tail if only one turn — but here we have two)
        self.assertEqual(len(bs), 3)
        history = bs[0]['text']
        latest = bs[1]['text']
        self.assertEqual(history + latest, c)

    def test_non_timestamp_brackets_do_not_split(self):
        """`System - [Browser]` (no digit in brackets) must NOT split a turn."""
        c = ('**Digital Being - [2026-01-01 12:00]**\n'
             'reply\n\n'
             'System - [Browser] - Result:\n```\nreturn 42\n```\n\n'
             '**Digital Being - [2026-01-01 12:01]**\nfollow-up\n')
        w = _make_worker(consciousness=c)
        p = w._build_payload('anthropic', 'claude-x', 'sys', False)
        bs = _blocks(p)
        # 2 turns expected: turn 1 carries the System line; turn 2 = follow-up
        # blocks: history (turn 1 incl. System line), latest (turn 2), tail
        self.assertEqual(len(bs), 3)
        self.assertIn('System - [Browser]', bs[0]['text'])
        self.assertIn('return 42', bs[0]['text'])
        self.assertIn('follow-up', bs[1]['text'])
        self.assertNotIn('follow-up', bs[0]['text'])

    def test_thinking_flag_sets_budget(self):
        w = _make_worker(consciousness='**A - [1]**\nx\n')
        p = w._build_payload('anthropic', 'claude-x', 'sys', True)
        self.assertEqual(p['thinking'], {'type': 'enabled', 'budget_tokens': 10000})
        self.assertEqual(p['temperature'], 1)
        self.assertEqual(p['max_tokens'], 16000)

    def test_no_thinking_keeps_temperature(self):
        w = _make_worker(consciousness='**A - [1]**\nx\n')
        p = w._build_payload('anthropic', 'claude-x', 'sys', False)
        self.assertNotIn('thinking', p)
        self.assertEqual(p['temperature'], 0.7)
        self.assertEqual(p['max_tokens'], 16384)

    def test_realtime_lands_in_tail_uncached(self):
        w = _make_worker(
            consciousness='**A - [1]**\nx\n',
            realtime='[Realtime]\nReminder: end with /call_for_trigger',
        )
        p = w._build_payload('anthropic', 'claude-x', 'sys', False)
        tail = _blocks(p)[-1]
        self.assertFalse(_has_cc(tail))
        self.assertIn('[Realtime]', tail['text'])
        self.assertIn('Digital Being -', tail['text'])  # being_prefix


class OpenAILayout(unittest.TestCase):
    def test_full_text_concatenates_all_three_segments(self):
        w = _make_worker(
            consciousness='HISTORY',
            core_mem='CM',
            skills=[{'name': 's', 'instruction': 'I'}],
            realtime='RT',
        )
        p = w._build_payload('openai', 'gpt-x', 'sys', False)
        # stable head + history in block 0, realtime + being prefix in the last block
        text = ''.join(b['text'] for b in p['messages'][1]['content'] if b['type'] == 'text')
        self.assertIn('CORE MEMORY', text)
        self.assertIn('SKILLS', text)
        self.assertIn('HISTORY', text)
        self.assertIn('RT', text)
        # ordering: stable < history < tail
        self.assertLess(text.index('CORE MEMORY'), text.index('HISTORY'))
        self.assertLess(text.index('HISTORY'), text.index('RT'))


class GeminiLayout(unittest.TestCase):
    def test_no_cache_buffer_includes_stable_head(self):
        w = _make_worker(consciousness='HIST', core_mem='CM', realtime='RT')
        p = w._build_payload('gemini', 'gemini-x', 'sys', False)
        text = ''.join(part.get('text', '') for part in p['contents'][0]['parts'])
        self.assertIn('CORE MEMORY', text)  # stable_head present
        self.assertIn('HIST', text)
        self.assertIn('RT', text)
        self.assertIn('systemInstruction', p)  # no cache → systemInstruction present

    def test_cache_path_strips_prefix_and_skips_stable_head(self):
        # Server prepends stableHead + history[0..cached_length] when cache exists.
        # Client only sends history[cached_length:] + tail.
        w = _make_worker(
            consciousness='AAAABBBB',  # 8 chars
            core_mem='CM',
            realtime='RT',
            metadata={'cacheName': 'caches/abc', 'cachedLength': 4},
        )
        p = w._build_payload('gemini', 'gemini-x', 'sys', False)
        text = ''.join(part.get('text', '') for part in p['contents'][0]['parts'])
        self.assertIn('cachedContent', p)
        self.assertNotIn('systemInstruction', p)
        self.assertNotIn('CORE MEMORY', text)  # stable_head NOT re-sent
        self.assertNotIn('AAAA', text)  # cached prefix stripped
        self.assertIn('BBBB', text)  # delta only
        self.assertIn('RT', text)


if __name__ == '__main__':
    unittest.main()
