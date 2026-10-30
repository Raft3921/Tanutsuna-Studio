"""Undoable audio roles and frame-accurate silence cuts; source media stays intact."""
from pathlib import Path
import copy
import math
import re
import tempfile
from cutraft.model import Clip, uid
from cutraft.media_cache import ffmpeg_executable
from .jobs import run_command
from .takes import takes_for

ROLES = {'voice':'自分の声', 'desktop':'ゲーム・PC音', 'sfx':'効果音・追加音', 'bgm':'BGM'}


def role_paths(project):
    result = {}
    for folder,take in takes_for(project):
        for role in ('voice','desktop'):
            if role in take.get('files',{}): result[str((folder/take['files'][role]).resolve())] = role
    return result


def audio_role(asset, paths=None):
    return asset.payload.get('studio_audio_role') or (paths or {}).get(str(Path(asset.path).resolve()),'sfx')


def merge_ranges(ranges):
    result=[]
    for a,b in sorted(ranges):
        if b<=a:continue
        if result and a<=result[-1][1]:result[-1]=(result[-1][0],max(b,result[-1][1]))
        else:result.append((a,b))
    return result


def subtract_ranges(ranges, removed):
    result=[];removed=merge_ranges(removed)
    for a,b in merge_ranges(ranges):
        cursor=a
        for left,right in removed:
            if right<=cursor:continue
            if left>=b:break
            if left>cursor:result.append((cursor,min(left,b)))
            cursor=max(cursor,right)
            if cursor>=b:break
        if cursor<b:result.append((cursor,b))
    return result


def ripple_cut(sequence, ranges):
    """Remove the same half-open spans on every track and close time globally."""
    cuts=merge_ranges((max(0,a),min(sequence.end,b)) for a,b in ranges)
    if not cuts:return
    def shifted(frame):
        return frame-sum(max(0,min(frame,b)-a) for a,b in cuts if a<frame)
    clips=[]
    for clip in sequence.clips:
        kept=subtract_ranges([(clip.start,clip.end)],cuts)
        for index,(a,b) in enumerate(kept):
            part=copy.deepcopy(clip)
            if index:part.id=uid()
            part.start=shifted(a);part.source_in=clip.source_in+a-clip.start;part.duration=b-a
            part.partition_transform_keys()
            clips.append(part)
    sequence.clips=clips


def parse_silences(lines, duration):
    start=None;result=[]
    for line in lines:
        match=re.search(r'silence_start:\s*([-+0-9.eE]+)',line)
        if match:start=max(0,float(match[1]))
        match=re.search(r'silence_end:\s*([-+0-9.eE]+)',line)
        if match and start is not None:
            result.append((start,min(duration,float(match[1]))));start=None
    if start is not None:result.append((start,duration))
    return merge_ranges(result)


def analyze_silence(library, sequence_id, clip_ids, threshold, minimum, padding, cancel):
    seq=next(s for s in library.sequences if s.id==sequence_id);rate=float(seq.rate)
    candidates=[];speaking=[]
    with tempfile.TemporaryDirectory(prefix='tanutsuna-silence-') as directory:
        for index,clip in enumerate(c for c in seq.clips if c.id in clip_ids):
            asset=library.assets[clip.asset_id]
            if asset.kind!='media' or not asset.has_audio:raise ValueError('音声ファイルのクリップを指定してください。複合クリップは中を開いて解析できます。')
            duration=clip.duration/rate;log=Path(directory)/f'{index}.log'
            run_command([ffmpeg_executable(),'-hide_banner','-nostdin','-nostats','-threads','1',
                '-ss',f'{clip.source_in/rate:.9f}','-i',asset.path,'-t',f'{duration:.9f}',
                '-vn','-filter_threads','1','-af',f'silencedetect=noise={threshold}dB:d={minimum}',
                '-f','null','-'],log,cancel)
            with log.open(encoding='utf-8',errors='replace') as stream:silent=parse_silences(stream,duration)
            # Round inward; retain padding at both sides of each detected pause.
            local=merge_ranges((max(clip.start,clip.start+math.ceil((a+padding)*rate)),
                min(clip.end,clip.start+math.floor((b-padding)*rate))) for a,b in silent)
            candidates.extend(local)
            speaking.extend(subtract_ranges([(clip.start,clip.end)],local))
    # If two voice recordings overlap, keep any interval where either is speaking.
    return subtract_ranges(merge_ranges(candidates),speaking)


