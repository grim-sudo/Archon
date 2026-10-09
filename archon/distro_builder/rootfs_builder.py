"""
Root filesystem builders for Debian, Arch, and Buildroot (unix minimal).

Each builder function requires root privileges (os.geteuid() == 0)
and logs all subprocess output via loguru.

All blocking subprocess calls run in asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import tempfile
from pathlib import Path

from loguru import logger
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .models import DistroProfile

__all__ = [
    "build_debian_rootfs",
    "build_arch_rootfs",
    "build_buildroot_rootfs",
]

_BUILDROOT_URL = "https://buildroot.org/downloads/buildroot-snapshot.tar.gz"

# Fallback used when mirror selection is disabled or every candidate fails. The
# deb.debian.org CDN is geo-routed, so it is a safe default even when slow.
_DEFAULT_DEBIAN_MIRROR = "http://deb.debian.org/debian"

# Curated set of well-maintained, full Debian mirrors probed when the configured
# mirror is "auto". Kept short on purpose — the point is to pick the fastest
# reachable one, not to mirror the whole official list.
_DEBIAN_MIRROR_CANDIDATES = (
    "http://deb.debian.org/debian",
    "http://ftp.us.debian.org/debian",
    "http://ftp.uk.debian.org/debian",
    "http://ftp.de.debian.org/debian",
    "http://mirror.leaseweb.com/debian",
    "http://mirrors.kernel.org/debian",
)


def _select_debian_mirror(configured: str, suite: str) -> str:
    """Resolve the Debian mirror to use, probing for the fastest when 'auto'.

    An explicit URL is returned untouched. When *configured* is ``"auto"`` (or
    blank), each candidate's ``dists/<suite>/Release`` is fetched concurrently and
    the fastest responder wins. If probing is impossible (no httpx) or every
    candidate fails, we fall back to the geo-routed CDN.
    """
    if configured and configured.strip().lower() != "auto":
        return configured

    try:
        import concurrent.futures
        import time

        import httpx
    except Exception:  # noqa: BLE001 - no probing available, use the safe default
        logger.debug("Mirror probing unavailable; using {}", _DEFAULT_DEBIAN_MIRROR)
        return _DEFAULT_DEBIAN_MIRROR

    def _probe(base: str) -> float | None:
        url = f"{base.rstrip('/')}/dists/{suite}/Release"
        try:
            start = time.monotonic()
            with httpx.Client(follow_redirects=True, timeout=5.0) as client:
                resp = client.get(url, headers={"Range": "bytes=0-0"})
            if resp.status_code >= 400:
                return None
            return time.monotonic() - start
        except httpx.HTTPError:
            return None

    timings: dict[str, float] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(_DEBIAN_MIRROR_CANDIDATES)
    ) as pool:
        for base, elapsed in zip(
            _DEBIAN_MIRROR_CANDIDATES,
            pool.map(_probe, _DEBIAN_MIRROR_CANDIDATES),
            strict=True,
        ):
            if elapsed is not None:
                timings[base] = elapsed

    if not timings:
        logger.warning("No Debian mirror responded; falling back to {}", _DEFAULT_DEBIAN_MIRROR)
        return _DEFAULT_DEBIAN_MIRROR

    fastest = min(timings, key=timings.__getitem__)
    logger.info("Fastest Debian mirror: {} ({:.0f} ms)", fastest, timings[fastest] * 1000)
    return fastest


# Kernel flavor → prebuilt package name, per base. Debian's archive only ships a
# single generic image line, so every flavor resolves to linux-image-amd64 there
# (a Kali security build still boots a stable Debian kernel — the toolset is what
# matters). Arch exposes real variants.
def _arch_kernel_package(flavor: str) -> str:
    """Map a kernel flavor to the Arch package pacstrap should install."""
    mapping = {
        "standard": "linux",
        "hardened": "linux-hardened",
        "lts": "linux-lts",
        "zen": "linux-zen",
        # ponytail: CachyOS ships its kernel from its own repo, whose keyring
        # bootstrap would mutate the host pacman keyring — off-limits here. zen is
        # the closest throughput-tuned kernel installable from official Arch repos.
        "cachyos": "linux-zen",
        "kali": "linux",  # Kali is Debian-based; no Arch equivalent.
    }
    return mapping.get(flavor, "linux")


def _debian_kernel_package(flavor: str) -> str:
    """Map a kernel flavor to the Debian image package apt should install.

    Debian's archive exposes one generic amd64 image line, so all flavors resolve
    to it. A ``kali`` flavor rides the Kali repo layered elsewhere; the pinned-low
    repo keeps Debian's stable kernel as the one that actually installs.
    """
    return "linux-image-amd64"


def _require_root(func_name: str) -> None:
    """Ensure privileged commands can run, or raise PermissionError.

    Root itself is fine. Otherwise an active :class:`PrivilegeEscalator` session
    (opened by the caller after prompting for the sudo password) lets the build
    proceed by running each command through ``sudo -S``. Only when neither is
    available do we refuse.

    Args:
        func_name: Name of the calling function for the error message.

    Raises:
        PermissionError: If not root and no sudo escalation session is active.
    """
    if os.geteuid() == 0:
        return
    try:
        from ..security.privilege import get_escalator

        if get_escalator().active_session is not None:
            return
    except Exception:  # noqa: BLE001 - fall through to the honest refusal
        pass
    raise PermissionError(
        f"{func_name}() requires root privileges. Re-run with sudo or as root."
    )


def _run_logged(
    cmd: list[str], cwd: str | None = None, env: dict | None = None
) -> tuple[bool, str]:
    """Run a command, log stdout/stderr, return (success, combined_log).

    When not root but a sudo-escalation session is active, the command runs
    through ``sudo -S`` transparently so the whole build can complete.

    Both branches go through :func:`run_with_timeout`, so a command that blocks
    on an interactive prompt with no TTY is killed (process group and all) and
    reported as a failure instead of stalling the build indefinitely.
    """
    logger.debug("Running: {}", " ".join(cmd))
    session = None
    if os.geteuid() != 0:
        try:
            from ..security.privilege import get_escalator

            session = get_escalator().active_session
        except Exception:  # noqa: BLE001
            session = None
    run_env = env or dict(os.environ)
    if session is not None:
        result = session.run(cmd, cwd=cwd, env=run_env)
    else:
        from ..security.privilege import run_with_timeout

        result = run_with_timeout(cmd, cwd=cwd, env=run_env)
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode != 0:
        logger.error("Command failed (exit {}):\n{}", result.returncode, result.stderr[:500])
    else:
        logger.debug("Command OK: {}", cmd[0])
    return result.returncode == 0, output


def _install_file(content: str, dest: Path, mode: str = "0644") -> tuple[bool, str]:
    """Write *content* to *dest* inside a root-owned rootfs, with privilege.

    debootstrap/pacstrap create the rootfs tree owned by root, so the
    unprivileged build process cannot write into it directly — a plain
    ``Path.write_text`` raises ``PermissionError`` (this is the ``/etc/hostname``
    failure). The escalation session's stdin is already consumed by the sudo
    password, so we can't pipe content through ``sudo tee`` either.

    Instead, stage the content in a user-owned temp file and ``install`` it into
    place through :func:`_run_logged` (which sudo-wraps when escalated). ``install``
    sets ownership to the running user (root) and an explicit mode in one step,
    and passing content via a file sidesteps any shell-quoting of the payload.
    """
    fd, tmp = tempfile.mkstemp(prefix="archon_rootfs_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        return _run_logged(["install", "-D", "-m", mode, "-T", tmp, str(dest)])
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# Kernel virtual filesystems bind-mounted into the chroot before apt/dpkg run.
# Kernel and live-boot postinst scripts (notably update-initramfs) need these
# present; without them the initramfs is silently not generated and the live
# ISO ends up with no initrd. Order matters — /dev before /dev/pts.
_CHROOT_BINDS = (
    ("proc", ["mount", "-t", "proc", "proc"]),
    ("sys", ["mount", "-t", "sysfs", "sys"]),
    ("dev", ["mount", "--bind", "/dev"]),
    ("dev/pts", ["mount", "--bind", "/dev/pts"]),
)


def _mount_chroot_binds(rootfs_path: Path) -> list[str]:
    """Bind /proc, /sys, /dev into the chroot; return the targets mounted.

    Returns only the successfully-mounted targets so the caller can unmount
    exactly those in a finally block. These MUST be unmounted before the rootfs
    is squashed, or mksquashfs would descend into the live host's /proc & /dev.
    """
    mounted: list[str] = []
    for rel, cmd in _CHROOT_BINDS:
        target = rootfs_path / rel
        ok, _ = _run_logged(cmd + [str(target)])
        if ok:
            mounted.append(str(target))
    return mounted


def _umount_chroot_binds(mounted: list[str]) -> None:
    """Lazily unmount chroot bind targets in reverse order (best effort)."""
    for target in reversed(mounted):
        _run_logged(["umount", "-l", target])


def _chroot_cmd(rootfs_path: Path) -> list[str]:
    """Chroot argv with the non-interactive debconf frontend baked into it.

    ``DEBIAN_FRONTEND=noninteractive`` is set here, in the *target* command's
    argv, rather than passed as an ``env=`` to the runner. That distinction is the
    whole point: privileged commands go through ``sudo``, which resets the
    environment (``env_reset`` in sudoers is on by default), so a variable set in
    Archon's own environment is stripped before ``apt-get`` ever sees it. debconf
    then falls back to its default whiptail frontend, and a package with a
    config template (``wireshark-common``) prompts on a stdin that ``sudo -S``
    already consumed — with no TTY behind it, blocking the build forever.

    ``DEBCONF_NONINTERACTIVE_SEEN`` tells debconf the dialogs are already dealt
    with, so a later ``apt-get`` in the same rootfs doesn't re-prompt.

    ``/usr/bin/env`` is coreutils, always present in a debootstrap base.
    """
    return [
        "chroot", str(rootfs_path), "/usr/bin/env",
        "DEBIAN_FRONTEND=noninteractive",
        "DEBCONF_NONINTERACTIVE_SEEN=true",
    ]


# apt/dpkg must never stop for input. These keep dpkg on the recorded conffile
# defaults instead of opening an editor/prompt with no TTY — the same hang class
# as the debconf dialog above, at the dpkg layer.
_APT_NONINTERACTIVE = [
    "-o", "Dpkg::Options::=--force-confdef",
    "-o", "Dpkg::Options::=--force-confold",
]


def _apt_install(chroot: list[str], pkgs: list[str], env: dict) -> tuple[bool, list[str]]:
    """Install *pkgs* in the chroot resiliently; return (all_ok, failed).

    Tries the whole set in one transaction (fast path). apt aborts the *entire*
    transaction if a single name is missing from the enabled repos, so on failure
    we retry per package — the installable ones still land and only the genuinely
    unavailable names are reported. This is what keeps one bad tool name from
    leaving the ISO empty.
    """
    if not pkgs:
        return True, []
    base = chroot + ["apt-get", "install", "-y", "--no-install-recommends"] + _APT_NONINTERACTIVE
    ok, _ = _run_logged(base + pkgs, env=env)
    if ok:
        return True, []
    logger.warning("Batch install failed; retrying {} packages individually …", len(pkgs))
    failed: list[str] = []
    for pkg in pkgs:
        ok, _ = _run_logged(base + [pkg], env=env)
        if not ok:
            failed.append(pkg)
    return not failed, failed


def _setup_kali_repo(rootfs_path: Path, env: dict) -> bool:
    """Layer the Kali rolling repo onto a Debian rootfs, pinned low.

    Pinned to priority 100 so Debian (500) stays the default source; Kali is used
    only for packages Debian doesn't provide (metasploit-framework, burpsuite,
    veracrypt, …). This keeps the base stable while making the security toolset
    installable. Requires ca-certificates/gnupg/curl already in the chroot.
    """
    chroot = _chroot_cmd(rootfs_path)
    apt = rootfs_path / "etc" / "apt"
    ok_list, _ = _install_file(
        "deb http://http.kali.org/kali kali-rolling main contrib non-free non-free-firmware\n",
        apt / "sources.list.d" / "kali.list",
    )
    ok_pin, _ = _install_file(
        "Package: *\nPin: release o=Kali\nPin-Priority: 100\n",
        apt / "preferences.d" / "kali.pref",
    )
    # Fetch and de-armor the Kali archive signing key (the keyring package itself
    # lives only in the Kali repo, so we can't apt-install it first).
    ok_key, _ = _run_logged(
        chroot
        + [
            "sh",
            "-c",
            "curl -fsSL https://archive.kali.org/archive-key.asc "
            "| gpg --dearmor -o /etc/apt/trusted.gpg.d/kali-archive-keyring.gpg",
        ],
        env=env,
    )
    ok_upd, _ = _run_logged(chroot + ["apt-get", "update"], env=env)
    return ok_list and ok_pin and ok_key and ok_upd


async def build_debian_rootfs(profile: DistroProfile, rootfs_path: Path) -> bool:
    """Build a Debian minimal rootfs using debootstrap.

    Args:
        profile:     DistroProfile with packages, locale, timezone, hostname.
        rootfs_path: Target rootfs directory (will be created if absent).

    Returns:
        ``True`` on success.

    Raises:
        PermissionError: If not running as root.
    """
    _require_root("build_debian_rootfs")
    rootfs_path = rootfs_path.resolve()
    rootfs_path.mkdir(parents=True, exist_ok=True)

    try:
        from ..config import get_config

        cfg = get_config()
        mirror = cfg.distro_builder.debian_mirror
        suite = cfg.distro_builder.debian_suite
    except Exception:
        mirror = "auto"
        suite = "bookworm"

    mirror = await asyncio.to_thread(_select_debian_mirror, mirror, suite)

    logger.info("Running debootstrap {} {} …", suite, rootfs_path)

    with Progress(
        SpinnerColumn(), TextColumn("[bold]{task.description}"), TimeElapsedColumn()
    ) as p:
        task = p.add_task("debootstrap …", total=None)

        ok, log = await asyncio.to_thread(
            _run_logged,
            ["debootstrap", "--arch=amd64", suite, str(rootfs_path), mirror],
        )
        p.update(task, completed=1, total=1)

    if not ok:
        logger.error("debootstrap failed.")
        return False

    # ── chroot setup ──────────────────────────────────────────────────────────
    # The rootfs is root-owned (debootstrap ran as root), so config files must be
    # written through the privilege session, not with a direct Path.write_text.
    chroot = _chroot_cmd(rootfs_path)

    # Set hostname
    await asyncio.to_thread(
        _install_file, profile.hostname + "\n", rootfs_path / "etc" / "hostname"
    )

    # Set timezone
    await asyncio.to_thread(
        _install_file, profile.timezone + "\n", rootfs_path / "etc" / "timezone"
    )
    await asyncio.to_thread(
        _run_logged,
        chroot + ["ln", "-sf", f"/usr/share/zoneinfo/{profile.timezone}", "/etc/localtime"],
    )

    # Locale: write locale.gen now, but defer locale-gen until the `locales`
    # package is installed below — running it against a base debootstrap tree
    # fails with "locale-gen: not found" (exit 127) and poisons apt with locale
    # warnings.
    locale_gen = rootfs_path / "etc" / "locale.gen"
    existing = locale_gen.read_text(encoding="utf-8") if locale_gen.exists() else ""
    await asyncio.to_thread(
        _install_file, existing + f"\n{profile.locale} UTF-8\n", locale_gen
    )

    # Critical infrastructure that MUST install for a bootable, usable live ISO:
    #   * initramfs-tools builds the initrd; live-boot supplies the hooks that
    #     mount live/filesystem.squashfs; live-config handles first boot;
    #     systemd-sysv is the init a --variant=minbase tree lacks.
    #   * locales is required so locale-gen works (and to silence apt's locale
    #     warnings that previously surfaced as spurious failures).
    #   * the prebuilt kernel, when requested, so its postinst + our explicit
    #     regen produce a live-capable initramfs.
    #   * ca-certificates/gnupg/curl only when the Kali repo will be added, to
    #     fetch and verify its signing key.
    live_pkgs = [
        "initramfs-tools",
        "live-boot",
        "live-config",
        "live-config-systemd",
        "systemd-sysv",
        "locales",
    ]
    if profile.kernel.source == "prebuilt":
        live_pkgs.append(_debian_kernel_package(profile.kernel.flavor))
    if profile.kali_repo:
        live_pkgs += ["ca-certificates", "gnupg", "curl"]

    env = dict(os.environ)
    env["DEBIAN_FRONTEND"] = "noninteractive"

    # Bind /proc, /sys, /dev so kernel/live-boot postinst scripts behave; unmount
    # in the finally so the rootfs is clean before it is squashed.
    mounted = await asyncio.to_thread(_mount_chroot_binds, rootfs_path)
    try:
        # apt needs a package index inside the chroot before it can install anything.
        await asyncio.to_thread(_run_logged, chroot + ["apt-get", "update"], env=env)

        logger.info("Installing {} live/base packages …", len(live_pkgs))
        infra_ok, _ = await asyncio.to_thread(
            _run_logged,
            chroot + ["apt-get", "install", "-y", "--no-install-recommends", *_APT_NONINTERACTIVE, *live_pkgs],
            env=env,
        )
        if not infra_ok:
            # live-boot failing is fatal — without it the ISO cannot boot. Only
            # refuse when the live infra is genuinely missing; a non-zero exit
            # can also come from an unrelated postinst.
            live_boot_present = (
                rootfs_path / "usr" / "share" / "initramfs-tools" / "scripts" / "live"
            ).exists()
            if not live_boot_present:
                logger.error("live-boot infrastructure failed to install — ISO would not boot.")
                return False
            logger.warning("Base install returned non-zero but live-boot is present; continuing.")

        # Now that `locales` is installed, generate the requested locale.
        await asyncio.to_thread(_run_logged, chroot + ["locale-gen"], env=env)

        # Security builds: layer the Kali repo so tools absent from Debian
        # (metasploit-framework, burpsuite, veracrypt, …) become installable.
        if profile.kali_repo:
            kali_ok = await asyncio.to_thread(_setup_kali_repo, rootfs_path, env)
            if not kali_ok:
                logger.warning("Kali repo setup failed; Kali-only tools may be skipped.")

        # Profile tools, installed resiliently so one unavailable name does not
        # abort the whole set and leave the ISO empty.
        if profile.packages:
            logger.info("Installing {} profile packages …", len(profile.packages))
            _all_ok, failed = await asyncio.to_thread(
                _apt_install, chroot, list(profile.packages), env
            )
            if failed:
                logger.warning(
                    "{} package(s) could not be installed (skipped): {}",
                    len(failed),
                    ", ".join(failed),
                )

        # Regenerate the initramfs now that live-boot is installed, so the initrd
        # carries the hooks that mount the squashfs at boot. The kernel postinst
        # does not reliably produce one inside a chroot (that was the "initrd=None,
        # the live ISO cannot boot" failure), so this is the authoritative step for
        # the prebuilt-kernel path. The custom path builds its own initramfs in
        # install_custom_kernel afterwards.
        if profile.kernel.source == "prebuilt":
            await asyncio.to_thread(
                _run_logged,
                chroot
                + [
                    "sh",
                    "-c",
                    'for d in /lib/modules/*/; do k=${d%/}; k=${k##*/}; '
                    '[ -e "/boot/vmlinuz-$k" ] || continue; '
                    'update-initramfs -c -k "$k" 2>/dev/null || update-initramfs -u -k "$k"; '
                    "done",
                ],
                env=env,
            )

        # Cleanup
        await asyncio.to_thread(_run_logged, chroot + ["apt-get", "clean"], env=env)
    finally:
        await asyncio.to_thread(_umount_chroot_binds, mounted)

    # A prebuilt-kernel live rootfs must have a matching initrd or the ISO cannot
    # boot. Fail here with a clear message instead of ~200s later in the live
    # layout step (the custom path stages its initrd after this function).
    if profile.kernel.source == "prebuilt" and not any(
        (rootfs_path / "boot").glob("initrd.img-*")
    ):
        logger.error(
            "no initramfs was generated in {}/boot — live ISO would not boot.", rootfs_path
        )
        return False

    logger.info("Debian rootfs built at {}", rootfs_path)
    return True


async def build_arch_rootfs(profile: DistroProfile, rootfs_path: Path) -> bool:
    """Build an Arch Linux rootfs using pacstrap.

    Args:
        profile:     DistroProfile with packages, locale, timezone, hostname.
        rootfs_path: Target rootfs directory.

    Returns:
        ``True`` on success.

    Raises:
        PermissionError: If not running as root.
    """
    _require_root("build_arch_rootfs")
    rootfs_path = rootfs_path.resolve()
    rootfs_path.mkdir(parents=True, exist_ok=True)

    kernel_pkg = (
        _arch_kernel_package(profile.kernel.flavor)
        if profile.kernel.source == "prebuilt"
        else "linux"
    )
    logger.info("Arch kernel flavor {} -> {}", profile.kernel.flavor, kernel_pkg)
    base_pkgs = ["base", kernel_pkg, "linux-firmware"] + profile.packages

    logger.info("Running pacstrap …")
    with Progress(
        SpinnerColumn(), TextColumn("[bold]{task.description}"), TimeElapsedColumn()
    ) as p:
        task = p.add_task("pacstrap …", total=None)
        ok, _ = await asyncio.to_thread(_run_logged, ["pacstrap", str(rootfs_path)] + base_pkgs)
        p.update(task, completed=1, total=1)

    if not ok:
        logger.error("pacstrap failed.")
        return False

    arch_chroot = ["arch-chroot", str(rootfs_path)]

    # Timezone
    await asyncio.to_thread(
        _run_logged,
        arch_chroot + ["ln", "-sf", f"/usr/share/zoneinfo/{profile.timezone}", "/etc/localtime"],
    )
    await asyncio.to_thread(_run_logged, arch_chroot + ["hwclock", "--systohc"])

    # Locale — the pacstrap tree is root-owned, so writes go through the session.
    locale_gen = rootfs_path / "etc" / "locale.gen"
    existing = locale_gen.read_text(encoding="utf-8") if locale_gen.exists() else ""
    await asyncio.to_thread(
        _install_file, existing + f"\n{profile.locale} UTF-8\n", locale_gen
    )
    await asyncio.to_thread(_run_logged, arch_chroot + ["locale-gen"])
    await asyncio.to_thread(
        _install_file, f"LANG={profile.locale}\n", rootfs_path / "etc" / "locale.conf"
    )

    # Hostname
    await asyncio.to_thread(
        _install_file, profile.hostname + "\n", rootfs_path / "etc" / "hostname"
    )

    logger.info("Arch rootfs built at {}", rootfs_path)
    return True


async def build_buildroot_rootfs(
    profile: DistroProfile,
    rootfs_path: Path,
    jobs: int = 0,
) -> bool:
    """Build a minimal rootfs using Buildroot.

    Args:
        profile:     DistroProfile (kconfig_options are treated as BR2_ options).
        rootfs_path: Where Buildroot output/images/ will be copied.
        jobs:        Parallel make jobs (0 = cpu_count).

    Returns:
        ``True`` on success.

    Raises:
        PermissionError: If not running as root.
    """
    _require_root("build_buildroot_rootfs")
    rootfs_path = rootfs_path.resolve()
    rootfs_path.mkdir(parents=True, exist_ok=True)

    import tempfile

    build_dir = Path(tempfile.mkdtemp(prefix="archon_buildroot_"))
    num_jobs = jobs or os.cpu_count() or 1

    # Download Buildroot snapshot
    logger.info("Downloading Buildroot snapshot …")
    import httpx

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(_BUILDROOT_URL)
        resp.raise_for_status()
        tarball = build_dir / "buildroot.tar.gz"
        tarball.write_bytes(resp.content)

    # Extract
    await asyncio.to_thread(
        subprocess.run,
        ["tar", "-xzf", str(tarball), "-C", str(build_dir), "--strip-components=1"],
        check=True,
    )
    tarball.unlink()

    # Generate .config from profile BR2_ options + defaults
    config_lines = ['BR2_WGET="wget --passive-ftp -nd -t 3"']
    for key, val in profile.kernel.kconfig_options.items():
        if key.startswith("BR2_"):
            config_lines.append(f"{key}={val}")
    config_lines.append(f'BR2_TARGET_GENERIC_HOSTNAME="{profile.hostname}"')
    (build_dir / ".config").write_text("\n".join(config_lines) + "\n", encoding="utf-8")

    # Merge defconfig
    await asyncio.to_thread(
        _run_logged,
        ["make", "olddefconfig"],
        cwd=str(build_dir),
    )

    # Build
    logger.info("Building Buildroot rootfs with {} jobs …", num_jobs)
    with Progress(
        SpinnerColumn(), TextColumn("[bold]{task.description}"), TimeElapsedColumn()
    ) as p:
        task = p.add_task("Buildroot make …", total=None)
        ok, _ = await asyncio.to_thread(
            _run_logged,
            ["make", f"-j{num_jobs}"],
            cwd=str(build_dir),
        )
        p.update(task, completed=1, total=1)

    if not ok:
        logger.error("Buildroot make failed.")
        return False

    # Copy output
    import shutil

    images_dir = build_dir / "output" / "images"
    for f in images_dir.glob("rootfs.*"):
        dest = rootfs_path / f.name
        shutil.copy2(str(f), str(dest))
        logger.info("Copied {} to {}", f.name, dest)

    shutil.rmtree(str(build_dir), ignore_errors=True)
    logger.info("Buildroot rootfs complete at {}", rootfs_path)
    return True
