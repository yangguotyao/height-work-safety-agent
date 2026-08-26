from __future__ import annotations

import sys
from pathlib import Path


def convert(source: Path, target: Path) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        document = word.Documents.Open(
            FileName=str(source),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Visible=False,
            OpenAndRepair=True,
        )
        document.SaveAs2(FileName=str(target), FileFormat=12, AddToRecentFiles=False)
    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def main() -> int:
    if len(sys.argv) != 3:
        print("用法：word_doc_converter_worker.py SOURCE.doc TARGET.docx", file=sys.stderr)
        return 2
    source = Path(sys.argv[1]).resolve()
    target = Path(sys.argv[2]).resolve()
    try:
        convert(source, target)
    except ImportError:
        print("当前 Windows 环境缺少 pywin32，无法转换旧版 DOC", file=sys.stderr)
        return 3
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 4
    return 0 if target.exists() else 5


if __name__ == "__main__":
    raise SystemExit(main())
