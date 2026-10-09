"""Privilege escalation input layer.

Archon normally runs unprivileged (uid != 0). When an operation genuinely needs
root — a distro build's ``pacstrap``/``debootstrap``/``chroot`` steps, for
example — this layer asks the UI for the sudo password through an injected
*provider*, runs the privileged command via ``sudo -S`` (password on stdin), and
never persists the password itself.

Design
------
* The password *provider* is set by the presentation layer (a masked modal in
  the TUI, ``getpass`` in the REPL/CLI). The core never imports a UI.
* A single privileged *operation* (which may fan out into many ``sudo``
  sub-commands, like a build) opens a :meth:`PrivilegeEscalator.session`. The
  session prompts **once**, holds the password only for the lifetime of the
  ``with`` block, feeds it to each ``sudo -S`` call, and discards it on exit.
  Archon therefore never caches the password across operations — honoring the
  "prompt every time, never cache" contract while keeping a long build usable.
* A process-wide singleton (:func:`get_escalator`) lets deep code (the distro
  builder's subprocess helpers) reach the active session without threading a
  runner through every call site.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator
from typing import Any

from ..utils.logger import get_logger

logger = get_logger("Privilege")

# Hard cap on any single subprocess. A command with no TTY that hits an
# interactive prompt (debconf/whiptail inside a chroot, a su/gpg prompt) blocks
# forever and silently — a 68-minute stall with no output before. Capping every
# command turns that hang into a loud failure the caller can retry or skip.
# ponytail: a flat per-command cap; the longest real operation is an apt install
# of the live-boot stack, which must stay under it or be given its own budget.
CMD_TIMEOUT = int(os.environ.get("ARCHON_BUILD_CMD_TIMEOUT", "1800"))


def run_with_timeout(
    cmd: list[str], *, input_text: str | None = None, **kwargs: Any
) -> subprocess.CompletedProcess:
    """``subprocess.run`` that kills the whole process group on timeout.

    ``start_new_session=True`` detaches the child into its own process group so a
    timeout can kill the entire tree (sudo → chroot → apt-get → whiptail) rather
    than just the direct child, which would otherwise be orphaned still holding
    the pipes open. Returns exit code 124 (timeout's conventional code) on
    expiry so callers treat it as a normal non-zero failure.
    """
    # ``capture_output`` is a run()-only kwarg; Popen needs explicit pipes. We
    # always capture, since the caller returns combined output for the build log.
    kwargs.pop("capture_output", None)
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    kwargs.setdefault("text", True)
    proc = subprocess.Popen(cmd, start_new_session=True, **kwargs)
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=CMD_TIMEOUT)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        stdout, stderr = "", ""
        with contextlib.suppress(Exception):
            stdout, stderr = proc.communicate(timeout=30)
        logger.error(
            "Command timed out after {}s: {}", CMD_TIMEOUT, " ".join(cmd)[:200]
        )
        return subprocess.CompletedProcess(cmd, 124, stdout, stderr)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

# provider(reason) -> password, or None if the user cancels/declines.
PasswordProvider = Callable[[str], "str | None"]


class PrivilegeError(PermissionError):
    """Raised when a privileged operation cannot proceed (no sudo, cancelled)."""


class _Session:
    """An active privileged operation holding a password for its lifetime only."""

    def __init__(self, password: str) -> None:
        self._password = password

    def run(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        """Run ``argv`` as root via ``sudo -S``, feeding the held password.

        ``-k`` ignores any cached sudo timestamp so behavior is deterministic;
        ``-p ''`` suppresses sudo's own prompt (we supply the password on stdin).

        NOTE: sudo resets the environment (``env_reset`` in sudoers), so any
        ``env=`` the caller passes here is stripped before the target command
        sees it. Variables the *target* needs must be set in its argv (e.g. an
        ``env VAR=val`` prefix), not via this kwarg.
        """
        kwargs.setdefault("capture_output", True)
        kwargs.setdefault("text", True)
        return run_with_timeout(
            ["sudo", "-S", "-k", "-p", "", *argv],
            input_text=self._password + "\n",
            **kwargs,
        )


class PrivilegeEscalator:
    """Obtains root for privileged operations without storing the password."""

    def __init__(self, provider: PasswordProvider | None = None) -> None:
        self._provider = provider
        # The session bound to the *current privileged operation*. Kept as a
        # plain attribute (not thread-local) on purpose: a distro build fans its
        # sudo sub-commands out across worker threads via ``asyncio.to_thread``,
        # so the runner must be reachable from any thread for the operation's
        # lifetime. ponytail: assumes one privileged operation at a time (no
        # concurrent builds); a concurrent design would need per-operation keys.
        self._session: _Session | None = None

    def set_provider(self, provider: PasswordProvider | None) -> None:
        """Register (or clear) the UI callback that supplies the sudo password."""
        self._provider = provider

    @staticmethod
    def is_root() -> bool:
        if sys.platform == "win32":
            try:
                import ctypes

                return bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception:
                return False
        return os.geteuid() == 0

    @staticmethod
    def sudo_available() -> bool:
        return shutil.which("sudo") is not None

    def can_escalate(self) -> bool:
        """True if this process is root, or can prompt+sudo to become root."""
        return self.is_root() or (self._provider is not None and self.sudo_available())

    @property
    def active_session(self) -> _Session | None:
        """The session for the current privileged operation, if one is open."""
        return self._session

    def _prompt(self, reason: str) -> str | None:
        if self._provider is None:
            return None
        try:
            pw = self._provider(reason)
        except Exception as exc:  # noqa: BLE001 - a broken UI must not crash a run
            logger.debug("sudo password provider raised: {}", exc)
            return None
        return pw or None

    @contextlib.contextmanager
    def session(self, reason: str = "a privileged operation") -> Iterator[_Session | None]:
        """Open a privileged operation.

        Yields a :class:`_Session` runner when escalation succeeds, or ``None``
        when the process is already root (callers just run commands directly).

        Prompts **once** for the whole operation, validates the password up front
        so a long build fails fast on a bad password, and holds it only for the
        lifetime of the ``with`` block.

        Raises:
            PrivilegeError: if not root and escalation is impossible or declined.
        """
        if self.is_root():
            yield None
            return
        if not self.sudo_available():
            raise PrivilegeError(
                f"{reason} requires root, but 'sudo' is not available on this system."
            )
        password = self._prompt(reason)
        if password is None:
            raise PrivilegeError(f"{reason} requires root; sudo password was not provided.")
        # Validate the password once up front so a long operation fails fast on a
        # bad password rather than mid-build.
        try:
            check = run_with_timeout(
                ["sudo", "-S", "-k", "-p", "", "true"],
                input_text=password + "\n",
            )
        except Exception as exc:  # noqa: BLE001
            raise PrivilegeError(f"Could not invoke sudo for {reason}: {exc}") from exc
        if check.returncode != 0:
            raise PrivilegeError(f"Incorrect sudo password; {reason} cancelled.")

        session = _Session(password)
        previous = self._session
        self._session = session
        try:
            yield session
        finally:
            # Restore the prior session (supports nesting) and drop our reference
            # so the password is not retained past the operation.
            self._session = previous
            del session

    def run(
        self, argv: list[str], reason: str = "a privileged operation", **kwargs: Any
    ) -> subprocess.CompletedProcess:
        """Run one privileged command.

        If a :meth:`session` is active, reuse it (no re-prompt). Otherwise open a
        one-shot session (prompt once), run, and discard.
        """
        if self.is_root():
            kwargs.setdefault("capture_output", True)
            kwargs.setdefault("text", True)
            return subprocess.run(argv, **kwargs)
        active = self.active_session
        if active is not None:
            return active.run(argv, **kwargs)
        with self.session(reason) as sess:
            assert sess is not None  # not root, so session yields a runner
            return sess.run(argv, **kwargs)


_escalator: PrivilegeEscalator | None = None


def get_escalator() -> PrivilegeEscalator:
    """Return the process-wide escalator singleton."""
    global _escalator
    if _escalator is None:
        _escalator = PrivilegeEscalator()
    return _escalator


def configure_escalator(provider: PasswordProvider | None) -> PrivilegeEscalator:
    """Register the UI's sudo-password provider on the singleton."""
    esc = get_escalator()
    esc.set_provider(provider)
    return esc


__all__ = [
    "PrivilegeError",
    "PrivilegeEscalator",
    "configure_escalator",
    "get_escalator",
]
