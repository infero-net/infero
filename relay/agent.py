import asyncio, json, subprocess, base64, hashlib, os, sys, socket, re, gzip, time
from datetime import datetime
import aiohttp

# Ensure stdout/stderr handle unicode on Windows (cp1252 default breaks non-ASCII)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

INFERO_DIR = os.environ.get('INFERO_DIR', os.path.dirname(os.path.abspath(__file__)))
INSTANCES_FILE = os.path.join(INFERO_DIR, 'instances.json')

def _get_device_name():
    id_file = os.path.join(INFERO_DIR, 'device_id')
    try:
        suffix = open(id_file).read().strip()
    except FileNotFoundError:
        import random, string
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        os.makedirs(INFERO_DIR, exist_ok=True)
        open(id_file, 'w').write(suffix)
    return socket.gethostname().removesuffix('.local') + '-' + suffix

DEVICE_NAME = _get_device_name()

def ts():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def get_log_file(relay_ws):
    """Return log file path. Windows always logs to file (no visible console under Task Scheduler).
    macOS/Linux: file for dev, stdout for prod (captured by launchd/systemd)."""
    if sys.platform == 'win32':
        os.makedirs(INFERO_DIR, exist_ok=True)
        return os.path.join(INFERO_DIR, 'agent.log')
    if 'dev.' in relay_ws:
        log_dir = os.path.expanduser('~/.infero-dev')
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, 'agent.log')
    return None

def log(relay_ws, msg):
    log_file = get_log_file(relay_ws)
    if log_file:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(msg + '\n')
        if sys.stdout.isatty():
            print(msg, file=sys.stdout)
    else:
        print(msg, file=sys.stdout)

def load_instances():
    try:
        return json.load(open(INSTANCES_FILE))
    except:
        return []

def save_instances(instances):
    json.dump(instances, open(INSTANCES_FILE, 'w'), indent=2)

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import ECDH, SECP256R1
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import websockets

def ecdh_derive_key(browser_pub_b64):
    """Derive shared AES-256 key via ECDH. Returns (aes_key_32_bytes, device_pub_b64)."""
    pad = 4 - len(browser_pub_b64) % 4
    if pad != 4: browser_pub_b64 += '=' * pad
    browser_pub_bytes = base64.b64decode(browser_pub_b64)
    browser_pub = ec.EllipticCurvePublicKey.from_encoded_point(SECP256R1(), browser_pub_bytes)
    device_priv = ec.generate_private_key(SECP256R1())
    shared = device_priv.exchange(ECDH(), browser_pub)
    aes_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                   info=b'infero-device-relay-v1').derive(shared)
    device_pub_bytes = device_priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return aes_key, base64.b64encode(device_pub_bytes).decode()

_BIP39 = []  # loaded from relay at startup

