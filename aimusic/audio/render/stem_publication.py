"""Compatibility module alias for the retained Windows implementation.

All application callers use aimusic.audio.publication. Preserve the old API and
Windows simulation seam until the native backend replaces this implementation.
"""
import sys
from aimusic.audio import publication_windows

sys.modules[__name__] = publication_windows
