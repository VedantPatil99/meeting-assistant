"""Runs before the app in the packaged build.

PyAV (FFmpeg, about 67 MB) is left out of the build because the app records WAV files and
reads them itself (transcribe.read_wav). faster-whisper still imports `av` when it loads, but
only uses it to decode other formats, so a placeholder is enough.
"""

import sys
import types


def _missing(name):
    if name.startswith("__"):  # Python and Qt's import hook probe for __wrapped__, __path__ etc.
        raise AttributeError(name)
    raise ImportError("This build reads WAV recordings only; decoding other audio formats needs PyAV, "
                      "which isn't included.")


placeholder = types.ModuleType("av")
placeholder.__getattr__ = _missing
sys.modules["av"] = placeholder
