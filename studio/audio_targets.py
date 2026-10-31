"""Resolve audio-bearing leaves in a sequence, including nested compounds."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioSource:
    path: str
    source_seconds: float
    duration_seconds: float
    timeline_seconds: float
    asset_id: str


def audio_sources(library, sequence, window=None, timeline_offset=0.0, clip_ids=None):
    """Flatten audio clips to seconds in the caller's timeline coordinate space."""
    result = []
    sequences = {item.id: item for item in library.sequences}
    visited = set()

    def walk(current, offset, limit, ancestry, top=False):
        if current.id in ancestry:
            return
        rate = float(current.rate)
        start_limit, end_limit = limit if limit else (0.0, current.end / rate)
        for clip in current.clips:
            if top and clip_ids is not None and clip.id not in clip_ids:
                continue
            clip_start, clip_end = clip.start / rate, clip.end / rate
            start, end = max(clip_start, start_limit), min(clip_end, end_limit)
            if end <= start:
                continue
            asset = library.assets[clip.asset_id]
            if asset.kind == 'compound':
                child = sequences.get(asset.payload.get('sequence_id'))
                if child is None:
                    continue
                # A compound's source_in and duration use its parent sequence's frame rate.
                source_start = clip.source_in / rate + (start - clip_start)
                source_end = source_start + (end - start)
                child_offset = offset + clip_start - clip.source_in / rate
                walk(child, child_offset, (source_start, source_end), ancestry | {current.id})
            elif asset.kind == 'media' and (asset.has_audio or asset.has_video):
                source = clip.source_in / rate + (start - clip_start)
                result.append(AudioSource(asset.path, source, end - start,
                                          offset + start, asset.id))

    walk(sequence, timeline_offset, window, set(), top=True)
    return result
