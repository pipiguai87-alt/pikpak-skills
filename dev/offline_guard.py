"""Deny-default offline guard for trusted repository tests (not an OS sandbox).

Every real child is an exact approved Python entry, re-entered via -I -B -S and an
explicit bootstrap before test code runs. Shell/env wrappers and arbitrary
interpreters are not approved. This guards accidental effects in trusted tests,
not hostile code: native/ctypes calls, pre-existing descriptors, local relays and
arbitrary filesystem access are outside its containment guarantees.
"""
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SCRATCH = Path(tempfile.gettempdir()).resolve()
BOOTSTRAP = ROOT / "dev/offline_bootstrap.py"
PYTHON = sys.executable
_STATES = []
_INSTALLED = False
_REAL_POPEN = subprocess.Popen
_PERMIT = threading.local()
DENY_BODY = "import sys\nprint('offline test: real side effects denied', file=sys.stderr)\nraise SystemExit(97)\n"
UNAUTH_BODY = "import sys\nprint('UNAUTHENTICATED', file=sys.stderr)\nraise SystemExit(1)\n"
PIPELINE_BODY = '''import sys,json,os
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['NEUTRAL_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')
if args[:2]==['auth','status']: print(json.dumps({'logged_in':True,'profile':{'id':'fixture-account-id'}}))
elif args[:2]==['task','add']: print(json.dumps({'task_id':'T1'}))
elif args[:3]==['task','get','T1']: print(json.dumps({'task_id':'T1','status':'complete','file_id':'F1'}))
elif args[:2]==['download','F1'] and len(args)==4 and args[2]=='-o': Path(args[3]).write_bytes(b'neutral')
else: sys.exit(77)
'''


def loopback(host):
    # Numeric literals only; even localhost must not trigger system DNS here.
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def _deny(message="unapproved entry"):
    raise PermissionError("offline tests forbid " + message)


def _audit(event, args):
    if not _STATES:
        return
    if event in ("socket.getaddrinfo", "socket.gethostbyname"):
        if not args or not loopback(args[0]):
            _deny("non-loopback DNS")
    elif event in ("socket.getnameinfo", "socket.gethostbyaddr"):
        _deny("reverse DNS")
    elif event in ("socket.connect", "socket.bind"):
        if len(args) < 2 or not isinstance(args[1], tuple) or not loopback(args[1][0]):
            _deny("non-loopback socket")
    elif event in ("socket.sendto", "socket.sendmsg"):
        _deny("datagram send")
    elif event == "socket.__new__":
        if len(args) >= 3 and (args[1] not in (2, 10, 30) or args[2] != 1):
            _deny("non-TCP socket")
    elif event in ("os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.forkpty", "pty.spawn"):
        _deny("shell/exec/spawn")
    elif event == "subprocess.Popen":
        permit = getattr(_PERMIT, "value", None)
        if permit is None or len(args) != 4:
            _deny("raw subprocess; use approved guarded entries")
        exe, argv, cwd, env = args
        if (exe, list(argv), cwd, env) != permit:
            _deny("subprocess argv/environment mismatch")


def _private_path(value, private, *, exists=False):
    path = Path(value)
    resolved = path.resolve()
    if not path.is_absolute() or not resolved.is_relative_to(private) or path.is_symlink():
        _deny("path outside private test root")
    if exists and not resolved.is_file():
        _deny("missing private input")
    return resolved


def _url_file(value, private):
    path = _private_path(value, private, exists=True)
    text = path.read_text(encoding="utf-8")
    if len(text) > 16384:
        _deny("oversize URL fixture")
    parsed = urllib.parse.urlsplit(text.strip())
    if parsed.scheme != "http" or not loopback(parsed.hostname) or parsed.username or parsed.password:
        _deny("non-loopback URL fixture")
    return str(path)


def _pairs(args, allowed, private):
    """Validate option/value pairs and private file operands, never a shell."""
    seen = set()
    index = 0
    while index < len(args):
        flag = args[index]
        if flag not in allowed or flag in seen and flag != "--approve-id":
            _deny("unapproved script option")
        seen.add(flag)
        kind = allowed[flag]
        if kind is None:
            index += 1
            continue
        if index + 1 == len(args):
            _deny("missing option operand")
        value = args[index + 1]
        if kind == "path":
            _private_path(value, private)
        elif kind == "url":
            _url_file(value, private)
        elif not re.fullmatch(kind, value):
            _deny("unapproved option value")
        index += 2
    return seen