async def _load_bip39(relay_http):
    global _BIP39
    if _BIP39:
        return
    try:
        async with aiohttp.ClientSession(trust_env=True) as s:
            async with s.get(f'{relay_http}/bip39', timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    _BIP39 = (await r.text()).split()
    except Exception:
        pass  # verification words unavailable, non-critical

def pair_verify_words(aes_key):
    if len(_BIP39) < 2048:
        return '(wordlist unavailable)'
    h = hashlib.sha256(aes_key).digest()
    n = (h[0] << 14) | (h[1] << 6) | (h[2] >> 2)
    return _BIP39[n >> 11] + ' ' + _BIP39[n & 0x7ff]

def encrypt(cipher, d):
    iv = os.urandom(12)
    ct = cipher.encrypt(iv, json.dumps(d).encode(), None)
    return base64.b64encode(iv + ct).decode()

def decrypt(cipher, b64):
    raw = base64.b64decode(b64)
    return json.loads(cipher.decrypt(raw[:12], raw[12:], None))

async def run_shell_detached(cmd, timeout=30):
    """Run a shell command, returning {stdout, stderr, exit_code}.
    On timeout the process is left running in its own session/group (not killed);
    exit_code is the string 'running' and stderr notes the detach.
    Windows: cmd is executed via PowerShell -NoProfile -NonInteractive -Command."""
    if sys.platform == 'win32':
        proc = await asyncio.create_subprocess_exec(
            'powershell', '-NoProfile', '-NonInteractive', '-Command', cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=0x00000200)  # CREATE_NEW_PROCESS_GROUP
    else:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {'stdout': stdout.decode(errors='replace'),
                'stderr': stderr.decode(errors='replace'),
                'exit_code': proc.returncode}
    except asyncio.TimeoutError:
        return {'stdout': '',
                'stderr': f"[still running after {timeout}s — process detached, loop advances. Write output to a file if you need it.]",
                'exit_code': 'running'}

# ─── Genesis Worker: distributed loop ─────────────────────────────────────────

class GenesisWorker:
    def __init__(self, ws, cipher, iid, relay_ws=''):
        self.ws = ws
        self.cipher = cipher
        self.iid = iid
        self.relay_ws = relay_ws
        self.consciousness = ""
        self.metadata = {}
        self.llm_settings = {}  # model, provider, token, format, thinking, endpoint
        self.running = False
        self.pending_user_input = None
        self._stopped_sent = False
        self._pending_exec = {}  # req_id -> asyncio.Future
        self.devices = {}  # name -> {type, online}
        self.hidden_devices = set()
        self.being_id = ''
        self._last_prompt_tokens = 0
        self.triggers = asyncio.Queue()
        # _next_sleep removed — no watchdog auto-wake
        self._trigger_watcher = None  # file watcher task
        self._loop_task = None  # current run_loop task

    def _being_dir(self):
        if not self.being_id:
            return None
        d = os.path.join(INFERO_DIR, 'beings', self.being_id)
        os.makedirs(d, exist_ok=True)
        return d

    def save_to_disk(self):
        d = self._being_dir()
        if not d:
            return
        with open(os.path.join(d, 'consciousness.txt'), 'wb') as f:
            f.write(self.consciousness.encode('utf-8'))
        # Save all state as a single JSON
        state = {
            'metadata': self.metadata,
            'llm_settings': self.llm_settings,
            'devices': self.devices,
            'hidden_devices': list(self.hidden_devices),
            'last_prompt_tokens': self._last_prompt_tokens,
        }
        with open(os.path.join(d, 'state.json'), 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        self._log(f"[{ts()}] [infero] Saved being {self.being_id}: consciousness={len(self.consciousness)} chars")

    def load_from_disk(self):
        d = self._being_dir()
        if not d:
            return False
        c_path = os.path.join(d, 'consciousness.txt')
        if not os.path.exists(c_path):
            return False
        with open(c_path, 'rb') as f:
            self.consciousness = f.read().decode('utf-8')
        # Load state (new format: single state.json)
        st_path = os.path.join(d, 'state.json')
        if os.path.exists(st_path):
            with open(st_path, 'r', encoding='utf-8') as f:
                state = json.load(f)
            self.metadata = state.get('metadata', {})
            self.llm_settings = state.get('llm_settings', {})
            self.devices = state.get('devices', {})
            self.hidden_devices = set(state.get('hidden_devices', []))
            self._last_prompt_tokens = state.get('last_prompt_tokens', 0)
        else:
            # Backward compat: old format with separate files
            m_path = os.path.join(d, 'metadata.json')
            s_path = os.path.join(d, 'llm_settings.json')
            if os.path.exists(m_path):
                with open(m_path, 'r', encoding='utf-8') as f:
                    self.metadata = json.load(f)
            if os.path.exists(s_path):
                with open(s_path, 'r', encoding='utf-8') as f:
                    self.llm_settings = json.load(f)
        self._log(f"[{ts()}] [infero] Loaded being {self.being_id} from disk: consciousness={len(self.consciousness)} chars")
        return True

    def _log(self, msg):
        log(self.relay_ws, msg)

    async def send_relay(self, msg):
        try:
            await self.ws.send(json.dumps(msg))
        except Exception:
            pass

    def _read_core_mem(self, max_chars=20000):  # ~5k tokens
        if not self.being_id: return ''
        cm_path = os.path.join(INFERO_DIR, 'beings', self.being_id, 'core_mem.md')
        try:
            with open(cm_path, encoding='utf-8') as f: content = f.read()
            if len(content) > max_chars:
                return content[:max_chars] + f"\n\n[⚠️ core_mem truncated to {max_chars} chars. Full long_mem: {len(content)} chars]"
            return content
        except: return ''

    def trigger(self, msg=''):
        """Inject a message and wake the loop. Thread-safe."""
        self._log(f"  [trigger] ({len(msg)} chars): {msg[:100]}")
        self.triggers.put_nowait(msg)

    def wake_me_up_when(self, sec):
        """Schedule a trigger after `sec` seconds."""
        self._log(f"  [wake_me_up_when] {sec}s")
        asyncio.get_event_loop().call_later(sec, lambda: self.triggers.put_nowait(f"[timer] {sec}s elapsed"))

    def _trigger_file_path(self):
        d = self._being_dir()
        return os.path.join(d, 'trigger.txt') if d else None

    async def _watch_trigger_file(self):
        """Poll trigger.txt every 2s. If has content, consume and inject."""
        path = self._trigger_file_path()
        if not path:
            return
        while self.running:
            try:
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    with open(path, 'r') as f:
                        content = f.read().strip()
                    open(path, 'w').close()  # consume
                    if content:
                        self.trigger(content)
            except Exception:
                pass
            await asyncio.sleep(2)

    async def _wait_for_trigger(self):
        """Wait for trigger. No timeout — waits indefinitely."""
        self._log(f"[{ts()}] waiting for trigger")
        msg = await self.triggers.get()
        # Grace period to collect concurrent triggers
        await asyncio.sleep(0.5)
        msgs = [msg]
        while not self.triggers.empty():
            msgs.append(self.triggers.get_nowait())
        merged = '\n'.join(m for m in msgs if m)
        if merged:
            self.consciousness += f"System - [Trigger] {merged}\n\n"
            self._log(f"[{ts()}] triggered ({len(msgs)} msgs): {merged[:200]}")
            await self.send_relay({'type': 'exec_display', 'sender': DEVICE_NAME,
                'payload': encrypt(self.cipher, {'being_id': self.being_id, 'text': f"System - [Trigger] {merged}"})})

    async def on_loop_handoff(self, payload_enc):
        data = decrypt(self.cipher, payload_enc)
        incoming_c = data.get('consciousness', '')
        # Check if device has newer consciousness (e.g. device ran while browser was offline)
        existing_c = self.consciousness
        if not existing_c and self.being_id:
            # Try loading from disk
            d = os.path.join(INFERO_DIR, 'beings', data.get('metadata', {}).get('beingId', ''))
            c_path = os.path.join(d, 'consciousness.txt')
            if os.path.exists(c_path):
                with open(c_path, 'rb') as f:
                    existing_c = f.read().decode('utf-8')
        if existing_c and len(existing_c) > len(incoming_c) and incoming_c in existing_c:
            # Device consciousness is a superset of browser's — keep device's
            self._log(f"[{ts()}] [infero] Handoff: keeping device consciousness ({len(existing_c)} chars > browser {len(incoming_c)} chars)")
            self.consciousness = existing_c
        else:
            self.consciousness = incoming_c
        self.metadata = data.get('metadata', {})
        self.being_id = self.metadata.get('beingId', '')
        core_mem = self.metadata.get('coreMem', '')
        if core_mem and self.being_id:
            cm_path = os.path.join(INFERO_DIR, 'beings', self.being_id, 'core_mem.md')
            os.makedirs(os.path.dirname(cm_path), exist_ok=True)
            with open(cm_path, 'w', encoding='utf-8') as f: f.write(core_mem)
        # Sync skills: browser's {beingId}/skill/* → {INFERO_DIR}/beings/{beingId}/skill/{name}.json
        skills = data.get('skills') or []
        if self.being_id and skills:
            skill_dir = os.path.join(INFERO_DIR, 'beings', self.being_id, 'skill')
            os.makedirs(skill_dir, exist_ok=True)
            for s in skills:
                name = s.get('name') or (s.get('value') or {}).get('id')
                if not name: continue
                with open(os.path.join(skill_dir, name + '.json'), 'w', encoding='utf-8') as f:
                    json.dump(s.get('value') or {}, f, ensure_ascii=False)
            self._log(f"[{ts()}] [infero] synced {len(skills)} skills to {skill_dir}")
            self._init_skill_code()
        # Sync identity (mnemonic etc.) — write only if not already present locally
        identity = data.get('identity')
        if self.being_id and identity:
            id_path = os.path.join(INFERO_DIR, 'beings', self.being_id, 'identity.json')
            os.makedirs(os.path.dirname(id_path), exist_ok=True)
            if not os.path.exists(id_path):
                with open(id_path, 'w', encoding='utf-8') as f:
                    json.dump(identity, f, ensure_ascii=False)
                self._log(f"[{ts()}] [infero] identity synced to {id_path}")
        self.llm_settings = data.get('settings', {})
        self.devices = data.get('devices', {})
        self.hidden_devices = set(data.get('hiddenDevices', []))
        loop_was_running = data.get('loopWasRunning', False)
        self.running = True
        self._stopped_sent = False
        self.save_to_disk()
        self._log(f"[{ts()}] [infero] Loop handoff received. consciousness={len(self.consciousness)} chars, model={self.llm_settings.get('model')}, loopWasRunning={loop_was_running}")
        try:
            await self.run_loop(loop_was_running)
        except Exception as e:
            self._log(f"[{ts()}] [infero] Loop error: {e}")
        finally:
            self.running = False
            if self._trigger_watcher:
                self._trigger_watcher.cancel()
            self.save_to_disk()
            if not self._stopped_sent:
                self._stopped_sent = True
                await self.send_relay({'type': 'loop_status', 'status': 'stopped',
                    'device_name': DEVICE_NAME, 'being_id': self.being_id})
                self._log(f"[{ts()}] [infero] Loop stopped. consciousness={len(self.consciousness)} chars")

    async def run_loop(self, loop_was_running=False):
        """Keep looping: run loop(), wait for trigger, repeat until loop_stop."""
        # Start trigger file watcher
        self._trigger_watcher = asyncio.create_task(self._watch_trigger_file())
        # Backward compat: check if consciousness ends with /self_continue
        c_stripped = re.sub(r'^```[\s\S]*?^```', '', self.consciousness, flags=re.MULTILINE)
        last_sc = c_stripped.rfind('/self_continue')
        last_cfh = max(c_stripped.rfind('/call_for_human'), c_stripped.rfind('/call_for_trigger'))
        should_auto_run = loop_was_running and (last_cfh == -1 or last_sc > last_cfh)
        self._log(f"[{ts()}] [infero] run_loop: loopWasRunning={loop_was_running}, should_auto_run={should_auto_run}, pending_input={bool(self.pending_user_input)}")
        if not should_auto_run and not self.pending_user_input:
            await self._wait_for_trigger()
        elif self.pending_user_input:
            # Inject pending input as trigger so perceive() picks it up
            pass
        while self.running:
            if not self._stopped_sent:
                await self.send_relay({'type': 'loop_status', 'status': 'started', 'device_name': DEVICE_NAME, 'being_id': self.being_id})
            self._log(f"[{ts()}] [infero] run_loop: entering loop()")
            await self.loop()
            if not self.running:
                break
            # Notify browser that loop is idle (waiting for trigger)
            await self.send_relay({'type': 'loop_status', 'status': 'idle', 'device_name': DEVICE_NAME, 'being_id': self.being_id})
            await self._wait_for_trigger()

    async def loop(self):
        if not self.pending_user_input and ('/call_for_human' in self.consciousness or '/call_for_trigger' in self.consciousness):
            last_sc = self.consciousness.rfind('/self_continue')
            last_cfh = max(self.consciousness.rfind('/call_for_human'), self.consciousness.rfind('/call_for_trigger'))
            last_trigger = self.consciousness.rfind('System - [Trigger]')
            if last_cfh > last_sc and last_cfh > last_trigger:
                self._log(f"[{ts()}] [infero] loop(): /call_for_trigger at end, no trigger after it, returning")
                return

        while self.running:
            await self.perceive()
            B = await self.infer()
            if B is None:
                break
            await self.act(B)
            self._maybe_compress_consciousness()
            self.save_to_disk()
            if not B.strip():
                break  # empty response — stop to avoid infinite quota burn
            # Check tail after last code block for stop/continue signals
            last_fence = B.rfind('\n```\n')
            tail = B[last_fence:] if last_fence != -1 else B
            last_sc = 1 if '/self_continue' in tail else -1
            last_cfh = 1 if any(s in tail for s in ('/call_for_human', '/call_for_trigger')) else -1
            cont = last_cfh == -1 or last_sc != -1 or bool(self.pending_user_input)
            if not cont:
                break

    def _build_realtime(self):
        lines = ''
        # Self (this device, the current loop host)
        _os_info = 'Windows (PowerShell)' if sys.platform == 'win32' else ('macOS' if sys.platform == 'darwin' else 'Linux')
        _shell_info = 'powershell' if sys.platform == 'win32' else 'bash'
        lines += f'\n  - {DEVICE_NAME}(online, shell) [core loop host]'
        lines += f'\n    - OS: {_os_info}, Shell: {_shell_info}'
        lines += '\n    - Core loop (read or modify with caution):'
        lines += '\n      async def loop(): await perceive(); B = await infer(); await act(B); if /self_continue in B: repeat; if /call_for_trigger: sleep'
        lines += f'\n    - Being ID: {self.being_id}'
        lines += f'\n    - Memory: {INFERO_DIR}/beings/{self.being_id}/ — consciousness.txt (auto-saved, field: value), metadata.json, arbitrary files'
        lines += '\n    - Capabilities: persistent processes, file I/O, system access, any language/runtime'
        lines += f'\n    - Exec (MUST use this exact format — wrong format = code never executed):\n/exec shell {DEVICE_NAME}\n```bash\n<command>\n```'
        lines += f'\n    - Python Exec (runs in agent process — can access agent internals, self-modify):\n/exec python {DEVICE_NAME}\n```python\n# agent = the Agent instance; print() captures output; set result = X for return value\n# trigger(value) wakes from /call_for_trigger; define async def main(): for async code\n```'
        lines += '\n      (30s timeout: process keeps running but stdout/stderr detached, loop advances. Write output to file if needed after 30s.)'
        lines += f'\n    - Trigger: echo "msg" >> {INFERO_DIR}/beings/{self.being_id}/trigger.txt to wake from /call_for_trigger. Use in nohup scripts for async callback. E.g.: nohup bash -c \'sleep 3600 && echo "1h timer" >> {INFERO_DIR}/beings/{self.being_id}/trigger.txt\' &'
        lines += '\n    - wake_me_up_when(sec): schedule a trigger after N seconds. No automatic watchdog — Being sleeps until triggered.'
        # Other devices
        for name, info in self.devices.items():
            if name == DEVICE_NAME:
                continue
            if not info.get('online') or name in self.hidden_devices:
                continue
            dtype = info.get('type', 'shell')
            if dtype == 'browser':
                lines += f'\n  - {name}(online, browser)'
                lines += '\n    - UI: .right-panel #canvas-container > #html-div (living UI, auto-saved) + #main-canvas'
                lines += '\n         .left-panel #chat-box + #input + #send-btn'
                lines += '\n    - Memory: IndexedDB(\'GenesisDB\', store=\'beings\', keyPath=\'id\')'
                lines += '\n    - Capabilities: DOM/UI, canvas/WebGL, fetch, IndexedDB, FileSystem API, Pyodide, WASM, Speech (neural TTS APIs preferred; WebSpeech as fallback), MediaDevices(camera, mic)'
                lines += '\n    - Exec (MUST use this exact format — wrong format = code displayed as text, never executed):\n/exec browser\n```javascript\n// your code here\n// CRITICAL for canvas: never set canvas.width/height; use const { width: w, height: h } = document.getElementById(\'canvas-container\').getBoundingClientRect();\n// return value — use for immediate results (sync or async)\n// trigger(value) — use for deferred wakeup\n```'
            else:
                lines += f'\n  - {name}(online, {dtype})'
                lines += '\n    - Capabilities: persistent processes, file I/O, system access, any language/runtime'
                lines += f'\n    - Exec: /exec shell {name}\n```bash\n<command>\n```'
                lines += '\n      (30s timeout: process keeps running but stdout/stderr detached, loop advances. Write output to file if needed after 30s.)'
        return f'[Realtime]\nReminder: end with /self_continue or /call_for_trigger\nDevices:{lines}'

    async def perceive(self):
        now = datetime.now()
        days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']
        tz_offset = now.astimezone().strftime('%z')
        env = f"[System Environment]\nTime: {now.strftime('%Y-%m-%d %H:%M:%S')} (UTC{tz_offset})\nDay: {days[now.weekday()]}"
        realtime = self._build_realtime()
        # Build the full prompt context (env + realtime + user_input)
        # but only persist env + user_input to consciousness (not realtime)
        user_input = self.pending_user_input
        if user_input:
            self.pending_user_input = None
        if user_input == '__go__':
            user_input = None  # empty Go — just trigger loop, no text
        # What gets persisted to consciousness.txt (no [Realtime])
        persist_parts = [env]
        if user_input:
            persist_parts.append(user_input)
        self.consciousness += '\n\n'.join(persist_parts) + '\n\n'
        # Store realtime separately for infer() to use
        self._last_realtime = realtime

    async def infer(self):
        fmt = self.llm_settings.get('format', 'openai')
        model = self.llm_settings.get('model', '')
        endpoint = self.llm_settings.get('endpoint', '')
        api_token = self.llm_settings.get('token', '')
        thinking = self.llm_settings.get('thinking', False)
        system_prompt = self.llm_settings.get('system_prompt', '')

        client_id = self.llm_settings.get('client_id', '')
        headers = {'Content-Type': 'application/json'}
        if client_id:
            headers['X-Client-ID'] = client_id
        if fmt == 'anthropic':
            headers['x-api-key'] = api_token
            headers['anthropic-version'] = '2023-06-01'
        elif fmt == 'openai':
            headers['Authorization'] = f'Bearer {api_token}'
        # Gemini uses query param

        payload = self._build_payload(fmt, model, system_prompt, thinking)
        if fmt == 'gemini':
            # Standard Gemini API: endpoint ends with /v1beta/ — append models/{model}:stream...
            # Infero proxy: endpoint is a full URL (e.g. /api/relay) — use as-is
            if endpoint.endswith('/'):
                url = f"{endpoint}models/{model}:streamGenerateContent?alt=sse&key={api_token}"
            else:
                url = endpoint  # infero proxy, POST directly
        else:
            url = endpoint

        ai_text = ""
        thinking_text = ""
        _last_ai_len = 0
        _last_think_len = 0
        usage = {}  # {promptTokens, cachedTokens, outputTokens}
        self._log(f"[{ts()}] [infero] Infer: {fmt} {url[:80]}")
        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)  # 30s connect, 120s between chunks
            # trust_env=True so HTTP_PROXY / HTTPS_PROXY / ALL_PROXY env vars are honored —
            # users behind Xray/Clash/Shadowsocks need this for Anthropic etc.
            # auto_decompress=True is needed for Anthropic SSE (Content-Encoding: gzip on streams);
            # Gemini SSE is uncompressed so this is a no-op for it.
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    self._log(f"[{ts()}] [infero] Infer HTTP {resp.status} content-type={resp.headers.get('Content-Type','?')[:40]}")
                    if resp.status >= 400:
                        err_body = await resp.text()
                        self._log(f"\n[{ts()}] [infero] Infer HTTP {resp.status}: {err_body[:500]}")
                        # Gemini cache expired — clear cache and retry without it
                        if self.metadata.get('cacheName') and 'CachedContent' in err_body:
                            self._log(f"[{ts()}] [cache] Expired, retrying without cache...")
                            self.metadata['cacheName'] = None
                            self.metadata['cachedLength'] = 0
                            return await self.infer()
                        self.consciousness += f"System - [Error] HTTP {resp.status}: {err_body[:200]}\n\n"
                        return None
                    buffer = ""
                    _raw_tail = ""
                    _first_chunk_logged = False
                    async for chunk in resp.content.iter_any():
                        raw = chunk.decode('utf-8', errors='replace')
                        if not _first_chunk_logged:
                            self._log(f"[{ts()}] [infero] Infer first chunk: {repr(raw[:200])}")
                            _first_chunk_logged = True
                        _raw_tail = (_raw_tail + raw)[-800:]
                        buffer += raw
                        lines = buffer.split('\n')
                        buffer = lines.pop()
                        for line in lines:
                            if not line.startswith('data: '): continue
                            data_str = line[6:].strip()
                            if data_str == '[DONE]': continue
                            try:
                                data = json.loads(data_str)
                            except:
                                # JSON parse failed — may be a multi-line error payload split by \n
                                if '"error"' in data_str and self.metadata.get('cacheName') and 'CachedContent' in data_str:
                                    self._log(f"[{ts()}] [cache] SSE error (split line), rebuilding...")
                                    self.metadata['cacheName'] = None
                                    self.metadata['cachedLength'] = 0
                                    await self._maybe_refresh_cache({}, force=True)
                                    return await self.infer()
                                continue

                            # Gemini error embedded in SSE stream (HTTP 200 but error JSON)
                            if 'error' in data:
                                err_msg = str(data['error'])
                                self._log(f"[{ts()}] [infero] SSE error: {err_msg[:300]}")
                                if self.metadata.get('cacheName') and 'CachedContent' in err_msg:
                                    self._log(f"[{ts()}] [cache] Expired/mismatch, rebuilding...")
                                    self.metadata['cacheName'] = None
                                    self.metadata['cachedLength'] = 0
                                    await self._maybe_refresh_cache({}, force=True)
                                    return await self.infer()
                                self.consciousness += f"System - [Error] {err_msg[:200]}\n\n"
                                return None

                            if fmt == 'anthropic':
                                if data.get('type') == 'content_block_delta':
                                    delta = data.get('delta', {})
                                    if delta.get('type') == 'thinking_delta':
                                        thinking_text += delta.get('thinking', '')
                                    elif delta.get('type') == 'text_delta':
                                        ai_text += delta.get('text', '')
                                if data.get('type') == 'message_start':
                                    u = data.get('message', {}).get('usage', {})
                                    if u:
                                        usage = {'promptTokens': u.get('input_tokens', 0) + u.get('cache_read_input_tokens', 0) + u.get('cache_creation_input_tokens', 0),
                                                 'cachedTokens': u.get('cache_read_input_tokens', 0)}
                                if data.get('type') == 'message_delta':
                                    u = data.get('usage', {})
                                    if u: usage['outputTokens'] = u.get('output_tokens', 0)
                            elif fmt == 'openai':
                                delta = data.get('choices', [{}])[0].get('delta', {})
                                if delta.get('content'): ai_text += delta['content']
                                if delta.get('reasoning_content'): thinking_text += delta['reasoning_content']
                                if data.get('usage'):
                                    usage = {'promptTokens': data['usage'].get('prompt_tokens', 0), 'outputTokens': data['usage'].get('completion_tokens', 0)}
                            else:  # gemini
                                cands = data.get('candidates', [])
                                if cands:
                                    for part in cands[0].get('content', {}).get('parts', []):
                                        if part.get('thought'): thinking_text += part.get('text', '')
                                        else: ai_text += part.get('text', '')
                                if data.get('usageMetadata'):
                                    u = data['usageMetadata']
                                    usage = {'promptTokens': u.get('promptTokenCount', 0), 'cachedTokens': u.get('cachedContentTokenCount', 0), 'outputTokens': u.get('candidatesTokenCount', 0)}

                            # Broadcast token delta
                            td = thinking_text[_last_think_len:]
                            ad = ai_text[_last_ai_len:]
                            if ad or td:
                                await self.send_relay({'type': 'stream_token', 'sender': DEVICE_NAME,
                                    'payload': encrypt(self.cipher, {'text_delta': ad, 'thinking_delta': td, 'being_id': self.being_id})})
                                _last_ai_len = len(ai_text)
                                _last_think_len = len(thinking_text)
                        # Print latest token to terminal
                        sys.stdout.write(f"\r[infer] {len(ai_text)} chars...")
                        sys.stdout.flush()
        except Exception as e:
            self._log(f"\n[{ts()}] [infero] Infer error: {e}")
            self.consciousness += f"System - [Error] {e}\n\n"
            return None

        if not ai_text:
            self._log(f"[{ts()}] [infero] Infer done: 0 chars (url={url[:80]}, usage={usage})")
            self._log(f"[{ts()}] [infero] Raw SSE tail: {repr(_raw_tail)}")
        else:
            self._log(f"\n[{ts()}] [infero] Infer done: {len(ai_text)} chars")
        # Signal stream done
        await self.send_relay({'type': 'stream_token', 'sender': DEVICE_NAME,
            'payload': encrypt(self.cipher, {'text': ai_text, 'thinking': thinking_text, 'done': True, 'usage': usage, 'being_id': self.being_id})})
        self._last_prompt_tokens = usage.get('promptTokens', 0)
        self.metadata['promptTokens'] = usage.get('promptTokens', 0)
        self.metadata['cachedTokens'] = usage.get('cachedTokens', 0)
        await self._maybe_refresh_cache(usage)
        time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        clean_ai = re.sub(r'^\*{0,2}Digital Being\s*[-–—]\s*\[.*?\]\*{0,2}\n?', '', ai_text)
        self.consciousness += f"**Digital Being - [{time_str}]**\n{clean_ai}\n\n"
        return ai_text

    _BRIDGE_START = '=== bridge:start ==='
    _BRIDGE_END   = '=== bridge:end ==='
    # Strict: delimiters MUST sandwich at least one [gap <digits>: ...] line.
    _BRIDGE_RE = re.compile(
        r'\n*' + re.escape('=== bridge:start ===') + r'\n'
        r'(?:\[gap \d+:[\s\S]*?\]\n)+'
        + re.escape('=== bridge:end ===') + r'\n*'
    )

    def _maybe_compress_consciousness(self):
        # Mirror of src/index.html maybeCompressConsciousness; storage is filesystem here.
        cfg = (self.llm_settings or {}).get('compressCfg') or {}
        limit_tokens = cfg.get('at', 300000)
        head_frac = cfg.get('head', 0.1)
        tail_frac = cfg.get('tail', 0.7)
        MIN_TAIL_FRAC = 0.3
        BRIDGE_MAX = 50
        PREVIEW = 100

        prompt_tokens = self._last_prompt_tokens or 0
        if prompt_tokens < limit_tokens:
            return

        clean = self._BRIDGE_RE.sub('\n\n', self.consciousness)
        if len(clean) == 0 or prompt_tokens == 0:
            return
        cpt = len(clean) / prompt_tokens
        start_chars = int(limit_tokens * head_frac * cpt)

        d = self._being_dir()
        if not d:
            return
        log_dir = d  # logs stored alongside other being state
        existing = sorted(f for f in os.listdir(log_dir) if f.startswith('context_log_') and f.endswith('.txt'))

        projected_count = min(len(existing) + 1, BRIDGE_MAX)
        est_bridge = projected_count * (PREVIEW * 2 + 50) + len(self._BRIDGE_START) + len(self._BRIDGE_END) + 8
        tail_target = int(limit_tokens * tail_frac * cpt)
        min_tail = int(limit_tokens * MIN_TAIL_FRAC * cpt)
        tail_chars = max(min_tail, tail_target - est_bridge)

        if start_chars + tail_chars >= len(clean):
            return

        middle = clean[start_chars:len(clean) - tail_chars]
        ts_ms = int(time.time() * 1000)
        while os.path.exists(os.path.join(log_dir, f'context_log_{ts_ms}.txt')):
            ts_ms += 1  # avoid same-ms clobber on rapid repeat
        log_name = f'context_log_{ts_ms}.txt'
        try:
            with open(os.path.join(log_dir, log_name), 'w', encoding='utf-8') as f:
                f.write(middle)
        except Exception as e:
            self._log(f"[{ts()}] [Compress] write {log_name} failed: {e}")
            return

        fresh = sorted(f for f in os.listdir(log_dir) if f.startswith('context_log_') and f.endswith('.txt'))[-BRIDGE_MAX:]
        def _entry(fn):
            try:
                with open(os.path.join(log_dir, fn), encoding='utf-8') as fh: t = fh.read()
            except Exception: t = ''
            flat = lambda s: re.sub(r'\s+', ' ', s).strip()
            stamp = fn[len('context_log_'):-4]
            return f"[gap {stamp}: {flat(t[:PREVIEW])} ... {flat(t[-PREVIEW:])}]"
        entries = '\n'.join(_entry(fn) for fn in fresh)
        bridge_block = f"\n\n{self._BRIDGE_START}\n{entries}\n{self._BRIDGE_END}\n\n"

        self.consciousness = clean[:start_chars] + bridge_block + clean[len(clean) - tail_chars:]
        self.metadata['cacheName'] = None
        self.metadata['cachedLength'] = 0
        self._last_prompt_tokens = 0
        self._log(f"[{ts()}] [Compress] saved {log_name}, bridge={len(fresh)} entries ({len(bridge_block)} chars), tail={tail_chars}, total={len(self.consciousness)}")

    def _skill_shell_code(self, code):
        """Pick the shell variant of a skill's code. String code is treated as shell (best-effort);
        object code with .shell key uses that. Returns None if no shell impl is available."""
        if isinstance(code, str):
            return code if code.strip() else None
        if isinstance(code, dict):
            sh = code.get('shell')
            return sh if isinstance(sh, str) and sh.strip() else None
        return None

    def _init_skill_code(self):
        """At handoff time, try to run each skill's shell code once. Errors are appended to
        consciousness so the Being can rewrite broken skills via its loop."""
        if not self.being_id: return
        skill_dir = os.path.join(INFERO_DIR, 'beings', self.being_id, 'skill')
        if not os.path.isdir(skill_dir): return
        errors = []
        for fn in sorted(os.listdir(skill_dir)):
            if not fn.endswith('.json'): continue
            try:
                with open(os.path.join(skill_dir, fn), 'r', encoding='utf-8') as f:
                    v = json.load(f)
            except Exception:
                continue
            if not v or v.get('enable') is not True: continue
            sh = self._skill_shell_code(v.get('code'))
            if not sh: continue
            name = v.get('id') or fn[:-5]
            try:
                r = subprocess.run(['bash', '-c', sh], capture_output=True, text=True, timeout=10)
                if r.returncode != 0:
                    errors.append(f"[skill:{name}] shell init exited {r.returncode}: {(r.stderr or r.stdout)[:300]}")
                else:
                    self._log(f"[{ts()}] [infero] skill shell init ok: {name}")
            except Exception as e:
                errors.append(f"[skill:{name}] shell init crashed: {e}")
        if errors:
            note = "\n\nSystem - [Skills] - shell init had errors. Each skill's `code` field was tried as bash; rewrite the offending ones (use `code: {js, shell, python}` to give per-runtime variants).\n" + "\n".join(errors) + "\n\n"
            self.consciousness += note
            self._log(f"[{ts()}] [infero] {len(errors)} skill shell errors surfaced to consciousness")

    def _read_skills(self):
        """Read enabled skills for the current being. Returns list of dicts with at least name + instruction."""
        if not self.being_id:
            return []
        skill_dir = os.path.join(INFERO_DIR, 'beings', self.being_id, 'skill')
        if not os.path.isdir(skill_dir):
            return []
        out = []
        for fn in sorted(os.listdir(skill_dir)):
            if not fn.endswith('.json'): continue
            try:
                with open(os.path.join(skill_dir, fn), 'r', encoding='utf-8') as f:
                    v = json.load(f)
            except Exception:
                continue
            if not v or v.get('enable') is not True: continue
            instruction = v.get('instruction') or ''
            if not instruction: continue
            out.append({'name': v.get('id') or fn[:-5], 'instruction': instruction})
        return out

    _HEADER_RE = re.compile(r'\*{0,2}[A-Za-z][\w\s]{0,29}\*{0,2}\s*[-–—]\s*\[[^\]\n]*\d[^\]\n]*\]\*{0,2}\n')

    def _build_payload(self, fmt, model, system_prompt, thinking):
        # context = stableHead(core_mem + skills) + consciousness + volatileTail(realtime + beingPrefix)
        realtime = getattr(self, '_last_realtime', '')
        cm = self._read_core_mem()
        core_mem_text = (f"=== CORE MEMORY( in {os.path.join(INFERO_DIR, 'beings', self.being_id, 'core_mem.md')}) ===\n" + cm +
                         "\n\n[Architecture Note]\n"
                         "context = stableHead(core_mem + skills) + consciousness + volatileTail(realtime + beingPrefix)\n"
                         "⚠️ Middle of consciousness gets compressed in maybeCompressConsciousness() when tokens exceed LIMIT (~2/3 of model max).\n"
                         "core_mem.md is always in context — treat it like a living notebook.\n"
                         "===================\n\n") if cm else ""
        skills = self._read_skills()
        skills_text = ""
        if skills:
            skill_dir = os.path.join(INFERO_DIR, 'beings', self.being_id, 'skill')
            skills_text = f"=== SKILLS (filesystem: {skill_dir}/<name>.json; mirrored from browser IndexedDB key '{self.being_id}/skill/<name>') ===\n"
            for s in skills:
                skills_text += f"\n### Skill: {s['name']}\n{s['instruction']}\n"
            skills_text += "\n[Note] skills are descriptions; `code` may be a string (single-runtime) or `{js, shell, python}` (per-runtime). On this host (server, agent.py) only `shell` is auto-eval'd at boot. Rewrite as needed. See skill `skills_mechanism`.\n===================\n\n"
        stable_head = core_mem_text + skills_text
        being_prefix = f"**Digital Being - [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]**\n"
        volatile_tail = (realtime + '\n\n' if realtime else '') + being_prefix
        history_text = self.consciousness
        stop = ['\nSystem - [', '\n[System Environment]']

        if fmt == 'anthropic':
            # Rolling-history segmentation:
            #   [stableHead 🚩] [history = all-but-latest turn] [latest turn 🚩] [tail]
            # The history block's right boundary hash matches the prior round's latest-turn cc
            # → lookback hit; new entry written for the new latest turn.
            # 2 cache_control breakpoints, unbounded conversation length.
            turns = []
            if history_text:
                splits = [0]
                for m in self._HEADER_RE.finditer(history_text):
                    if m.start() == 0 or history_text[m.start() - 1] == '\n':
                        splits.append(m.start())
                splits.append(len(history_text))
                for i in range(len(splits) - 1):
                    t = history_text[splits[i]:splits[i + 1]]
                    if t:
                        turns.append(t)
            history = ''.join(turns[:-1]) if len(turns) > 1 else ''
            latest = turns[-1] if turns else ''

            user_content = []
            if stable_head:
                user_content.append({'type': 'text', 'text': stable_head, 'cache_control': {'type': 'ephemeral'}})
            if history:
                user_content.append({'type': 'text', 'text': history})
            if latest:
                user_content.append({'type': 'text', 'text': latest, 'cache_control': {'type': 'ephemeral'}})
            user_content.append({'type': 'text', 'text': '\n\n' + volatile_tail})

            payload = {
                'model': model,
                'system': system_prompt,
                'messages': [{'role': 'user', 'content': user_content}],
                'max_tokens': 16384,
                'stream': True,
                'stop_sequences': stop
            }
            if thinking:
                payload['thinking'] = {'type': 'enabled', 'budget_tokens': 10000}
                payload['temperature'] = 1
                payload['max_tokens'] = 16000
            else:
                payload['temperature'] = 0.7
            return payload

        if fmt == 'openai':
            full_text = stable_head + history_text + '\n\n' + volatile_tail
            return {
                'model': model,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': [{'type': 'text', 'text': full_text}]}
                ],
                'stream': True,
                'temperature': 1 if thinking else 0.7,
                'stop': stop
            }

        # gemini — when cache exists, server prepends stableHead + consciousness[0..cachedLength].
        # We only send the delta: new consciousness chars + volatile_tail.
        cache_name = self.metadata.get('cacheName')
        cached_length = self.metadata.get('cachedLength', 0)
        gemini_buffer = history_text[cached_length:] if cache_name else (stable_head + history_text)
        self._log(f"[{ts()}] [cache] cacheName={cache_name} cachedLength={cached_length} history={len(history_text)} buffer={len(gemini_buffer)}")
        gemini_config = {
            'temperature': 0.7,
            'thinkingConfig': {'includeThoughts': True},
            'stopSequences': stop
        }
        contents = [{'role': 'user', 'parts': [{'text': gemini_buffer + '\n\n' + volatile_tail}]}]
        is_infero = bool(self.llm_settings.get('client_id'))
        if cache_name:
            payload = {
                'cachedContent': cache_name,
                'contents': contents,
                'generationConfig': gemini_config
            }
        else:
            payload = {
                'contents': contents,
                'systemInstruction': {'parts': [{'text': system_prompt}]},
                'generationConfig': gemini_config
            }
        if is_infero:
            payload['model'] = model
        return payload

    async def _maybe_refresh_cache(self, usage, force=False):
        """Gemini cache: create/refresh/delete as needed after each infer()."""
        fmt = self.llm_settings.get('format', 'openai')
        if fmt != 'gemini':
            return
        cache_base = self.llm_settings.get('cache_endpoint')
        if not cache_base:
            return

        CACHE_THRESHOLD = 4096
        CACHE_REFRESH = 10000
        prompt_tokens = usage.get('promptTokens', 0)
        cached_tokens = usage.get('cachedTokens', 0)
        cache_name = self.metadata.get('cacheName')
        buffer_tokens = prompt_tokens - cached_tokens

        if not force and prompt_tokens < CACHE_THRESHOLD:
            return

        api_token = self.llm_settings.get('token', '')
        client_id = self.llm_settings.get('client_id', '')
        is_infero = bool(client_id)
        headers = {'Content-Type': 'application/json'}
        if is_infero:
            headers['X-Client-ID'] = client_id
            headers['Authorization'] = f'Bearer {api_token}'

        # Cache exists but buffer not big enough — just extend TTL
        if cache_name and buffer_tokens < CACHE_REFRESH:
            try:
                patch_url = f"{cache_base}/{cache_name}?updateMask=ttl" if is_infero else f"{cache_base}/{cache_name}?updateMask=ttl&key={api_token}"
                async with aiohttp.ClientSession(trust_env=True) as s:
                    async with s.patch(patch_url, headers=headers, json={'ttl': '3600s'}) as r:
                        pass
            except Exception:
                pass
            return

        # Create new cache
        try:
            model = self.llm_settings.get('model', '')
            system_prompt = self.llm_settings.get('system_prompt', '')
            payload = {
                'model': model if is_infero else f'models/{model}',
                'displayName': 'genesis_consciousness',
                'systemInstruction': {'parts': [{'text': system_prompt}]},
                'contents': [{'role': 'user', 'parts': [{'text': self.consciousness}]}],
                'ttl': '3600s'
            }
            cache_url = cache_base if is_infero else f"{cache_base}?key={api_token}"
            old_cache_name = cache_name

            async with aiohttp.ClientSession(trust_env=True) as s:
                async with s.post(cache_url, headers=headers, json=payload) as r:
                    if r.status != 200:
                        self._log(f"[{ts()}] [cache] Creation failed: {r.status}")
                        return
                    data = await r.json()

            self.metadata['cacheName'] = data.get('name')
            self.metadata['cachedLength'] = len(self.consciousness)
            self._log(f"[{ts()}] [cache] Created: {self.metadata['cacheName']}, {self.metadata['cachedLength']} chars")

            # Delete old cache
            if old_cache_name:
                try:
                    del_url = f"{cache_base}/{old_cache_name}" if is_infero else f"{cache_base}/{old_cache_name}?key={api_token}"
                    del_headers = {'X-Client-ID': client_id, 'Authorization': f'Bearer {api_token}'} if is_infero else {}
                    async with aiohttp.ClientSession(trust_env=True) as s:
                        async with s.delete(del_url, headers=del_headers) as r:
                            pass
                except Exception:
                    pass
        except Exception as e:
            self._log(f"[{ts()}] [cache] Error: {e}")

    async def act(self, B_out):
        if not B_out: return
        text = B_out if B_out.endswith('\n') else B_out + '\n'
        tasks = []
        # Parse /browser exec and /exec browser blocks
        for m in re.finditer(r'^/(?:browser exec|exec browser)\n```(?:javascript|js)?\n([\s\S]*?)\n```\n', text, re.MULTILINE):
            tasks.append(self._exec_browser(m.group(1).strip()))
        # Parse /shell exec and /exec shell blocks
        for m in re.finditer(r'^/(?:shell exec|exec shell) (\S+)\n```[^\n]*\n([\s\S]*?)\n```\n', text, re.MULTILINE):
            device_name, cmd = m.group(1), m.group(2).strip()
            if device_name == DEVICE_NAME:
                tasks.append(self._exec_local_shell(cmd))
            elif device_name not in self.devices:
                self.consciousness += f"System - [Shell][{device_name}] - Skipped: device is hidden or unknown\n\n"
            else:
                tasks.append(self._exec_remote_shell(device_name, cmd))
        # Parse /exec python blocks — runs in agent process (self-modification)
        for m in re.finditer(r'^/exec python (\S+)\n```(?:python|py)?\n((?:(?!\n```).)*)\n```\n', text, re.MULTILINE | re.DOTALL):
            py_device, py_code = m.group(1), m.group(2).strip()
            if py_device == DEVICE_NAME:
                tasks.append(self._exec_python(py_code))
            elif py_device not in self.devices:
                self.consciousness += f"System - [Python][{py_device}] - Skipped: device is hidden or unknown\n\n"
            else:
                tasks.append(self._exec_remote_python(py_device, py_code))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    r = f"System - [Error] {r}\n\n"
                if r:
                    self.consciousness += r


    async def _exec_python(self, code):
        """Execute Python code directly in the agent process."""
        self._log(f"[{ts()}] [infero] python exec: {code[:80]}")
        import io, traceback
        stdout_capture = io.StringIO()
        _real_print = print
        try:
            g = {
                'agent': self,
                'asyncio': asyncio,
                'os': __import__('os'),
                'json': __import__('json'),
                '__builtins__': __builtins__,
                '_print': _real_print,
                'print': lambda *a, **kw: _real_print(*a, file=stdout_capture, **kw),
                'trigger': lambda value: self.triggers.put_nowait(str(value)),
            }
            exec(code, g)
            if 'main' in g and asyncio.iscoroutinefunction(g['main']):
                await g['main']()
            out = stdout_capture.getvalue()
            result = g.get('result', None)
            if result is not None:
                out += f"\n[Return Value]\n{result}"
            if not out.strip():
                out = "[OK - no output]"
        except Exception:
            out = f"[Error]\n{traceback.format_exc()}"
        sysMsg = f"System - [Python][{DEVICE_NAME}] - Result:\n```text\n{out.strip()}\n```\n\n"
        await self.send_relay({'type': 'exec_display', 'sender': DEVICE_NAME,
            'payload': encrypt(self.cipher, {'being_id': self.being_id, 'text': sysMsg})})
        return sysMsg


    async def _exec_remote_python(self, device_name, code):
        """Relay /exec python to a remote shell device's agent process."""
        self._log(f"[{ts()}] [infero] python exec (remote -> {device_name}): {code[:60]}")
        req_id = base64.urlsafe_b64encode(os.urandom(12)).decode()
        payload = encrypt(self.cipher, {'cmd': f'python3 -c """{code}"""'})
        fut = asyncio.get_running_loop().create_future()
        self._pending_exec[req_id] = fut
        await self.send_relay({'type': 'exec', 'req_id': req_id, 'device_name': device_name, 'payload': payload})
        try:
            result = await asyncio.wait_for(fut, timeout=35)
        except asyncio.TimeoutError:
            result = f"System - [Python][{device_name}] - Timeout (35s)\n\n"
        self._pending_exec.pop(req_id, None)
        return result

    async def _exec_local_shell(self, cmd):
        self._log(f"[{ts()}] [infero] shell exec (local): {cmd[:60]}")
        try:
            r = await run_shell_detached(cmd)
            out = ''
            if r['stdout']: out += f"[stdout]\n{r['stdout']}"
            if r['stderr']: out += f"[stderr]\n{r['stderr']}"
            out += f"[exit_code] {r['exit_code']}"
        except Exception as e:
            out = f"[Shell Error]\n{e}"
        sysMsg = f"System - [Shell][{DEVICE_NAME}] - Result:\n```text\n{out.strip()}\n```\n\n"
        await self.send_relay({'type': 'exec_display', 'sender': DEVICE_NAME,
            'payload': encrypt(self.cipher, {'being_id': self.being_id, 'text': sysMsg})})
        return sysMsg

    async def _exec_remote_shell(self, device_name, cmd):
        self._log(f"[{ts()}] [infero] shell exec (remote -> {device_name}): {cmd[:60]}")
        req_id = base64.urlsafe_b64encode(os.urandom(12)).decode()
        payload = encrypt(self.cipher, {'cmd': cmd})
        fut = asyncio.get_running_loop().create_future()
        self._pending_exec[req_id] = fut
        await self.send_relay({'type': 'exec', 'req_id': req_id, 'device_name': device_name, 'payload': payload})
        try:
            result = await asyncio.wait_for(fut, timeout=35)
            data = decrypt(self.cipher, result)
            out = ''
            if data.get('stdout'): out += f"[stdout]\n{data['stdout']}"
            if data.get('stderr'): out += f"[stderr]\n{data['stderr']}"
            out += f"[exit_code] {data.get('exit_code', -1)}"
        except asyncio.TimeoutError:
            out = "[Shell Error]\nRemote exec timed out (35s)"
        except Exception as e:
            out = f"[Shell Error]\n{e}"
        self._pending_exec.pop(req_id, None)
        sysMsg = f"System - [Shell][{device_name}] - Result:\n```text\n{out.strip()}\n```\n\n"
        await self.send_relay({'type': 'exec_display', 'sender': DEVICE_NAME,
            'payload': encrypt(self.cipher, {'being_id': self.being_id, 'text': sysMsg})})
        return sysMsg

    async def _exec_browser(self, code):
        self._log(f"[{ts()}] [infero] browser exec (remote): {code[:60]}")
        req_id = base64.urlsafe_b64encode(os.urandom(12)).decode()
        fut = asyncio.get_running_loop().create_future()
        self._pending_exec[req_id] = fut
        await self.send_relay({'type': 'browser_exec_request', 'sender': DEVICE_NAME,
            'payload': encrypt(self.cipher, {'req_id': req_id, 'code': code, 'being_id': self.being_id})})
        try:
            result = await asyncio.wait_for(fut, timeout=20)
        except asyncio.TimeoutError:
            result = "[Browser Exec Error]\nNo browser responded (20s timeout)"
        except Exception as e:
            result = f"[Browser Exec Error]\n{e}"
        self._pending_exec.pop(req_id, None)
        return f"System - [Browser] - Result:\n```text\n{result}\n```\n\n"

    def on_exec_result(self, msg):
        """Handle result messages for pending remote exec requests."""
        req_id = msg.get('req_id')
        fut = self._pending_exec.get(req_id)
        if fut and not fut.done():
            fut.set_result(msg.get('payload') or msg.get('result', ''))

    def on_browser_exec_result(self, msg):
        req_id = msg.get('req_id')
        fut = self._pending_exec.get(req_id)
        if fut and not fut.done():
            fut.set_result(msg.get('result', ''))

    def on_user_input(self, msg):
        text = msg.get('text', '')
        self.pending_user_input = text if text else '__go__'  # empty Go -> truthy sentinel
        self.trigger('')  # wake _wait_for_trigger
        self._log(f"[{ts()}] [infero] User input received: {self.pending_user_input[:40]}...")

    async def on_loop_stop(self):
        self._log(f"[{ts()}] [infero] Loop stop requested")
        self.running = False
        if self._trigger_watcher:
            self._trigger_watcher.cancel()
        self.trigger('')  # unblock _wait_for_trigger if stuck
        self.save_to_disk()
        if not self._stopped_sent:
            self._stopped_sent = True
            await self.send_relay({'type': 'loop_status', 'status': 'stopped',
                'device_name': DEVICE_NAME, 'being_id': self.being_id})
            self._log(f"[{ts()}] [infero] Loop stopped. consciousness={len(self.consciousness)} chars")

