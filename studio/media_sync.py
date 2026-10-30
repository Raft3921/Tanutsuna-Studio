"""Drop stale media references after a file or its folder is removed on disk."""
from pathlib import Path


def remove_missing(library):
    ids={a.id for a in library.assets.values() if a.kind in ('media','image') and not Path(a.path).is_file()}
    if not ids:return 0
    for sequence in library.sequences:sequence.clips=[c for c in sequence.clips if c.asset_id not in ids]
    for event in library.events:event.asset_ids=[aid for aid in event.asset_ids if aid not in ids]
    for aid in ids:library.assets.pop(aid,None)
    library.sync_compounds();library.validate()
    return len(ids)