def sequence_tree(library, sequence):
    visited=set();stack=[sequence]
    while stack:
        seq=stack.pop()
        if seq.id in visited:continue
        visited.add(seq.id);yield seq
        for clip in seq.clips:
            asset=library.assets[clip.asset_id]
            if asset.kind=='compound':
                stack.extend(s for s in library.sequences if s.id==asset.payload['sequence_id'])


def apply_mix(library, sequence, assignments, factors, bgm='keep'):
    for asset_id,role in assignments.items():
        if role not in ROLES:raise ValueError('音の役割を選択してください。')
        asset=library.assets[asset_id]
        if role=='bgm' and (Path(asset.path).suffix.lower()!='.mp3' or asset.has_video):
            raise ValueError('BGMの役割はMP3音声に指定してください。')
        asset.payload['studio_audio_role']=role
    # Each press applies the displayed multiplier to the current mix. Fades survive.
    for seq in sequence_tree(library,sequence):
        for clip in seq.clips:
            asset=library.assets[clip.asset_id]
            if asset.kind!='media' or not asset.has_audio:continue
            factor=factors.get(audio_role(asset),1)
            if not 0<=factor<=5:raise ValueError('音量の割合は0〜500%で指定してください。')
            clip.volume=min(5,clip.volume*factor)
            clip.volume_keys={frame:min(5,gain*factor) for frame,gain in clip.volume_keys.items()}
    if bgm=='keep':return
    # BGM replacement affects this timeline; nested compositions remain independent.
    sequence.clips=[c for c in sequence.clips if audio_role(library.assets[c.asset_id])!='bgm']
    if bgm is None:return
    asset=library.assets[bgm]
    if Path(asset.path).suffix.lower()!='.mp3' or not asset.has_audio or asset.has_video:
        raise ValueError('BGMはMP3音声から選んでください。')
    asset.payload['studio_audio_role']='bgm'
    occupied={c.track for c in sequence.clips}
    track=next((-index for index in range(1,sequence.audio_tracks+1) if -index not in occupied),-sequence.audio_tracks-1)
    sequence.audio_tracks=max(sequence.audio_tracks,-track)
    duration=min(asset.frames(sequence.rate),sequence.end)
    if duration>0:sequence.clips.append(Clip(uid(),asset.id,0,0,duration,track=track,volume=factors.get('bgm',1)))


def cut_library(library, sequence_id, ranges):
    """Propagate shared compound edits to parents, including their other tracks."""
    order=[];visited=set()
    def visit(seq):
        if seq.id in visited:return
        visited.add(seq.id)
        for clip in seq.clips:
            asset=library.assets[clip.asset_id]
            if asset.kind=='compound':visit(next(s for s in library.sequences if s.id==asset.payload['sequence_id']))
        order.append(seq)
    for seq in library.sequences:visit(seq)
    pending={sequence_id:merge_ranges(ranges)}
    for child in order:
        cuts=pending.get(child.id,[])
        if not cuts:continue
        for parent in library.sequences:
            for clip in parent.clips:
                asset=library.assets[clip.asset_id]
                if asset.kind!='compound' or asset.payload['sequence_id']!=child.id:continue
                mapped=[(clip.start+max(a,clip.source_in)-clip.source_in,
                         clip.start+min(b,clip.source_in+clip.duration)-clip.source_in)
                        for a,b in cuts if a<clip.source_in+clip.duration and b>clip.source_in]
                pending[parent.id]=merge_ranges(pending.get(parent.id,[])+mapped)
    for seq in order:
        if seq.id in pending:ripple_cut(seq,pending[seq.id])
        for clip in seq.clips:
            asset=library.assets[clip.asset_id]
            cuts=pending.get(asset.payload.get('sequence_id'),[]) if asset.kind=='compound' else []
            if not cuts:continue
            def shifted(frame):return frame-sum(max(0,min(frame,b)-a) for a,b in cuts if a<frame)
            clip.volume_keys={shifted(f):v for f,v in clip.visible_volume_keys().items()}
            clip.transform_keys={name:{shifted(f):v for f,v in keys.items()} for name,keys in clip.transform_keys.items()}
            clip.source_in=shifted(clip.source_in)
    for asset in library.assets.values():
        if asset.kind=='compound' and asset.payload['sequence_id'] in pending:
            child=next(s for s in library.sequences if s.id==asset.payload['sequence_id'])
            asset.duration_ms=max(1,math.ceil(child.end*1000/child.rate))
