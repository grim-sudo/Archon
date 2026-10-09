"""Regression tests for privileged file writes into a root-owned rootfs.

debootstrap/pacstrap create the rootfs owned by root. Config files (hostname,
timezone, locale) were once written with a plain ``Path.write_text`` from the
unprivileged build process, which raised ``[Errno 13] Permission denied:
.../rootfs/etc/hostname`` mid-build. These lock in that every such write is
routed through the privileged runner instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from archon.distro_builder import rootfs_builder as rb
from archon.distro_builder.models import DistroProfile, KernelConfig


def test_install_file_stages_to_tmp_and_runs_install(monkeypatch):
    recorded: list[list[str]] = []
    seen_content: dict[str, str] = {}

    def fake_run_logged(cmd, cwd=None, env=None):
        recorded.append(cmd)
        # The staged temp file must hold the content at call time (before unlink).
        if cmd and cmd[0] == "install":
            seen_content["content"] = Path(cmd[-2]).read_text(encoding="utf-8")
        return True, ""

    monkeypatch.setattr(rb, "_run_logged", fake_run_logged)

    ok, _ = rb._install_file("myhost\n", Path("/tmp/does-not-matter/etc/hostname"))

    assert ok is True
    assert recorded and recorded[0][0] == "install"
    # -D creates parent dirs; -m sets the mode.
    assert recorded[0][1:4] == ["-D", "-m", "0644"]
    assert recorded[0][-1].endswith("etc/hostname")
    assert seen_content["content"] == "myhost\n"
    # Temp file is cleaned up regardless.
    assert not Path(recorded[0][-2]).exists()


def test_debian_rootfs_writes_config_through_privileged_runner(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    def fake_run_logged(cmd, cwd=None, env=None):
        commands.append(cmd)
        return True, ""

    # Not root, but pretend escalation is available so the builder proceeds.
    monkeypatch.setattr(rb, "_require_root", lambda *_a, **_k: None)
    monkeypatch.setattr(rb, "_run_logged", fake_run_logged)
    # Don't hit the network probing mirrors during the orchestration test.
    monkeypatch.setattr(
        rb, "_select_debian_mirror", lambda *_a, **_k: "http://deb.debian.org/debian"
    )

    profile = DistroProfile(
        name="secos",
        base_distro="debian",
        hostname="secos-host",
        packages=[],
        kernel=KernelConfig(source="prebuilt"),
    )
    rootfs = tmp_path / "rootfs"
    # Seed an initrd so the prebuilt-kernel bootability check passes; this test
    # is about config-write routing, not initramfs generation.
    (rootfs / "boot").mkdir(parents=True)
    (rootfs / "boot" / "initrd.img-6.1.0-test").write_text("x", encoding="utf-8")

    ok = asyncio.run(rb.build_debian_rootfs(profile, rootfs))
    assert ok is True

    installs = [c for c in commands if c and c[0] == "install"]
    dests = [c[-1] for c in installs]
    # hostname, timezone, and locale.gen all written via install(1), never a
    # direct Path.write_text into the root-owned tree.
    assert any(d.endswith("etc/hostname") for d in dests)
    assert any(d.endswith("etc/timezone") for d in dests)
    assert any(d.endswith("etc/locale.gen") for d in dests)

    # And the config files must NOT have been written directly to disk (which,
    # against a real root-owned rootfs, would be the permission-denied bug).
    assert not (rootfs / "etc" / "hostname").exists()


def test_select_mirror_returns_explicit_url_untouched():
    # An explicit URL must be honored verbatim — no probing, no override.
    url = "http://my.local.mirror/debian"
    assert rb._select_debian_mirror(url, "bookworm") == url


def test_select_mirror_auto_picks_fastest(monkeypatch):
    import time

    import httpx

    # Each candidate "responds" after a real (tiny) delay proportional to its
    # rank, so the measured monotonic timing is deterministic without patching
    # the clock across probe threads. Fastest wins.
    delays = {
        "http://deb.debian.org/debian": 0.06,
        "http://ftp.us.debian.org/debian": 0.01,  # fastest
        "http://ftp.uk.debian.org/debian": 0.12,
    }
    monkeypatch.setattr(rb, "_DEBIAN_MIRROR_CANDIDATES", tuple(delays), raising=True)

    def fake_get(self, url, **kwargs):
        base = url.split("/dists/")[0]
        time.sleep(delays[base])
        return httpx.Response(200)

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    chosen = rb._select_debian_mirror("auto", "bookworm")
    assert chosen == "http://ftp.us.debian.org/debian"


def test_select_mirror_falls_back_when_all_fail(monkeypatch):
    import httpx

    def always_fail(self, url, **kwargs):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(httpx.Client, "get", always_fail)
    assert rb._select_debian_mirror("auto", "bookworm") == rb._DEFAULT_DEBIAN_MIRROR


def test_debian_rootfs_fails_when_no_initrd_produced(tmp_path, monkeypatch):
    # The build must refuse (not silently succeed) when the prebuilt-kernel path
    # produces no initramfs — that was the "initrd=None, live ISO cannot boot"
    # failure surfacing 200s too late in the live layout step.
    monkeypatch.setattr(rb, "_require_root", lambda *_a, **_k: None)
    monkeypatch.setattr(rb, "_run_logged", lambda *_a, **_k: (True, ""))
    monkeypatch.setattr(
        rb, "_select_debian_mirror", lambda *_a, **_k: "http://deb.debian.org/debian"
    )

    profile = DistroProfile(
        name="secos",
        base_distro="debian",
        hostname="secos-host",
        packages=[],
        kernel=KernelConfig(source="prebuilt"),
    )
    # No /boot/initrd.img-* seeded → verification must fail the build.
    assert asyncio.run(rb.build_debian_rootfs(profile, tmp_path / "rootfs")) is False


def test_debian_rootfs_binds_and_regenerates_initramfs(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    def fake_run_logged(cmd, cwd=None, env=None):
        commands.append(cmd)
        return True, ""

    monkeypatch.setattr(rb, "_require_root", lambda *_a, **_k: None)
    monkeypatch.setattr(rb, "_run_logged", fake_run_logged)
    monkeypatch.setattr(
        rb, "_select_debian_mirror", lambda *_a, **_k: "http://deb.debian.org/debian"
    )

    profile = DistroProfile(
        name="secos",
        base_distro="debian",
        hostname="secos-host",
        packages=[],
        kernel=KernelConfig(source="prebuilt"),
    )
    rootfs = tmp_path / "rootfs"
    (rootfs / "boot").mkdir(parents=True)
    (rootfs / "boot" / "initrd.img-6.1.0-test").write_text("x", encoding="utf-8")

    assert asyncio.run(rb.build_debian_rootfs(profile, rootfs)) is True

    firsts = [c[0] for c in commands]
    # /proc, /sys, /dev bound before apt, and unmounted afterwards.
    assert firsts.count("mount") == len(rb._CHROOT_BINDS)
    assert firsts.count("umount") == len(rb._CHROOT_BINDS)
    # initramfs regenerated in-chroot after live-boot is installed.
    assert any(
        c[0] == "chroot" and "update-initramfs" in " ".join(c) for c in commands
    )
    # Mounts torn down before the (later) squashfs step — the last mount-family
    # command must be a umount, never a leftover mount.
    mount_family = [c[0] for c in commands if c[0] in ("mount", "umount")]
    assert mount_family[-1] == "umount"


def test_apt_install_retries_individually_and_reports_failures(monkeypatch):
    # The batch fails; individually all but "burpsuite" succeed. The good ones
    # must still install and only the truly-missing name is reported — one bad
    # package name must never leave the ISO empty.
    calls: list[list[str]] = []

    def fake_run_logged(cmd, cwd=None, env=None):
        calls.append(cmd)
        # The package list is whatever follows the last apt option flag.
        cut = cmd.index("Dpkg::Options::=--force-confold") + 1
        pkgs = cmd[cut:]
        if len(pkgs) > 1:
            return False, "batch abort"  # transaction aborts on the missing name
        return ("burpsuite" not in pkgs), ""

    monkeypatch.setattr(rb, "_run_logged", fake_run_logged)

    chroot = ["chroot", "/rootfs"]
    all_ok, failed = rb._apt_install(chroot, ["nmap", "burpsuite", "wireshark"], {})

    assert all_ok is False
    assert failed == ["burpsuite"]
    # Batch attempt + one per-package attempt each.
    assert len(calls) == 1 + 3


def test_chroot_cmd_sets_noninteractive_debconf_past_sudo(tmp_path):
    """Regression: debconf must never fall back to whiptail inside the chroot.

    The build runs privileged commands through ``sudo``, which resets the
    environment (``env_reset`` in sudoers). Setting DEBIAN_FRONTEND via the
    runner's ``env=`` therefore never reaches apt-get, and a package with a
    config template (wireshark-common) opens a whiptail dialog on a stdin that
    ``sudo -S`` already consumed — no TTY behind it, so the build hangs forever.
    The variable must therefore be set in the target command's own argv.
    """
    argv = rb._chroot_cmd(tmp_path)

    assert argv[:2] == ["chroot", str(tmp_path)]
    # Set as env-assignment arguments for the chrooted command, not via env=.
    assert "DEBIAN_FRONTEND=noninteractive" in argv
    assert "DEBCONF_NONINTERACTIVE_SEEN=true" in argv
    # Before whatever command follows, so it is inherited by apt-get.
    assert argv.index("DEBIAN_FRONTEND=noninteractive") < len(argv) - 1


def test_apt_install_passes_dpkg_noninteractive_options(monkeypatch):
    """dpkg must not stop for conffile input either — same no-TTY hang class."""
    seen: list[list[str]] = []
    monkeypatch.setattr(rb, "_run_logged", lambda cmd, cwd=None, env=None: (seen.append(cmd), (True, ""))[1])

    rb._apt_install(["chroot", "/r"], ["nmap"], {})

    assert "Dpkg::Options::=--force-confdef" in seen[0]
    assert "Dpkg::Options::=--force-confold" in seen[0]


def test_kali_repo_setup_writes_pinned_source_and_key(monkeypatch, tmp_path):
    cmds: list[list[str]] = []
    files: dict[str, str] = {}

    def fake_run_logged(cmd, cwd=None, env=None):
        cmds.append(cmd)
        return True, ""

    def fake_install_file(content, dest, mode="0644"):
        files[str(dest)] = content
        return True, ""

    monkeypatch.setattr(rb, "_run_logged", fake_run_logged)
    monkeypatch.setattr(rb, "_install_file", fake_install_file)

    assert rb._setup_kali_repo(tmp_path / "rootfs", {}) is True

    src = next(v for k, v in files.items() if k.endswith("kali.list"))
    pin = next(v for k, v in files.items() if k.endswith("kali.pref"))
    assert "kali-rolling" in src
    # Pinned low so Debian stays the default source.
    assert "Pin-Priority: 100" in pin
    # The archive key is imported and the index refreshed.
    assert any("archive-key.asc" in " ".join(c) for c in cmds)
    assert any(c[-1] == "update" for c in cmds)


def test_kali_repo_set_up_when_profile_requests_it(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    def fake_run_logged(cmd, cwd=None, env=None):
        commands.append(cmd)
        return True, ""

    monkeypatch.setattr(rb, "_require_root", lambda *_a, **_k: None)
    monkeypatch.setattr(rb, "_run_logged", fake_run_logged)
    monkeypatch.setattr(
        rb, "_select_debian_mirror", lambda *_a, **_k: "http://deb.debian.org/debian"
    )

    profile = DistroProfile(
        name="secos",
        hostname="secos-host",
        packages=["nmap", "metasploit-framework"],
        kernel=KernelConfig(source="prebuilt"),
        kali_repo=True,
    )
    rootfs = tmp_path / "rootfs"
    (rootfs / "boot").mkdir(parents=True)
    (rootfs / "boot" / "initrd.img-6.1.0-test").write_text("x", encoding="utf-8")

    assert asyncio.run(rb.build_debian_rootfs(profile, rootfs)) is True
    # The Kali signing key was imported → the repo was set up during the build.
    assert any("archive-key.asc" in " ".join(c) for c in commands)


def test_kernel_flavor_maps_to_packages():
    # Arch exposes real kernel variants; cachyos degrades to zen (repo bootstrap
    # would touch the host keyring) and kali has no Arch equivalent.
    assert rb._arch_kernel_package("standard") == "linux"
    assert rb._arch_kernel_package("hardened") == "linux-hardened"
    assert rb._arch_kernel_package("lts") == "linux-lts"
    assert rb._arch_kernel_package("zen") == "linux-zen"
    assert rb._arch_kernel_package("cachyos") == "linux-zen"
    assert rb._arch_kernel_package("kali") == "linux"
    # Debian ships one generic image line regardless of flavor.
    assert rb._debian_kernel_package("zen") == "linux-image-amd64"
    assert rb._debian_kernel_package("kali") == "linux-image-amd64"
