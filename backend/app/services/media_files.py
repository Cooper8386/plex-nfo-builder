"""Temporary media companions that remain readable by the media server."""
import os
import secrets
import stat
from pathlib import Path


def create_media_temp(destination: Path) -> tuple[int, Path]:
    """Create exclusively with the process umask; retain existing file permissions.

    Unlike private configuration, NFOs and artwork must be readable by Plex
    running under another user. mkstemp's fixed 0600 mode breaks that use.
    Callers validate the destination boundary before creating its parent.
    """
    temporary = destination.parent / f".pnb-{secrets.token_hex(16)}.tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temporary, flags, 0o666)
    try:
        if os.name == "posix":
            try:
                existing = destination.lstat()
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISREG(existing.st_mode):
                    # Never follow a destination symlink or carry set-ID bits
                    # onto a new inode owned by the application user.
                    os.chmod(descriptor, stat.S_IMODE(existing.st_mode) & 0o777)
        return descriptor, temporary
    except BaseException:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