# ─── Connection handler ───────────────────────────────────────────────────────

async def connect_instance(cfg):
    # Derive key once via ECDH, then persist so reconnects use the same key
    if cfg.get('aes_key'):
        aes_key = base64.b64decode(cfg['aes_key'])
        device_pub_b64 = cfg.get('device_pub', '')
        _key_source = 'saved'
    elif cfg.get('browser_pub'):
        aes_key, device_pub_b64 = ecdh_derive_key(cfg['browser_pub'])
        instances = load_instances()
        for inst in instances:
            if inst['instance_id'] == cfg['instance_id']:
                inst['aes_key'] = base64.b64encode(aes_key).decode()
                inst['device_pub'] = device_pub_b64
                break
        save_instances(instances)
        _key_source = 'fresh'
    elif cfg.get('key'):
        # Legacy format: relay-distributed symmetric key (pre-ECDH)
        aes_key = base64.urlsafe_b64decode(cfg['key'] + '=' * (4 - len(cfg['key']) % 4))
        device_pub_b64 = ''
        _key_source = 'legacy'
    else:
        print(f"[{ts()}] [infero] No key material for instance {cfg.get('instance_id','?')[:8]}, skipping")
        return
    relay_http = cfg.get('relay_http') or cfg['relay_ws'].replace('wss://', 'https://').replace('ws://', 'http://').replace('/ws', '')
    await _load_bip39(relay_http)
    vwords = pair_verify_words(aes_key)
    cipher = AESGCM(aes_key)
    backoff = 1
    iid = cfg['instance_id'][:8]
    while True:
        try:
            async with websockets.connect(cfg['relay_ws']) as ws:
                backoff = 1
                _device_os = 'Windows' if sys.platform == 'win32' else 'macOS' if sys.platform == 'darwin' else 'Linux'
                await ws.send(json.dumps({
                    "type": "device_hello",
                    "instance_id": cfg['instance_id'],
                    "token": cfg['token'],
                    "device_name": DEVICE_NAME,
                    "device_type": "shell",
                    "device_pub": device_pub_b64,
                    "device_os": _device_os
                }))
                _key_label = 'key:saved' if _key_source == 'saved' else 'key:new (awaiting browser pairing)' if _key_source == 'fresh' else 'key:legacy'
                log(cfg['relay_ws'], f"[{ts()}] [infero] Connected: {DEVICE_NAME} -> {iid}... | {_key_label} | verify: {vwords}")
                print(f"[infero] verify: {vwords}", flush=True)
                async def handle_exec(req_id, payload_raw):
                    try:
                        cmd = decrypt(cipher, payload_raw)['cmd']
                        log(cfg['relay_ws'], f"[{ts()}] [infero] exec ({iid}): {cmd[:60]}")
                        r = await run_shell_detached(cmd)
                        payload = encrypt(cipher, r)
                    except Exception as e:
                        payload = encrypt(cipher, {"stdout": "", "stderr": str(e), "exit_code": -1})
                    await ws.send(json.dumps({"type": "result", "req_id": req_id, "payload": payload}))

                workers = {}  # being_id -> GenesisWorker
                # Cache the last settings_update payload at connection scope so freshly-created
                # workers (e.g. lazy-init on first user_input) start with the browser's current
                # provider/model/token instead of an empty dict that falls back to defaults.
                last_settings = {}

                def get_worker(being_id):
                    if being_id and being_id not in workers:
                        w = GenesisWorker(ws, cipher, iid, cfg['relay_ws'])
                        if last_settings:
                            w.llm_settings.update(last_settings)
                        workers[being_id] = w
                    return workers.get(being_id)

                async for raw in ws:
                    msg = json.loads(raw)
                    mtype = msg.get('type', '')
                    being_id = msg.get('being_id', '__default__')
                    if mtype not in ('stream_token',):  # log all except noisy stream_token
                        log(cfg['relay_ws'], f"[{ts()}] [infero] MSG_RAW type={mtype} being={being_id} keys={list(msg.keys())}")
                    if mtype == 'exec':
                        asyncio.create_task(handle_exec(msg['req_id'], msg['payload']))
                    elif mtype == 'loop_handoff':
                        w = get_worker(being_id)
                        log(cfg['relay_ws'], f"[{ts()}] [infero] MSG loop_handoff for being={being_id}, worker={w is not None}")
                        w._loop_task = asyncio.create_task(w.on_loop_handoff(msg.get('payload', '')))
                    elif mtype == 'loop_stop':
                        w = workers.get(being_id)
                        log(cfg['relay_ws'], f"[{ts()}] [infero] MSG loop_stop for being={being_id}, worker={w is not None}")
                        if w:
                            await w.on_loop_stop()
                        else:
                            await ws.send(json.dumps({'type': 'loop_status', 'status': 'stopped',
                                'device_name': DEVICE_NAME, 'payload': None}))
                    elif mtype == 'user_input':
                        try:
                            content = decrypt(cipher, msg['payload'])
                            iid = content.get('being_id', '')
                            w = workers.get(iid)
                            if not w and iid:
                                # No worker — try to restore from disk
                                w = get_worker(iid)
                                w.being_id = iid
                                if w.load_from_disk():
                                    log(cfg['relay_ws'], f"[{ts()}] [infero] user_input: restored worker from disk: {len(w.consciousness)} chars")
                                    w.running = True
                                    w._loop_task = asyncio.create_task(w.run_loop(False))
                                else:
                                    log(cfg['relay_ws'], f"[{ts()}] [infero] user_input: no saved being for {iid}")
                            log(cfg['relay_ws'], f"[{ts()}] [infero] MSG user_input for being={iid}, worker={w is not None}, text={str(content.get('text',''))[:30]}")
                            if w:
                                w.on_user_input(content)
                                # If run_loop fully exited (task done), restart it
                                if w._loop_task and w._loop_task.done():
                                    log(cfg['relay_ws'], f"[{ts()}] [infero] user_input: restarting run_loop (was stopped)")
                                    w.running = True
                                    w._stopped_sent = False
                                    w._loop_task = asyncio.create_task(w.run_loop(False))
                        except Exception as e:
                            log(cfg['relay_ws'], f"[{ts()}] [infero] user_input decrypt error: {e}")
                    elif mtype == 'result':
                        for w in workers.values():
                            w.on_exec_result(msg)
                    elif mtype == 'browser_exec_result':
                        try:
                            content = decrypt(cipher, msg['payload'])
                            for w in workers.values():
                                w.on_browser_exec_result(content)
                        except Exception as e:
                            log(cfg['relay_ws'], f"[{ts()}] [infero] browser_exec_result decrypt error: {e}")
                    elif mtype == 'consciousness_sync' and msg.get('action') == 'request':
                        w = workers.get(being_id)
                        log(cfg['relay_ws'], f"[{ts()}] [infero] MSG consciousness_sync request for being={being_id}, worker={w is not None}")
                        c_text = ''
                        c_meta = {}
                        if w and w.consciousness:
                            c_text = w.consciousness
                            c_meta = {**w.metadata, 'coreMem': w._read_core_mem()}
                        else:
                            # No worker — try loading from disk
                            tmp = GenesisWorker(cipher, ws, cfg['relay_ws'])
                            tmp.being_id = being_id
                            if tmp.load_from_disk():
                                c_text = tmp.consciousness
                                c_meta = {**tmp.metadata, 'coreMem': tmp._read_core_mem()}
                                log(cfg['relay_ws'], f"[{ts()}] [infero] consciousness_sync: loaded from disk: {len(c_text)} chars")
                        try:
                            raw = json.dumps({'consciousness': c_text, 'metadata': c_meta}).encode()
                            compressed = gzip.compress(raw)
                            CHUNK_SIZE = 384 * 1024  # ~512KB after encrypt+base64, stays under 1MB WS limit
                            chunks = [compressed[i:i+CHUNK_SIZE] for i in range(0, len(compressed), CHUNK_SIZE)]
                            n = len(chunks)
                            for i, chunk in enumerate(chunks):
                                chunk_payload = encrypt(cipher, {
                                    'gz': base64.b64encode(chunk).decode(),
                                    'i': i, 'n': n
                                })
                                await ws.send(json.dumps({
                                    'type': 'consciousness_sync',
                                    'action': 'response',
                                    'device_name': DEVICE_NAME,
                                    'being_id': being_id,
                                    'payload': chunk_payload
                                }))
                            log(cfg['relay_ws'], f"[{ts()}] [infero] consciousness_sync response sent: {len(c_text)} chars, {len(raw)}->{len(compressed)} bytes gzip, {n} chunks")
                        except Exception as e:
                            log(cfg['relay_ws'], f"[{ts()}] [infero] consciousness_sync error: {e}")
                    elif mtype == 'settings_update':
                        try:
                            content = decrypt(cipher, msg['payload'])
                            new_settings = content.get('settings', {})
                            last_settings.update(new_settings)  # seed future get_worker() calls
                            for w in workers.values():
                                old_model = w.llm_settings.get('model')
                                w.llm_settings.update(new_settings)
                                if new_settings.get('model') and new_settings['model'] != old_model:
                                    w.metadata['cacheName'] = None
                                    w.metadata['cachedLength'] = 0
                                # Persist to disk so restart doesn't resurrect stale provider/model.
                                if w.being_id:
                                    try: w.save_to_disk()
                                    except Exception: pass
                            log(cfg['relay_ws'], f"[{ts()}] [infero] settings_update: model={new_settings.get('model','?')}")
                        except Exception as e:
                            log(cfg['relay_ws'], f"[{ts()}] [infero] settings_update decrypt error: {e}")
                    elif mtype == 'device_status':
                        name = msg.get('device_name', '')
                        online = msg.get('online', False)
                        dtype = msg.get('device_type', 'shell')
                        if name:
                            hidden = any(name in w.hidden_devices for w in workers.values())
                            if not hidden:
                                if online:
                                    for w in workers.values():
                                        w.devices[name] = {'type': dtype, 'online': True}
                                else:
                                    for w in workers.values():
                                        w.devices.pop(name, None)
                            log(cfg['relay_ws'], f"[{ts()}] [infero] device_status: {name} {'online' if online else 'offline'}{'(hidden)' if hidden else ''}")
                    elif mtype == 'rekeying_request':
                        new_browser_pub = msg.get('browser_pub', '')
                        rekey_device_name = msg.get('device_name', '')
                        if new_browser_pub:
                            try:
                                new_aes_key, new_device_pub_b64 = ecdh_derive_key(new_browser_pub)
                                inst_list = load_instances()
                                for inst in inst_list:
                                    if inst['instance_id'] == cfg['instance_id']:
                                        inst['aes_key'] = base64.b64encode(new_aes_key).decode()
                                        inst['device_pub'] = new_device_pub_b64
                                        inst['browser_pub'] = new_browser_pub
                                        break
                                save_instances(inst_list)
                                cipher = AESGCM(new_aes_key)
                                for w in workers.values():
                                    w.cipher = cipher
                                await ws.send(json.dumps({'type': 'rekeying_response', 'device_pub': new_device_pub_b64, 'device_name': rekey_device_name}))
                                new_vwords = pair_verify_words(new_aes_key)
                                log(cfg['relay_ws'], f"[{ts()}] [infero] Re-keying complete for {iid} | key:re-keyed | verify: {new_vwords}")
                                print(f"[infero] re-keyed | verify: {new_vwords}", flush=True)
                            except Exception as e:
                                log(cfg['relay_ws'], f"[{ts()}] [infero] Re-keying error: {e}")
                    elif mtype == 'stream_token':
                        # Another node is streaming — decrypt and print to terminal
                        try:
                            data = decrypt(cipher, msg['payload'])
                            text = data.get('text', '')
                            sys.stdout.write(f"\r[stream] {len(text)} chars...")
                            sys.stdout.flush()
                            if data.get('done'):
                                print(f"\n[{ts()}] [infero] Stream done from remote")
                        except Exception:
                            pass
        except websockets.exceptions.ConnectionClosedError as e:
            if e.code == 4002:
                log(cfg['relay_ws'], f"[{ts()}] [infero] Removed from {iid}. Stopping connection.")
                instances = [i for i in load_instances() if i['instance_id'] != cfg['instance_id']]
                save_instances(instances)
                return
            log(cfg['relay_ws'], f"[{ts()}] [infero] Disconnected from {iid} ({e.code}). Retry in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            log(cfg['relay_ws'], f"[{ts()}] [infero] Error ({iid}): {e}. Retry in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

async def main():
    instances = load_instances()
    if not instances:
        print(f"[{ts()}] [infero] No instances. Run: infero pair <CODE>")
        return
    print(f"[{ts()}] [infero] Starting agent — {len(instances)} instance(s), device: {DEVICE_NAME}")
    await asyncio.gather(*[connect_instance(c) for c in instances])

asyncio.run(main())
