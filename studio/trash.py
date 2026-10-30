"""Recoverable project data deletion. No permanent deletion or external-file removal."""
from pathlib import Path
from datetime import datetime
from uuid import uuid4
import copy
import json
import os
from cutraft.model import Sequence, uid
from cutraft.storage import load_library, save_library
from cutraft.session import SessionStore
from .workspace import atomic_text
from .takes import load_take, write_take


class DataTrash:
    def __init__(self, workspace, root):
        self.workspace=workspace;self.index_file=Path(root)/'trash-index.json'

    def all_entries(self):
        if not self.index_file.is_file():return []
        values=json.loads(self.index_file.read_text(encoding='utf-8'))
        # Project-relative receipts remain discoverable after moving a whole video.
        for value in values:
            ticket=Path(value['ticket'])
            if not (ticket/'receipt.json').is_file() and value['kind']!='project':
                project=next((p for p in self.workspace.data['projects'] if p['id']==value['project_id']),None)
                if project:
                    candidate=Path(project['folder'])/'.tanutsuna-trash'/ticket.name
                    if (candidate/'receipt.json').is_file():value['ticket']=str(candidate.resolve())
        return values

    def entries(self):
        return [v for v in self.all_entries() if (Path(v['ticket'])/'receipt.json').is_file()]

    def register(self, entry):
        values=self.all_entries();values.insert(0,entry);self.write_index(values)

    def write_index(self, values):atomic_text(self.index_file,json.dumps(values,ensure_ascii=False,indent=2))

    def begin(self, project, kind, label, parent):
        ticket=Path(parent)/'.tanutsuna-trash'/uuid4().hex
        (ticket/'data').mkdir(parents=True,exist_ok=False)
        receipt={'version':1,'kind':kind,'label':label,'date':datetime.now().astimezone().isoformat(),
            'project_id':project['id'],'project':copy.deepcopy(project),'files':[],'takes':[]}
        return ticket,receipt

    def finish(self,ticket,receipt):
        atomic_text(ticket/'receipt.json',json.dumps(receipt,ensure_ascii=False,indent=2))
        self.register({'ticket':str(ticket),'label':receipt['label'],'kind':receipt['kind'],'project_id':receipt['project_id'],'date':receipt['date']})
        return str(ticket)

    def rebase_index(self,old,new):
        # Receipts for a video's assets travel with that video's own directory.
        values=self.all_entries()
        for value in values:
            try:relative=Path(value['ticket']).relative_to(old)
            except ValueError:continue
            value['ticket']=str(Path(new)/relative)
        self.write_index(values)

    def delete_project(self,project):
        source=Path(project['folder']).resolve()
        if not source.is_dir():raise ValueError('動画フォルダが見つかりません。')
        metadata=source/'project.json'
        if project not in self.workspace.data['projects'] or not metadata.is_file() or json.loads(metadata.read_text(encoding='utf-8')).get('id')!=project['id']:
            raise ValueError('動画情報が一致しないため削除できません。')
        ticket,receipt=self.begin(project,'project',project['title'],source.parent)
        destination=ticket/'data'/'project'
        previous=list(self.workspace.data['projects'])
        try:
            os.rename(source,destination)
            self.rebase_index(source,destination)
            self.workspace.data['projects']=[p for p in previous if Path(p['folder']).resolve()!=source]
            self.workspace.save()
            receipt['source']=str(source)
            return self.finish(ticket,receipt)
        except Exception:
            self.workspace.data['projects']=previous
            if destination.exists():
                os.rename(destination,source);self.rebase_index(destination,source)
            self.workspace.save()
            raise

    @staticmethod
    def remove_assets(library, ids):
        for seq in library.sequences:seq.clips=[c for c in seq.clips if c.asset_id not in ids]
        for event in library.events:event.asset_ids=[a for a in event.asset_ids if a not in ids]
        for aid in ids:library.assets.pop(aid,None)
        library.sync_compounds();library.validate()

    def delete_data(self,project,paths=(),asset_ids=(),take_folders=(),sequence_id=None,event_id=None,label='素材'):
        root=Path(project['folder']).resolve();path=self.workspace.ensure_library(project)
        before=load_library(str(path));after=before.clone()
        paths={Path(p).resolve() for p in paths}
        folders={Path(p).resolve() for p in take_folders}
        for folder in folders:
            if not folder.is_relative_to(root/'01-recordings') or folder==root/'01-recordings':
                raise ValueError('この動画の収録フォルダだけを削除できます。')
        ids=set(asset_ids)
        ids.update(a.id for a in before.assets.values() if a.kind in ('media','image') and
            (Path(a.path).resolve() in paths or any(Path(a.path).resolve().is_relative_to(f) for f in folders)))
        # Include owned files for source-list deletion; external files are only unregistered.
        for aid in ids:
            asset=before.assets.get(aid)
            if asset and asset.kind in ('media','image'):paths.add(Path(asset.path).resolve())
        kind='take' if folders else 'sequence' if sequence_id else 'event' if event_id else 'media'
        removed_sequences=[]
        removed_events=[]
        replacement_id=None
        if sequence_id:
            selected=next((s for s in after.sequences if s.id==sequence_id),None)
            if selected is None:raise ValueError('編集版が見つかりません。')
            removed_sequences=[sequence_id]
            ids.update(a.id for a in after.assets.values() if a.kind=='compound' and a.payload.get('sequence_id')==sequence_id)
            after.sequences=[s for s in after.sequences if s.id!=sequence_id]
            if not any(s.kind!='compound' for s in after.sequences):
                replacement_id=uid()
                after.sequences.append(Sequence(replacement_id,name=project['title'],width=1920,height=1080,fps_num=50,fps_den=1,video_tracks=1,audio_tracks=1))
            for event in after.events:event.sequence_ids=[s for s in event.sequence_ids if s!=sequence_id]
            if after.active_id==sequence_id:after.active_id=next(s.id for s in after.sequences if s.kind!='compound')
        if event_id:
            removed_events=[event_id]
            after.events=[e for e in after.events if e.id!=event_id]
            if after.active_event_id==event_id:after.active_event_id=after.events[0].id if after.events else ''
        self.remove_assets(after,ids)
        ticket,receipt=self.begin(project,kind,label,root)
        receipt.update({'asset_ids':sorted(ids),'sequence_ids':removed_sequences,'event_ids':removed_events,'replacement_id':replacement_id})
        save_library(before,str(ticket/'before.cutraft'))
        # Preserve the exact removed placements, without replacing edits made later.
        after_clips={c.id for s in after.sequences for c in s.clips}
        receipt['clip_ids']=[c.id for s in before.sequences for c in s.clips if c.id not in after_clips]
        owned=[p for p in paths if p.is_file() and p.is_relative_to(root) and
            (p.is_relative_to(root/'01-recordings') or p.is_relative_to(root/'02-assets') or p.is_relative_to(root/'04-exports'))]
        sources=sorted(folders,key=str)+[p for p in sorted(owned,key=str) if not any(p.is_relative_to(f) for f in folders)]
        moved=[];takes=[]
        try:
            for number,source in enumerate(sources):
                target=ticket/'data'/str(number)
                os.rename(source,target)
                moved.append((source,target));receipt['files'].append({'source':str(source.relative_to(root)),'stored':str(target.relative_to(ticket))})
            # Remove selected take roles so auto-import and retries cannot resurrect them.
            for manifest in (root/'01-recordings').glob('*/take.json'):
                data=load_take(manifest.parent);removed={k:v for k,v in data['files'].items() if (manifest.parent/v).resolve() in paths}
                if not removed:continue
                previous=copy.deepcopy(data);takes.append((manifest.parent,previous))
                receipt['takes'].append({'folder':str(manifest.parent.relative_to(root)),'roles':removed,'options':copy.deepcopy(data['options'])})
                for role in removed:
                    data['files'].pop(role);data.setdefault('deleted_roles',[])
                    if role not in data['deleted_roles']:data['deleted_roles'].append(role)
                    if role in ('desktop','voice','avatar'):data['options'][role]=False
                    if role=='vtuber':data['avatar_pending']=False;data['options']['avatar']=False
                write_take(manifest.parent,data)
            save_library(after,str(path))
            SessionStore(path.parent/'recovery').write(after,{'path':str(path),'dirty':False,'frame':0})
            return self.finish(ticket,receipt)
        except Exception:
            for source,target in reversed(moved):
                if target.exists():os.rename(target,source)
            for folder,data in takes:write_take(folder,data)
            save_library(before,str(path))
            SessionStore(path.parent/'recovery').write(before,{'path':str(path),'dirty':False,'frame':0})
            raise

    @staticmethod
    def restore_placements(current,before,receipt):
        for aid in receipt.get('asset_ids',[]):
            if aid in before.assets:current.assets.setdefault(aid,copy.deepcopy(before.assets[aid]))
        for seq in before.sequences:
            if seq.id in receipt.get('sequence_ids',[]) and not any(s.id==seq.id for s in current.sequences):
                current.sequences.append(copy.deepcopy(seq))
        replacement=next((s for s in current.sequences if s.id==receipt.get('replacement_id')),None)
        if replacement and not replacement.clips and replacement.name==receipt['project']['title']:
            current.sequences.remove(replacement)
            for event in current.events:event.sequence_ids=[s for s in event.sequence_ids if s!=replacement.id]
            if current.active_id==replacement.id:current.active_id=current.sequences[0].id
        restored_ids=set(receipt.get('clip_ids',[]))
        for old in before.sequences:
            seq=next((s for s in current.sequences if s.id==old.id),None)
            if seq is None:continue
            present={c.id for c in seq.clips}
            for original in old.clips:
                if original.id not in restored_ids or original.id in present or original.asset_id not in current.assets:continue
                clip=copy.deepcopy(original)
                def occupied(track):return any(c.track==track and c.start<clip.end and c.end>clip.start for c in seq.clips)
                if occupied(clip.track):
                    choices=range(16) if clip.track>=0 else range(-1,-17,-1)
                    free=next((t for t in choices if not occupied(t)),None)
                    if free is None:clip.start=seq.end
                    else:clip.track=free
                seq.clips.append(clip)
                if clip.track>=0:seq.video_tracks=max(seq.video_tracks,clip.track+1)
                else:seq.audio_tracks=max(seq.audio_tracks,-clip.track)
        for event in before.events:
            if event.id in receipt.get('event_ids',[]) and not any(e.id==event.id for e in current.events):
                restored=copy.deepcopy(event)
                restored.asset_ids=[a for a in restored.asset_ids if a in current.assets]
                restored.sequence_ids=[s for s in restored.sequence_ids if any(q.id==s for q in current.sequences)]
                current.events.append(restored)
            target=next((e for e in current.events if e.id==event.id),None)
            if target:
                target.asset_ids=list(dict.fromkeys(target.asset_ids+[a for a in event.asset_ids if a in receipt.get('asset_ids',[]) and a in current.assets]))
                target.sequence_ids=list(dict.fromkeys(target.sequence_ids+[s for s in event.sequence_ids if s in receipt.get('sequence_ids',[])]))
        current.sync_compounds();current.validate()

    def restore(self,ticket):
        ticket=Path(ticket).resolve()
        if not any(Path(v['ticket']).resolve()==ticket for v in self.entries()):raise ValueError('ゴミ箱の項目が見つかりません。')
        receipt=json.loads((ticket/'receipt.json').read_text(encoding='utf-8'))
        if receipt['kind']=='project':
            source=Path(receipt['source']);payload=ticket/'data/project'
            if source.exists():raise ValueError('元の場所に別のフォルダがあります。先に保存先を確認してください。')
            os.rename(payload,source);self.rebase_index(payload,source)
            try:
                self.workspace.ensure_library({**receipt['project'],'folder':str(source)})
                self.workspace.attach(source)
            except Exception:
                os.rename(source,payload);self.rebase_index(source,payload);raise
        else:
            project=next((p for p in self.workspace.data['projects'] if p['id']==receipt['project_id']),None)
            if project is None:raise ValueError('先に、この素材を使う動画をゴミ箱から戻してください。')
            root=Path(project['folder']).resolve();path=self.workspace.library_path(project)
            current=load_library(str(path));before=load_library(str(ticket/'before.cutraft'))
            old_root=Path(receipt['project']['folder']).resolve()
            if old_root!=root:
                for asset in before.assets.values():
                    if asset.kind not in ('media','image'):continue
                    try:relative=Path(asset.path).resolve().relative_to(old_root)
                    except ValueError:continue
                    asset.path=str(root/relative)
            previous=current.clone();self.restore_placements(current,before,receipt)
            moved=[];takes=[]
            try:
                for item in receipt['files']:
                    source=(root/item['source']).resolve();stored=(ticket/item['stored']).resolve()
                    if not source.is_relative_to(root) or not stored.is_relative_to(ticket):raise ValueError('復元パスが正しくありません。')
                    if source.exists():raise ValueError(f'同じ場所にファイルがあります：{source.name}')
                    source.parent.mkdir(parents=True,exist_ok=True);os.rename(stored,source);moved.append((source,stored))
                for item in receipt.get('takes',[]):
                    folder=root/item['folder'];data=load_take(folder);takes.append((folder,copy.deepcopy(data)))
                    for role,file in item['roles'].items():
                        data['files'].setdefault(role,file)
                        data['deleted_roles']=[r for r in data.get('deleted_roles',[]) if r!=role]
                        if role in item['options']:data['options'][role]=item['options'][role]
                        if role=='vtuber':data['options']['avatar']=item['options'].get('avatar',False)
                    write_take(folder,data)
                save_library(current,str(path));SessionStore(path.parent/'recovery').write(current,{'path':str(path),'dirty':False,'frame':0})
            except Exception:
                for source,stored in reversed(moved):os.rename(source,stored)
                for folder,data in takes:write_take(folder,data)
                save_library(previous,str(path));raise
        self.write_index([e for e in self.all_entries() if Path(e['ticket']).resolve()!=ticket])
        receipt['restored']=True
        atomic_text(ticket/'receipt.json',json.dumps(receipt,ensure_ascii=False,indent=2))
        return receipt['project_id']
