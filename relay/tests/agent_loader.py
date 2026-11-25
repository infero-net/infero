"""Load relay/agent.py as a module for tests, without starting the agent.

agent.py ends with `_agent_lock = acquire_single_instance_lock()` and `asyncio.run(main())`, and writes
`device_id` into INFERO_DIR at import. Tests load it with those two startup lines removed and
INFERO_DIR pointed at a temporary folder, so nothing is written next to the source.

Import errors (e.g. `websockets` not installed) are raised, not swallowed: a test that can't load
the agent must fail, not pass vacuously. Install with: pip install -r src/tests/regression/requirements.txt
"""
import os
import re
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AGENT_PY = os.path.join(ROOT, 'relay', 'agent.py')
STARTUP = (r'^_agent_lock = acquire_single_instance_lock\(\)\s*$', r'^asyncio\.run\(main\(\)\)\s*$')

_cache = {}


def load_agent(infero_dir=None):
    """Return the agent module. Each infero_dir gets its own module (INFERO_DIR is read at import)."""
    infero_dir = infero_dir or _default_dir()
    if infero_dir in _cache:
        return _cache[infero_dir]
    with open(AGENT_PY, encoding='utf-8') as f:
        src = f.read()
    for pat in STARTUP:
        src, n = re.subn(pat, 'pass  # removed by tests/agent_loader.py', src, flags=re.M)
        if n != 1:
            raise RuntimeError(f'agent_loader: startup line {pat!r} not found in agent.py — update the loader')
    old = os.environ.get('INFERO_DIR')
    os.environ['INFERO_DIR'] = infero_dir
    try:
        mod = types.ModuleType('agent_under_test')
        mod.__file__ = AGENT_PY
        exec(compile(src, AGENT_PY, 'exec'), mod.__dict__)
    finally:
        if old is None:
            os.environ.pop('INFERO_DIR', None)
        else:
            os.environ['INFERO_DIR'] = old
    _cache[infero_dir] = mod
    return mod


def _default_dir():
    if 'default' not in _cache:
        _cache['default'] = tempfile.mkdtemp(prefix='infero-agent-test-')
    return _cache['default']


def worker_class(mod):
    return mod.GenesisWorker
