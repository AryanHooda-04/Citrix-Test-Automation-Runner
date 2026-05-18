from __future__ import annotations


def get_clipboard_text() -> str:
    try:
        import win32clipboard
    except ImportError as exc:
        raise RuntimeError("pywin32 is required to read text from the Windows clipboard.") from exc

    win32clipboard.OpenClipboard()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            return ""
        data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        return data or ""
    finally:
        win32clipboard.CloseClipboard()