def _script_args(script, args, private):
    relative = script.relative_to(ROOT).as_posix() if script.is_relative_to(ROOT) else ""
    if relative == "dev/offline_child_probe.py":
        if args:
            _deny()
    elif relative == "dev/smoke_e2e.py":
        if args != ["--json"]:
            _deny()
    elif relative == "dev/mock_pikpak/mock_pikpak.py":
        if not args or args[0] != "ls" or any(not re.fullmatch(r"[-A-Za-z0-9_.]*", arg) for arg in args[1:]):
            _deny("mock argv")
    elif relative == "skills/pikpak-speed-guide/scripts/speed_bench.py":
        if args != ["--worker"]:
            seen = _pairs(args, {"--baseline-url-file": "url", "--candidate-url-file": "url",
                "--bytes": r"[1-9][0-9]{0,7}", "--repeat": r"[1-5]", "--timeout": r"[0-9.]{1,8}", "--json": None}, private)
            if "--baseline-url-file" not in seen:
                _deny("missing loopback fixture")
    elif relative in ("skills/pikpak-ad-cleaner/scripts/scan_ads.py", "skills/pikpak-dedup/scripts/analyze_dupes.py"):
        _pairs(args, {"--folder": r"", "--json": "path", "--from-report": "path",
            "--report-sha256": r"[0-9a-f]{64}", "--approve-id": r"f_(?:url|s1|s3)",
            "--emit-trash-cmd": None if "dedup" in relative else r"high"}, private)
    elif relative == "skills/pikpak-onboarding/scripts/registration_plan.py":
        if args not in ([], ["--choice", "decline"]):
            _deny()
    elif relative == "skills/pikpak-save/scripts/inspect_task.py":
        if args != ["--task-id", "T1"]:
            _deny()
    elif relative == "skills/pikpak-magnet-pipeline/scripts/magnet_pipeline.py":
        # Only the exact neutral subprocess integration scenario, never a live CLI.
        if len(args) < 4 or args[-2] != "--state":
            _deny()
        _private_path(args[-1], private)
        body = args[:-2]
        if body not in (["step1-add", "https://example.invalid/demo"], ["step2-status", "T1"],
                        ["step4-cleanup", "F1", "--verified-local"]):
            if len(body) != 4 or body[:3] != ["step3-download", "F1", "--dest"]:
                _deny()
            _private_path(body[3], private)
    else:
        _deny("script not on exact allowlist")


def _fake_script(script, args, state):
    """Translate only pre-reviewed private fixtures; never execute a shell shim."""
    private = state["private"]
    _private_path(str(script), private, exists=True)
    text = script.read_text(encoding="utf-8")
    probe = ROOT / "dev/offline_child_probe.py"
    if not args and text == "#!" + PYTHON + "\n" + probe.read_text(encoding="utf-8"):
        return probe, []
    mock_shim = "#!/bin/sh\nexec " + shlex.quote(PYTHON) + " " + shlex.quote(str(ROOT / "dev/mock_pikpak/mock_pikpak.py")) + ' "$@"\n'
    if text == mock_shim:
        target = ROOT / "dev/mock_pikpak/mock_pikpak.py"
        _script_args(target, args, private)
        return target, args
    if text in ("#!" + PYTHON + "\n" + DENY_BODY,
                "#!/bin/sh\nprintf '%s\\n' 'UNAUTHENTICATED' >&2\nexit 1\n"):
        target = private / ("deny.py" if "UNAUTHENTICATED" not in text else "unauth.py")
        target.write_text(DENY_BODY if "UNAUTHENTICATED" not in text else UNAUTH_BODY)
        return target, []
    if text == "#!" + PYTHON + "\n" + PIPELINE_BODY:
        if args not in (["auth", "status", "-F", "json"], ["task", "add", "https://example.invalid/demo", "-F", "json"],
                        ["task", "get", "T1", "-F", "json"]):
            if len(args) != 4 or args[:3] != ["download", "F1", "-o"]:
                _deny("fake CLI argv")
            _private_path(args[3], private)
        return script, args
    _deny("unreviewed private executable content")


