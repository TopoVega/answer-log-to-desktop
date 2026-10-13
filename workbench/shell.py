"""Native shell actions: put a file on the clipboard, or reveal it in Explorer.

"Copy the file" (CF_HDROP) is what lets the user paste a dossier straight into
another agent's attachment box. Copying the *text* would lose the point of the
handoff document: the receiving platform reads it as a file, and the read-only
preamble only lands reliably when the file itself travels.

Implemented over ctypes rather than pywin32 to keep the packaged exe free of an
extra dependency.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import config

# Where a shell action is allowed to touch. Without this, the HTTP endpoint
# would be a generic "reveal/clipboard any path I name" primitive.
_ALLOWED_ROOTS = (config.SHARED_ROOT, config.DATA_ROOT)


def _allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(resolved == root or root in resolved.parents for root in _ALLOWED_ROOTS)


def _scoped(path: str) -> Path | None:
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.is_file() or not _allowed(p):
        return None
    return p.resolve()


class ShellError(RuntimeError):
    pass


def reveal_in_folder(path: str) -> str:
    """Open Explorer with the file selected (not opened)."""
    p = _scoped(path)
    if not p:
        raise ShellError(f"路径不存在或不在工作台目录内: {path}")
    if sys.platform.startswith("win"):
        # `explorer /select,<path>` both opens the folder and highlights the file.
        subprocess.Popen(["explorer", "/select,", str(p)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])
    return str(p)


def open_folder(path: str) -> str:
    """Open the containing directory itself."""
    p = _scoped(path)
    target = p.parent if p else (config.HANDOFF_DIR if config.HANDOFF_DIR.is_dir() else config.SHARED_ROOT)
    if sys.platform.startswith("win"):
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return str(target)


def copy_files_to_clipboard(paths: list[str]) -> list[str]:
    """Place real file objects on the clipboard so Ctrl+V pastes attachments."""
    if sys.platform == "darwin":
        joined = '"'.join(f'POSIX file "{p}"' for p in paths)
        subprocess.run(["osascript", "-e", f'set the clipboard to {{{joined}}}'], check=True)
        return paths
    if not sys.platform.startswith("win"):
        raise ShellError("该平台不支持把文件放入剪贴板")
    return _win_set_hdrop([_require(p) for p in paths])


def copy_file_to_clipboard(path: str) -> str:
    return copy_files_to_clipboard([path])[0]


def _require(path: str) -> str:
    p = _scoped(path)
    if not p:
        raise ShellError(f"路径不存在或不在工作台目录内: {path}")
    return str(p)


CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040


def _win_set_hdrop(paths: list[str]) -> list[str]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]

    # DROPFILES: DWORD pFiles; POINT pt; BOOL fNC; BOOL fWide  -> 20 bytes,
    # followed by a double-NUL-terminated list of wide paths.
    header = 20
    blob = "\0".join(paths) + "\0\0"
    data = blob.encode("utf-16-le")
    payload = header + len(data)

    if not user32.OpenClipboard(None):
        raise ShellError(f"OpenClipboard 失败: {ctypes.get_last_error()}")
    owned = True
    try:
        user32.EmptyClipboard()
        # GMEM_ZEROINIT matters: GlobalAlloc does not otherwise clear the block,
        # and a garbage fWide makes the shell parse the list as ANSI and silently
        # report zero files.
        hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, payload)
        if not hmem:
            raise ShellError("GlobalAlloc 失败（内存不足？）")
        addr = kernel32.GlobalLock(hmem)
        if not addr:
            kernel32.GlobalFree(hmem)
            raise ShellError("GlobalLock 失败")
        try:
            fields = (ctypes.c_uint32 * 5)(header, 0, 0, 0, 1)  # pFiles, pt.x, pt.y, fNC, fWide
            ctypes.memmove(addr, fields, header)
            ctypes.memmove(addr + header, data, len(data))
        finally:
            kernel32.GlobalUnlock(hmem)
        if not user32.SetClipboardData(CF_HDROP, hmem):
            raise ShellError(f"SetClipboardData 失败: {ctypes.get_last_error()}")
        # Ownership transferred to the clipboard; freeing it here would corrupt it.
        owned = False
    finally:
        user32.CloseClipboard()
    return paths


def clipboard_has_files() -> bool:
    """Used by the tests to prove a real HDROP landed, not just text."""
    if not sys.platform.startswith("win"):
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    return bool(user32.IsClipboardFormatAvailable(CF_HDROP))
