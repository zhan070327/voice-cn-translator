from __future__ import annotations

import sys


def self_test() -> int:
    """Check bundled imports and Qt without downloading models or opening the game."""
    try:
        import ctranslate2  # noqa: F401
        import faster_whisper  # noqa: F401
        import soundcard  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
        from PySide6.QtWidgets import QApplication, QLabel
        from valorant_translator.models.cos import Bundle, NOTICE
        from valorant_translator.translation.glossary import Glossary
        from valorant_translator.ui.fonts import load_display_fonts
        from valorant_translator.updates.domestic import load_channel

        channel = load_channel()
        if channel.get("edition") == "cpu":
            assert torch.version.cuda is None, "CPU installer contains CUDA PyTorch"
        if channel["channel"] in ("domestic", "dual"):
            Bundle.from_channel(channel)
            assert NOTICE.is_file(), "Bundled model license notice is missing"

        app = QApplication([])
        if getattr(sys, "frozen", False):
            assert load_display_fonts() == ("Rajdhani", "Noto Sans CJK SC"), "Bundled display fonts could not be loaded"
        label = QLabel("启动检查")
        assert label.text() == "启动检查"
        assert Glossary().callout("Go rush A", "en") == "快攻 A。"
        del app
        return 0
    except Exception:
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    from valorant_translator.ui.app import run
    raise SystemExit(run())
