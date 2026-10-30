"""Isolated media dependency; packaged apps include the same pinned PyAV wheel."""
import sys
from pathlib import Path
try:
    import av
except ImportError:
    folder=Path(__file__).resolve().parents[1]/'build/live-deps'
    if not folder.is_dir():raise RuntimeError('配信エンジンがありません。配布物をすべて展開してください。')
    sys.path.insert(0,str(folder))
    import av
# FFmpeg errors can include destinations (and thus stream keys). Never forward its log.
av.logging.set_level(-8)  # FFmpeg AV_LOG_QUIET
