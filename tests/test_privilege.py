"""Tests for the privilege-escalation input layer.

These never actually run ``sudo``; they exercise the decision logic (when to
prompt, when to refuse, that the password is not retained past an operation)
with the ``subprocess`` boundary mocked.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from archon.security import privilege
from archon.security.privilege import PrivilegeError, PrivilegeEscalator


def _completed(returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["sudo"], returncode=returncode, stdout="", stderr="")


def test_no_provider_and_not_root_refuses():
    esc = PrivilegeEscalator()
    with patch.object(PrivilegeEscalator, "is_root", staticmethod(lambda: False)):
        with pytest.raises(PrivilegeError):
            with esc.session("op"):
                pass


def test_cancelled_prompt_refuses():
    esc = PrivilegeEscalator(provider=lambda reason: None)
    with patch.object(PrivilegeEscalator, "is_root", staticmethod(lambda: False)), patch.object(
        PrivilegeEscalator, "sudo_available", staticmethod(lambda: True)
    ):
        with pytest.raises(PrivilegeError):
            with esc.session("op"):
                pass


def test_wrong_password_refuses():
    esc = PrivilegeEscalator(provider=lambda reason: "bad")
    with patch.object(PrivilegeEscalator, "is_root", staticmethod(lambda: False)), patch.object(
        PrivilegeEscalator, "sudo_available", staticmethod(lambda: True)
    ), patch("subprocess.run", return_value=_completed(returncode=1)):
        with pytest.raises(PrivilegeError):
            with esc.session("op"):
                pass


def test_session_prompts_once_and_password_not_retained():
    calls: list[str] = []
    esc = PrivilegeEscalator(provider=lambda reason: calls.append(reason) or "secret")
    with patch.object(PrivilegeEscalator, "is_root", staticmethod(lambda: False)), patch.object(
        PrivilegeEscalator, "sudo_available", staticmethod(lambda: True)
    ), patch("archon.security.privilege.run_with_timeout", return_value=_completed(returncode=0)) as run:
        assert esc.active_session is None
        with esc.session("build") as sess:
            assert sess is not None
            assert esc.active_session is sess
            # Prompted exactly once up front for the whole operation.
            assert calls == ["build"]
            # Sub-commands reuse the held password — no further prompts.
            sess.run(["a"])
            sess.run(["b"])
            assert calls == ["build"]
        # After the operation the session (and its password) is dropped.
        assert esc.active_session is None
        # The validation call fed the password on stdin to `sudo -S`.
        _, kwargs = run.call_args
        assert kwargs.get("input_text", "").startswith("secret")


def test_root_yields_no_session_and_runs_directly():
    esc = PrivilegeEscalator()
    with patch.object(PrivilegeEscalator, "is_root", staticmethod(lambda: True)), patch(
        "subprocess.run", return_value=_completed(returncode=0)
    ) as run:
        with esc.session("op") as sess:
            assert sess is None  # root => no sudo wrapper needed
        esc.run(["ls"])
        argv = run.call_args[0][0]
        assert argv == ["ls"]  # ran directly, no sudo prefix


def test_session_run_prefixes_sudo():
    esc = PrivilegeEscalator(provider=lambda reason: "pw")
    with patch.object(PrivilegeEscalator, "is_root", staticmethod(lambda: False)), patch.object(
        PrivilegeEscalator, "sudo_available", staticmethod(lambda: True)
    ), patch("archon.security.privilege.run_with_timeout", return_value=_completed(returncode=0)) as run:
        with esc.session("op") as sess:
            sess.run(["pacstrap", "/mnt"])
        # Last call is the real command, prefixed with sudo -S.
        argv = run.call_args[0][0]
        assert argv[:2] == ["sudo", "-S"]
        assert "pacstrap" in argv


def test_run_with_timeout_kills_the_process_group():
    """A command blocked on a prompt must be killed, not waited on forever.

    This is the defence-in-depth behind the wireshark hang: with no TTY, any
    interactive prompt (debconf/whiptail, su, gpg) blocks indefinitely and the
    build stalls with no output. The child is started in its own session so the
    timeout kills the whole tree (sudo → chroot → apt-get → whiptail) rather than
    orphaning the grandchild still holding the pipes.
    """
    import sys

    monkey = pytest.MonkeyPatch()
    monkey.setattr(privilege, "CMD_TIMEOUT", 1)
    try:
        result = privilege.run_with_timeout(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
    finally:
        monkey.undo()

    # 124 is the conventional timeout exit code.
    assert result.returncode == 124


def test_run_with_timeout_passes_through_success():
    import sys

    result = privilege.run_with_timeout(
        [sys.executable, "-c", "print('hello')"]
    )
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_session_run_does_not_rely_on_env_for_target_variables():
    """sudo resets the environment — `env=` is NOT how a target gets a var.

    Documents the trap behind the 68-minute build hang: DEBIAN_FRONTEND passed
    via `env=` is silently stripped at the sudo boundary, so debconf falls back
    to whiptail. The variable must be baked into the argv instead — see
    ``rootfs_builder._chroot_cmd``.
    """
    esc = PrivilegeEscalator(provider=lambda reason: "pw")
    with patch.object(PrivilegeEscalator, "is_root", staticmethod(lambda: False)), patch.object(
        PrivilegeEscalator, "sudo_available", staticmethod(lambda: True)
    ), patch("archon.security.privilege.run_with_timeout", return_value=_completed(returncode=0)) as run:
        with esc.session("op") as sess:
            sess.run(["chroot", "/rootfs", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "wireshark"])
        argv = run.call_args[0][0]
        # The value rides in argv (after sudo), so env_reset cannot strip it.
        assert argv[:2] == ["sudo", "-S"]
        assert "DEBIAN_FRONTEND=noninteractive" in argv