def _prepare(argv, supplied_env, state):
    if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(a, str) for a in argv):
        _deny("non-list argv")
    argv = list(argv)
    env = dict(os.environ) if supplied_env is None else dict(supplied_env)
    base = state["env"]
    # Only explicitly recognised neutral integration schemas may be re-homed.
    if env != base:
        if argv[:2] == [PYTHON, str(ROOT / "dev/smoke_e2e.py")] and argv[2:] == ["--json"]:
            changed = {key for key in set(env) | set(base) if env.get(key) != base.get(key)}
            if changed != {"PIKPAK_BIN"}:
                _deny("smoke env override")
            _private_path(env["PIKPAK_BIN"], state["private"], exists=True)
            env = dict(base)  # smoke itself replaces this; the supplied trap is NEVER run.
        elif env == {"PATH": os.defpath, "HOME": "/nonexistent-neutral-home"} and "registration_plan.py" in argv[1:3][-1]:
            env = dict(base)
        elif set(env) == {"PATH", "HOME", "TMPDIR", "PIKPAK_BIN", "NEUTRAL_LOG"} and env["PATH"] == os.defpath:
            fake = Path(env["PIKPAK_BIN"])
            # This one legacy test allocates its neutral directory in the caller TMPDIR.
            root = fake.parent.resolve()
            if (root.parent != state["scratch"] or env["HOME"] != str(root) or env["TMPDIR"] != str(root)
                    or env["NEUTRAL_LOG"] != str(root / "calls.jsonl") or fake.name != "neutral-cli"
                    or fake.is_symlink() or fake.read_text() != "#!" + PYTHON + "\n" + PIPELINE_BODY):
                _deny("unapproved fake CLI environment")
            state = dict(state, private=root)
            env = dict(base, HOME=str(root), TMPDIR=str(root), PIKPAK_BIN=str(fake), NEUTRAL_LOG=str(root / "calls.jsonl"))
        else:
            _deny("child environment override")
    if argv[0] == PYTHON:
        rest = argv[1:]
        if rest[:1] == ["-B"]:
            rest = rest[1:]
        if not rest or not Path(rest[0]).is_absolute() or Path(rest[0]).is_symlink():
            _deny("interpreter flags or script path")
        script, args = Path(rest[0]).resolve(), rest[1:]
        _script_args(script, args, state["private"])
    elif argv[0] == env["PIKPAK_BIN"]:
        script, args = _fake_script(Path(argv[0]), argv[1:], state)
    else:
        _deny("interpreter/executable not approved")
    return script, args, env, state


def _popen(argv, *positional, **kwargs):
    if not _STATES:
        return _REAL_POPEN(argv, *positional, **kwargs)
    allowed = {"stdin", "stdout", "stderr", "bufsize", "universal_newlines", "encoding", "errors", "text", "close_fds", "env", "cwd"}
    if positional or set(kwargs) - allowed:
        _deny("Popen controls/shell/executable override")
    state = _STATES[-1]
    script, args, env, launch_state = _prepare(argv, kwargs.get("env"), state)
    cwd = kwargs.get("cwd")
    if cwd is not None and Path(cwd).resolve() not in (ROOT, launch_state["private"]):
        _deny("working directory override")
    launch = {"private": str(launch_state["private"]), "scratch": str(state["scratch"]),
              "script": str(script), "args": args, "env": env,
              "sha256": hashlib.sha256(script.read_bytes()).hexdigest()}
    with tempfile.NamedTemporaryFile(mode="w", prefix="launch-", suffix=".json", dir=state["private"], delete=False) as stream:
        json.dump(launch, stream)
        manifest = stream.name
    wrapped = [PYTHON, "-I", "-B", "-S", str(BOOTSTRAP), manifest]
    kwargs.update(env=env, cwd=str(ROOT))
    _PERMIT.value = (PYTHON, wrapped, str(ROOT), env)
    try:
        return _REAL_POPEN(wrapped, **kwargs)
    finally:
        _PERMIT.value = None


@contextmanager
def active_guard(private, scratch, env):
    global _INSTALLED
    if not _INSTALLED:
        sys.addaudithook(_audit)
        _INSTALLED = True
    state = {"private": Path(private).resolve(), "scratch": Path(scratch).resolve(), "env": dict(env)}
    outer = not _STATES
    if outer:
        subprocess.Popen = _popen
    _STATES.append(state)
    try:
        yield
    finally:
        _STATES.pop()
        if outer:
            subprocess.Popen = _REAL_POPEN


@contextmanager
def offline_environment():
    """Private state + audited approved Python children; not hostile-code isolation."""
    previous_env = dict(os.environ)
    previous_tempdir = tempfile.tempdir
    temp_parent = Path(tempfile.gettempdir()).resolve()
    scratch = _STATES[0]["scratch"] if _STATES else temp_parent
    with tempfile.TemporaryDirectory(prefix="pikpak-offline-", dir=temp_parent) as folder:
        private = Path(folder).resolve()
        bins, home, temp = (private / name for name in ("bin", "home", "tmp"))
        for path in (bins, home, temp, home / ".config", home / ".cache", home / ".hermes/cache/scratch"):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        deny = bins / "pikpak"
        deny.write_text("#!" + PYTHON + "\n" + DENY_BODY)
        deny.chmod(0o700)
        env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
               "TMPDIR": str(temp), "PIKPAK_BIN": str(deny), "PATH": str(bins),
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "LANG": "en_US.UTF-8"}
        if sys.platform == "darwin":
            # CoreFoundation inserts this deterministic non-secret locale key at startup.
            env["__CF_USER_TEXT_ENCODING"] = "0x{:X}:0x0:0x0".format(os.getuid())
        os.environ.clear()
        os.environ.update(env)
        tempfile.tempdir = str(temp)
        try:
            with active_guard(private, scratch, env):
                yield private
        finally:
            tempfile.tempdir = previous_tempdir
            os.environ.clear()
            os.environ.update(previous_env)
