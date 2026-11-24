"""Bounded-output child execution for development initialization only."""
from collections import Counter
from contextlib import contextmanager
import os
import re
import selectors
import signal
import subprocess
import time


class InitializationCancelled(Exception):
    def __init__(self, signum):
        self.signum = signum
        super().__init__('Initialization interrupted; inspect existing state')


class UnexpectedApproval(Exception):
    """An approval prompt in this path is a regression, not a failed probe."""


class Cancellation:
    def __init__(self):
        self.signum = None

    @contextmanager
    def handling(self):
        def remember(signum, _frame):
            if self.signum is None:
                self.signum = signum
        previous = {sig: signal.signal(sig, remember)
                    for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            yield self
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def check(self):
        if self.signum is not None:
            raise InitializationCancelled(self.signum)


# Only static, reviewed messages leave the child-output boundary. Arbitrary
# strings, command lines, paths and credentials never reach the terminal.
NOTICES = {
    b'SECURITY: Membership in the docker group effectively grants root privileges.':
        'SECURITY: Docker group membership grants root-equivalent privileges.',
    b'ACTION REQUIRED: Docker group membership exists but is not active in this session.':
        'ACTION REQUIRED: Sign out and back in, then rerun make initialize.',
    b'ACTION REQUIRED: Sign out and back in (or reboot the VM), then rerun:':
        'ACTION REQUIRED: Sign out and back in, then rerun make initialize.',
    b'NEVER paste or commit this file. It is NOT safe for a shared/production Vault.':
        'SECURITY: Protect the private Vault recovery file outside the VM and Git.',
    b'ERROR: Unknown untrusted local CA; use explicit host preparation after review':
        'ERROR: Unknown untrusted local CA; review using --prepare-host.',
    b'ERROR: Local CA differs from its initialization record':
        'ERROR: Local CA identity differs from the recorded fingerprint.',
    b'ERROR: Incomplete local CA; refusing replacement':
        'ERROR: Incomplete local CA; refusing replacement.',
    b'ERROR: Incomplete browser TLS state; refusing replacement':
        'ERROR: Incomplete browser TLS state; refusing replacement.',
    b'ERROR: Unexpected Docker context; manual Docker access review required':
        'ERROR: Unexpected Docker context; manual Docker access review required.',
    b'ERROR: Unexpected DOCKER_HOST; manual Docker access review required':
        'ERROR: Remote or unexpected Docker endpoint; manual review required.',
    b'ERROR: Unexpected Docker socket ownership':
        'ERROR: Unexpected Docker socket ownership.',
    b'ERROR: Vault is sealed: use ./scripts/dev-start.sh for manual unseal':
        'ERROR: Vault is sealed; use make recover with existing shares.',
    b'CANCELLED: Vault recovery material persisted privately; review Vault seal state before continuing':
        'CANCELLED: Vault recovery material persisted privately; review seal state.',
}


class Diagnostics:
    def __init__(self):
        self.counts = Counter()
        self.notices = []
        self.buffers = {0: b'', 1: b''}
        self.truncated = {0: False, 1: False}
        self.prompt_seen = False

    def line(self, stream, value):
        if not value:
            return
        self.counts['stdout' if stream == 0 else 'stderr'] += 1
        if re.search(rb'\[[yYnN]/[yYnN]\]', value):
            self.prompt_seen = True
        if value in NOTICES:
            notice = NOTICES[value]
            if notice not in self.notices:
                self.notices.append(notice)
        elif re.match(rb'^(WARN(?:ING)?[: ]|\[WARN(?:ING)?\]|SECURITY:|IMPORTANT:|ACTION REQUIRED:)', value):
            self.counts['warning'] += 1
        elif re.match(rb'^(ERROR:|\[ERROR\]|FAIL:|RESULT: .*STOPPED)', value):
            self.counts['error'] += 1

    def feed(self, stream, data):
        for i, fragment in enumerate(data.split(b'\n')):
            if i:
                if not self.truncated[stream]:
                    self.line(stream, self.buffers[stream].rstrip(b'\r'))
                else:
                    self.counts['oversized'] += 1
                self.buffers[stream] = b''
                self.truncated[stream] = False
            if not self.truncated[stream]:
                combined = self.buffers[stream] + fragment
                if len(combined) > 4096:
                    self.truncated[stream] = True
                    self.buffers[stream] = b''
                else:
                    self.buffers[stream] = combined
                    if re.search(rb'\[[yYnN]/[yYnN]\]', combined):
                        self.prompt_seen = True

    def finish(self, stream):
        if self.truncated[stream]:
            self.counts['oversized'] += 1
        else:
            self.line(stream, self.buffers[stream].rstrip(b'\r'))
        self.buffers[stream] = b''


class ChildRunner:
    def __init__(self, progress, cancellation):
        self.progress = progress
        self.cancellation = cancellation
        self.verbose = os.environ.get('FT_DEV_BOOTSTRAP_VERBOSE') == '1'
        self.last_returncode = None
        self.last_diagnostics = None

    def run(self, argv, *, cwd, env=None, pass_fds=(), foreground=False,
            critical=False, probe=False):
        self.cancellation.check()
        diagnostics = Diagnostics()
        self.last_diagnostics = diagnostics
        if foreground:
            self.progress.pause()
        try:
            with subprocess.Popen(argv, cwd=cwd, env=env, pass_fds=pass_fds,
                                  stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  start_new_session=not foreground) as child:
                interrupted = None
                escalated = False
                with selectors.DefaultSelector() as sel:
                    for index, pipe in enumerate((child.stdout, child.stderr)):
                        sel.register(pipe, selectors.EVENT_READ, index)
                    while sel.get_map() or child.poll() is None:
                        if self.cancellation.signum is not None or diagnostics.prompt_seen:
                            now = time.monotonic()
                            if interrupted is None:
                                interrupted = now
                                signum = self.cancellation.signum or signal.SIGINT
                                if self.cancellation.signum is not None:
                                    self.progress.notice('Cancellation requested; waiting for active operation.')
                                try:
                                    if foreground:
                                        child.send_signal(signum)
                                    else:
                                        os.killpg(child.pid, signum)
                                except ProcessLookupError:
                                    pass
                            elif not critical and not foreground and now - interrupted > 10 and not escalated:
                                escalated = True
                                try:
                                    os.killpg(child.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                        for key, _ in sel.select(0.15):
                            data = os.read(key.fd, 8192)
                            if data:
                                diagnostics.feed(key.data, data)
                            else:
                                diagnostics.finish(key.data)
                                sel.unregister(key.fileobj)
                        self.progress.refresh()
                    self.last_returncode = child.wait()
        finally:
            if foreground:
                self.progress.resume()
        for notice in diagnostics.notices:
            if not probe or not notice.startswith('ERROR:'):
                self.progress.notice(notice)
        counts = diagnostics.counts
        if diagnostics.prompt_seen:
            self.progress.notice('ERROR: Unexpected initialization approval prompt; stopped safely.')
            raise UnexpectedApproval()
        if self.verbose or (not probe and (self.last_returncode != 0 or counts['stderr'])) or counts['warning']:
            self.progress.notice(f"Child exit {self.last_returncode}; stdout {counts['stdout']} lines, "
                                 f"stderr {counts['stderr']} lines, warnings {counts['warning']}, "
                                 f"errors {counts['error']}, oversized {counts['oversized']} (raw output withheld)")
        self.cancellation.check()
        if self.last_returncode in (-signal.SIGINT, -signal.SIGTERM, 130, 143):
            raise InitializationCancelled(signal.SIGINT if self.last_returncode in (-2, 130) else signal.SIGTERM)
        return self.last_returncode
