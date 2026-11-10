#!/usr/bin/env python3
"""
Test merged-block cache strategy past 20-block lookback.

Layout per request:
  [stableHead (cc)] [full_group_0] [full_group_1] ... [last_full (cc)] [partial (cc)] [tail]

Each group = GROUP_SIZE turns merged into one text block (frozen, never edited).
Partial = current incomplete group, grows each round.

Expectation: even at round 25+ the cache_read covers ~ all prior turns.
"""
import json
import os
import sys
import urllib.request

# Load API key
with open(os.path.expanduser("~/Projects/The-Principle/prototype/env")) as f:
    for line in f:
        if line.startswith("ANT_KEY="):
            API_KEY = line.split("=", 1)[1].strip()
            break

MODEL = "claude-haiku-4-5"
GROUP_SIZE = 19
ROUNDS = 60

# Build stable head ~ 4K tokens (16KB) of stable filler so it exceeds haiku's 2048-token min
STABLE_HEAD = ("=== STABLE HEAD ===\n" + ("This is a stable system context line that never changes across rounds. " * 250)).strip() + "\n"

# Each turn ~ 1K tokens (4KB)
def make_turn(idx):
    return f"\n**Digital Being - [turn {idx:04d}]**\n" + (f"Turn {idx} content. " * 200) + "\n"

def build_blocks(turns):
    """Return list of message-content blocks for `turns` past turns."""
    blocks = [{"type": "text", "text": STABLE_HEAD, "cache_control": {"type": "ephemeral"}}]
    n = len(turns)
    full_groups = n // GROUP_SIZE
    last_full_idx = None
    for g in range(full_groups):
        merged = "".join(turns[g * GROUP_SIZE:(g + 1) * GROUP_SIZE])
        block = {"type": "text", "text": merged}
        blocks.append(block)
        last_full_idx = len(blocks) - 1
    # partial
    partial_start = full_groups * GROUP_SIZE
    partial_text = "".join(turns[partial_start:])
    has_partial = bool(partial_text)
    if has_partial:
        blocks.append({"type": "text", "text": partial_text})
        partial_idx = len(blocks) - 1
    else:
        partial_idx = None
    # cache_control on last full + partial (avoid >4 total: stableHead + last_full + partial = 3)
    if last_full_idx is not None:
        blocks[last_full_idx]["cache_control"] = {"type": "ephemeral"}
    if partial_idx is not None:
        blocks[partial_idx]["cache_control"] = {"type": "ephemeral"}
    # volatile tail
    blocks.append({"type": "text", "text": f"\n[volatile tail — round-specific noise]\n"})
    return blocks, last_full_idx, partial_idx

def call(blocks):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": blocks}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())

def main():
    turns = []
    print(f"{'rnd':>3} {'turns':>5} {'blocks':>6} {'lf':>3} {'pt':>3} {'input':>7} {'cache_r':>9} {'cache_w':>9}")
    for r in range(1, ROUNDS + 1):
        turns.append(make_turn(r))
        blocks, last_full, partial = build_blocks(turns)
        try:
            resp = call(blocks)
        except Exception as e:
            print(f"round {r} error: {e}")
            return
        u = resp.get("usage", {})
        print(f"{r:>3} {len(turns):>5} {len(blocks):>6} {str(last_full):>3} {str(partial):>3} "
              f"{u.get('input_tokens',0):>7} {u.get('cache_read_input_tokens',0):>9} {u.get('cache_creation_input_tokens',0):>9}")

if __name__ == "__main__":
    main()
