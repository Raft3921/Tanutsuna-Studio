from pathlib import Path
import os
import sys
import json
from PySide6.QtCore import QStandardPaths,QTimer,Qt
from PySide6.QtGui import QAction,QKeySequence,QIcon,QPixmap,QPainter,QColor
from PySide6.QtWidgets import QApplication,QMainWindow,QMessageBox,QFileDialog,QSplashScreen
from .workspace import Workspace,DuplicateProject
from .desk import ProductionDesk
from .theme import apply_theme
from .jobs import Jobs
from .version import STUDIO_VERSION
from .updater import UpdateWorker


class StudioWindow(QMainWindow):
    """The project shell is the app. The media editor is an on-demand page."""
    def __init__(self,workspace,data_root):
        super().__init__()
        self.workspace=workspace;self.data_root=Path(data_root)
        self.loaded_editor=None;self.loaded_project=None
        from .trash import DataTrash
        self.data_trash=DataTrash(workspace,self.data_root)
        self.media_jobs=Jobs(self);self.media_jobs.finished.connect(self.media_finished)
        self.pending_media=None
        self.update_worker=None;self.update_release=None;self.update_package=None
        self.setWindowTitle('たぬつな Studio');self.resize(1360,900);self.setMinimumSize(1120,740)
        self.desk=ProductionDesk(self,workspace);self.setCentralWidget(self.desk)
        menu=self.menuBar().addMenu('ファイル')
        for title,fn,key in [('録画用の作品を作成',self.desk.quick_record,QKeySequence.StandardKey.New),
            ('動画フォルダを開く',self.open_project_folder,QKeySequence.StandardKey.Open),
            ('保存',self.save_project,QKeySequence.StandardKey.Save),('終了',self.close,QKeySequence.StandardKey.Quit)]:
            action=QAction(title,self);action.setShortcut(QKeySequence(key));action.triggered.connect(lambda checked=False,f=fn:self.desk.protect(f));menu.addAction(action)
        apply_theme(QApplication.instance())

    @property
    def path(self):return self.loaded_editor.path if self.loaded_editor else ''
    @property
    def sequence(self):return self.ensure_editor().sequence
    @property
    def library(self):return self.ensure_editor().library
    @property
    def timeline(self):return self.ensure_editor().timeline
    @property
    def title_inspector(self):return self.ensure_editor().title_inspector

    def ensure_editor(self):
        if self.loaded_editor is None:
            from cutraft.workspace import register_fonts
            register_fonts()
            from .editor_surface import EditorSurface
            self.loaded_editor=EditorSurface(self)
            self.loaded_editor.stateChanged.connect(self.refresh_save_state)
            self.loaded_editor.importer.finished.connect(self.place_imported_media)
        return self.loaded_editor

    def save_before_switch(self):
        if hasattr(self,'desk') and self.desk._planning:
            try:self.desk._planning.flush()
            except Exception as exc:self.desk.status.setText(str(exc));return False
        editor=self.loaded_editor
        if editor is None or not editor.path:return True
        editor.title_inspector.flush();editor.preview.pause()
        if editor.importer.running:
            self.desk.status.setText('素材の読み込みが完了してから動画を切り替えてください。');return False
        if editor.dirty or editor.background_save.future is not None:
            if not editor.save(False):return False
        return True

    def load_project(self,project,sync=True):
        if project is None:return None
        target=self.workspace.ensure_library(project)
        editor=self.ensure_editor()
        if editor.path!=str(target):
            if not self.save_before_switch():return None
            # One target, one recovery store, and no unrelated global last-library restore.
            editor.session_store=None
            editor.open_library(str(target))
            if editor.path!=str(target):return None
            from cutraft.session import SessionStore
            editor.session_store=SessionStore(target.parent/'recovery')
            try:
                restored=editor.session_store.read()
                if restored:
                    snapshot,state=restored
                    original=state.get('path','')
                    if state.get('dirty') and original and Path(original).resolve()==target.resolve():
                        import shutil
                        backup=target.with_suffix('.before-recovery.cutraft')
                        if not backup.exists():shutil.copy2(target,backup)
                        editor.replace_library(snapshot,str(target));editor.dirty=True
                        editor.preview.seek(min(state.get('frame',0),editor.sequence.end))
                        editor.autosave.start()
                        self.desk.status.setText('保存途中だった編集内容を、この動画へ復元しました。')
            except (OSError,ValueError):
                self.desk.status.setText('復旧用データを読めなかったため、保存済みの編集内容を開きました。')
            editor.statusBar().showMessage('この動画の編集内容を開きました。')
            self.loaded_project=project
        self.reconcile_media(project)
        if sync and not editor.importer.running:
            from .take_import import import_take
            from .takes import ready_files
            for folder,take in reversed(takes_for(project)):
                if ready_files(folder,take):import_take(editor,self.workspace,project,folder,take)
        self.refresh_save_state();return editor

    def reconcile_media(self,project):
        from .media_sync import remove_missing
        from cutraft.storage import load_library,save_library
        path=self.workspace.library_path(project)
        if not path.is_file():return 0
        editor=self.loaded_editor
        loaded=editor is not None and editor.path==str(path)
        if loaded and (editor.importer.running or editor.background_save.future is not None):return 0
        if loaded:editor.title_inspector.flush()
        library=editor.library.clone() if loaded else load_library(str(path))
        count=remove_missing(library)
        if count:
            if loaded:
                frame=editor.preview.frame
                editor.replace_library(library,str(path))
                editor._asset_paths={a.path for a in library.assets.values()}
                if not editor.save(False):raise ValueError('削除済み素材の一覧を保存できません。')
                editor.preview.seek(min(frame,editor.sequence.end));editor.persist_workspace()
            else:
                save_library(library,str(path))
                from cutraft.session import SessionStore
                SessionStore(path.parent/'recovery').write(library,{'path':str(path),'dirty':False,'frame':0})
            self.desk.status.setText(f'見つからない素材 {count}件を一覧と編集内の配置から外しました。')
        return count

    def open_desk(self):self.desk.navigate('home')
    def show_editor(self):return self.desk.navigate('edit')

    def page_changed(self,key):
        editor=self.loaded_editor
        if editor:
            if key!='edit':editor.preview.pause();editor.preview.timer.stop();editor.timeline.cache.suspended=True
            else:editor.preview.timer.start();editor.timeline.cache.suspended=False
        self.setWindowTitle((self.desk.current['title']+' — ' if self.desk.current else '')+'たぬつな Studio')

    def refresh_save_state(self):
        if not hasattr(self,'desk') or not hasattr(self.desk,'save_label'):return
        editor=self.loaded_editor
        text='保存先はこの動画に統一' if self.desk.current else 'ローカルに保存'
        if editor and self.desk.current and editor.path==str(self.workspace.library_path(self.desk.current)):
            text='保存できません' if editor.save_error else '変更を保存中…' if editor.dirty else '保存済み'
        if self.media_jobs.busy:text='素材を準備中…'
        self.desk.save_label.setText(text)
        self.desk.save_button.setEnabled(self.desk.current is not None)

    def save_project(self):
        if self.desk._planning:self.desk._planning.flush()
        if self.loaded_editor and self.loaded_editor.path:
            result=self.loaded_editor.save(False)
            self.refresh_save_state();return result
        self.desk.status.setText('動画は作成時に保存されています。編集内容は自動保存されます。');return True

    def import_recording(self,project,folder,take,show=True):
        if take.get('state')!='ready':raise ValueError('先に収録の整理を完了してください。')
        if project is not self.desk.current and not self.desk.select(project):return False
        editor=self.load_project(project,sync=False)
        if editor is None:return False
        from .take_import import import_take
        result=import_take(editor,self.workspace,project,folder,take)
        self.desk.update_header()
        if show and result:self.show_editor()
        return result

    def open_project_folder(self):
        if self.desk.busy:raise ValueError('収録・素材の準備を完了してから動画を切り替えてください。')
        folder=QFileDialog.getExistingDirectory(self,'動画フォルダを開く',self.workspace.data['root'])
        if folder:
            project=self.workspace.attach(folder)
            self.desk.search.clear();self.desk.select(project);self.desk.navigate('home')

    def choose_media(self):
        self.desk.selected_project()
        files,_=QFileDialog.getOpenFileNames(self,'この動画に素材を追加','','動画・音声・画像 (*.mp4 *.mov *.mkv *.webm *.wav *.mp3 *.m4a *.aac *.flac *.png *.jpg *.jpeg *.webp);;すべて (*)')
        if files:self.import_files(files)

    def import_files(self,paths,audio_role=None,place=True):
        project=self.desk.selected_project()
        if self.desk.busy:raise ValueError('収録・素材の準備を完了してから追加してください。')
        editor=self.load_project(project,sync=False)
        if editor.importer.running:raise ValueError('前の素材の読み込みをお待ちください。')
        from .media_store import collect_media
        self.pending_media={'project':project,'kind':'add','audio_role':audio_role,'place':place}
        self.media_jobs.submit('media',lambda cancel:collect_media(project,paths,cancel))
        self.desk.status.setText('素材をこの動画へコピーしています。同じ内容の素材は一度だけ保存します。')
        self.refresh_save_state()

    def media_finished(self,tag,result,error):
        if error:
            self.desk.status.setText(error);self.pending_media=None;self.refresh_save_state();return
        context=self.pending_media
        if context is None:return
        project=context['project']
        editor=self.load_project(project,sync=False)
        if context['kind']=='consolidate':
            mapping=dict(zip(context['paths'],result))
            editor.commit('素材を動画フォルダへまとめる',lambda:[setattr(a,'path',mapping[a.path]) for a in editor.library.assets.values() if a.path in mapping])
            editor._asset_paths={a.path for a in editor.library.assets.values()}
            editor.save(False);self.pending_media=None;self.desk.refresh_media();self.refresh_save_state();return
        self.pending_media['paths']=result
        missing=[p for p in result if p not in {a.path for a in editor.library.assets.values()}]
        if missing:editor.register_paths(missing)
        else:self.place_imported_media()
        self.show_editor()

    def place_imported_media(self):
        if not self.pending_media or 'paths' not in self.pending_media:return
        editor=self.loaded_editor;paths=set(self.pending_media['paths']);context=self.pending_media
        self.pending_media=None
        from cutraft.model import Clip,uid
        def apply():
            cursor=editor.sequence.end
            insertion_start=cursor
            placed=0
            seen=set()
            for asset in editor.library.assets.values():
                if asset.path not in paths:continue
                if context.get('audio_role') and asset.has_audio:asset.payload['studio_audio_role']=context['audio_role']
                if not context.get('place',True) or asset.id in seen:continue
                seen.add(asset.id)
                duration=max(1,round(asset.duration_ms*editor.sequence.rate/1000)) if asset.kind=='image' else asset.frames(editor.sequence.rate)
                editor.sequence.clips.append(Clip(uid(),asset.id,cursor,0,duration,track=0 if asset.has_video else -1))
                cursor+=duration
                placed+=1
            self._last_import_placement=(placed,insertion_start)
        editor.finish_import_segment()
        editor.commit('BGM候補を保存' if not context.get('place',True) else '素材をそのまま配置',apply)
        editor.save(False)
        if context.get('place',True):
            placed,start=getattr(self,'_last_import_placement',(0,0))
            if placed:
                editor.seek_timeline(start)
                editor.reveal_timeline_frame(start)
        self.desk.status.setText('BGM候補を保存しました。「音量・BGM」を開いて曲を選べます。' if not context.get('place',True) else f'素材を動画内に保存し、{getattr(self,"_last_import_placement",(0,))[0]}件をタイムライン末尾へ追加しました。')
        self.refresh_save_state()

    def consolidate_media(self):
        project=self.desk.selected_project()
        if self.desk.busy:raise ValueError('素材の準備が終わるまでお待ちください。')
        editor=self.load_project(project,sync=False)
        root=Path(project['folder']).resolve()
        paths=list(dict.fromkeys(a.path for a in editor.library.assets.values() if a.kind in ('media','image') and not Path(a.path).resolve().is_relative_to(root)))
        if not paths:self.desk.status.setText('素材はすべてこの動画のフォルダに保存されています。');return
        missing=[p for p in paths if not Path(p).is_file()]
        if missing:raise ValueError('見つからない素材があります。編集画面の再リンクで元のファイルを指定してください。')
        from .media_store import collect_media
        self.pending_media={'project':project,'kind':'consolidate','paths':paths}
        self.media_jobs.submit('media',lambda cancel:collect_media(project,paths,cancel,preserve_order=True));self.refresh_save_state()

    def import_legacy(self,path=None):
        if self.desk.busy:raise ValueError('素材の準備が終わるまでお待ちください。')
        if not path:path,_=QFileDialog.getOpenFileName(self,'以前の編集データを取り込む','','Cut Raft (*.cutraft)')
        if not path:return
        source=Path(path).expanduser().resolve()
        known=next((p for p in self.workspace.data['projects'] if self.workspace.library_path(p).resolve()==source),None)
        if known:self.desk.select(known);self.show_editor();return
        # Remember the source fingerprint: repeated imports reopen the same managed video.
        previous=next((p for p in self.workspace.data['projects'] if p.get('imported_from')==str(source)),None)
        if previous:self.desk.select(previous);self.show_editor();return
        from cutraft.storage import load_library,save_library
        library=load_library(str(source))
        title=source.stem if source.stem!='main' else source.parent.name
        candidate=title;index=2
        while True:
            try:project=self.workspace.create(candidate);break
            except DuplicateProject:candidate=f'{title} ({index})';index+=1
        project['imported_from']=str(source)
        save_library(library,str(self.workspace.library_path(project)));self.workspace.save()
        from .workspace import atomic_text
        atomic_text(Path(project['folder'])/'project.json',json.dumps(project,ensure_ascii=False,indent=2))
        self.desk.search.clear();self.desk.select(project);self.show_editor();self.consolidate_media()

    def show_trash(self):
        from .trash_dialog import TrashDialog
        TrashDialog(self).exec()

    def guard_deletion(self):
        if self.desk.busy:raise ValueError('収録・素材の準備が終わるまで削除・復元はできません。')
        if not self.save_before_switch():raise ValueError('変更を保存できなかったため、データは変更していません。')
        if self.desk._planning:self.desk._planning.flush()

    def unload_editor(self):
        editor=self.loaded_editor
        if editor:
            if not editor.close():raise ValueError('編集内容を保存できませんでした。')
            self.desk.stack.removeWidget(editor);self.desk.pages.pop('edit',None)
            editor.deleteLater();self.loaded_editor=None;self.loaded_project=None

    def delete_project(self,project):
        return self.delete_projects([project])

    def delete_projects(self,projects):
        self.guard_deletion()
        projects=list({project['id']:project for project in projects}.values())
        if not projects:raise ValueError('作品一覧で削除する作品を選択してください。')
        project_ids={project['id'] for project in projects}
        if self.loaded_project and self.loaded_project['id'] in project_ids:self.unload_editor()
        panel=self.desk._recording_panel
        if panel and panel.project and panel.project['id'] in project_ids:
            panel.folder=panel.take=panel.project=None
        tickets=[];failure=None
        for project in projects:
            try:tickets.append(self.data_trash.delete_project(project))
            except Exception as exc:
                failure=exc;break
        if self.desk._planning and self.desk._planning.project and self.desk._planning.project['id'] in project_ids:
            self.desk._planning.project=None;self.desk._planning.dirty=False
        if self.desk.current and self.desk.current['id'] in {p['id'] for p in projects[:len(tickets)]}:self.desk.current=None
        self.desk.refresh_projects();self.desk.navigate('library')
        available=self.workspace.available_projects()
        if available:self.desk.select(available[0])
        self.desk.update_header();self.desk.status.setText('動画をゴミ箱へ移しました。左下のゴミ箱から戻せます。')
        if failure:raise RuntimeError(f'{len(tickets)}件をゴミ箱へ移した後、残りの削除に失敗しました：{failure}')
        self.desk.status.setText(f'{len(tickets)}件の作品をゴミ箱へ移しました。作品一覧にいます。ゴミ箱から戻せます。')
        return tickets[0] if len(tickets)==1 else tickets

    def refresh_deleted_data(self,project):
        editor=self.loaded_editor
        if editor and self.loaded_project and self.loaded_project['id']==project['id']:
            from cutraft.storage import load_library
            frame=editor.preview.frame
            editor.replace_library(load_library(str(self.workspace.library_path(project))),str(self.workspace.library_path(project)))
            editor._asset_paths={a.path for a in editor.library.assets.values()}
            editor.preview.seek(min(frame,editor.sequence.end))
            editor.persist_workspace()
        if self.desk._recording_panel:self.desk._recording_panel.refresh()
        if self.desk.current:self.desk.refresh_media()
        self.desk.update_header();self.page_changed(self.desk.current_page)

    def delete_data(self,paths=(),asset_ids=(),take_folders=(),sequence_id=None,event_id=None,label='選択した素材'):
        self.guard_deletion();project=self.desk.selected_project()
        editor=self.loaded_editor
        if editor:
            from cutraft.model import Library
            editor.preview.pause();editor.preview.reset(Library());editor.timeline.cache.suspended=True
        try:
            ticket=self.data_trash.delete_data(project,paths,asset_ids,take_folders,sequence_id,event_id,label)
        finally:self.refresh_deleted_data(project)
        self.desk.status.setText('削除してゴミ箱へ移しました。編集内の配置も外しています。ゴミ箱から戻せます。')
        return ticket

    def restore_data(self,ticket):
        self.guard_deletion()
        project_id=self.data_trash.restore(ticket)
        project=next((p for p in self.workspace.data['projects'] if p['id']==project_id),None)
        self.desk.refresh_projects()
        if project:
            if self.desk.current is None:self.desk.select(project)
            self.refresh_deleted_data(project)
        self.desk.status.setText('ゴミ箱から元の場所へ戻しました。')
        return project

    def _start_update_worker(self,action,release=None,package=None):
        if self.update_worker and self.update_worker.isRunning():return
        self.desk.update_check_button.setEnabled(False)
        self.desk.update_download_button.setEnabled(False)
        self.desk.update_install_button.setEnabled(False)
        labels={'check':'GitHubで最新版を確認しています…','download':'更新ファイルを取得・検証しています…','install':'アプリを安全に入れ替える準備をしています…'}
        self.desk.update_status.setText(labels[action])
        if action in ('download','install'):
            self.desk.update_progress.setValue(0);self.desk.update_progress.show()
        else:self.desk.update_progress.hide()
        worker=UpdateWorker(action,release,package)
        worker.progress.connect(self._update_progress)
        worker.completed.connect(lambda result,a=action:self._update_finished(a,result))
        self.update_worker=worker;worker.start()

    def _update_progress(self,value,message):
        self.desk.update_progress.setValue(value)
        self.desk.update_status.setText(message)

    def check_for_updates(self):
        self._start_update_worker('check')

    def download_update(self):
        if self.update_release:
            self._start_update_worker('download',self.update_release)

    def install_update(self):
        if not self.update_package:return
        if self.desk.busy:
            self.desk.update_status.setText('録画・配信・素材処理・編集の保存が終わってから更新できます。')
            return
        if not self.save_before_switch() or not self.save_project():
            self.desk.update_status.setText('編集内容を保存できなかったため、更新を中止しました。')
            return
        self._start_update_worker('install',self.update_release,self.update_package)

    def _update_finished(self,action,result):
        self.desk.update_check_button.setEnabled(True)
        if result.get('error'):
            self.desk.update_status.setText('更新できませんでした。現在のアプリはそのままです。 '+result['error'])
            self.desk.update_download_button.setEnabled(bool(self.update_release and self.update_release.get('available') and not self.update_release.get('unsupported')))
            self.desk.update_install_button.setEnabled(bool(self.update_package and self.update_package.is_file()))
        elif action=='check':
            self.desk.update_progress.hide()
            if result.get('available'):
                self.update_release=result
                self.desk.update_status.setText(f"新しい版 {result['version']} が見つかりました。配布ファイルを確認してから入れ替えます。")
                if result.get('unsupported'):
                    self.desk.update_status.setText(f"新しい版 {result['version']} があります。Releaseページからお使いの起動方式に合う更新を取得してください。")
                    self.desk.update_download_button.setEnabled(False)
                else:self.desk.update_download_button.setEnabled(True)
            else:
                self.update_release=None
                self.desk.update_status.setText(f"最新版です（{result.get('version','')}）。")
        elif action=='download':
            self.update_package=Path(result['package'])
            self.desk.update_progress.setValue(100)
            self.desk.update_status.setText(f"{result['version']} をダウンロードし、サイズ・SHA-256・ZIP構成を確認しました。再起動して適用できます。")
            self.desk.update_download_button.setEnabled(False)
            self.desk.update_install_button.setEnabled(True)
        elif action=='install' and result.get('restarting'):
            self.desk.update_progress.setValue(100)
            self.desk.update_status.setText('更新を適用して再起動します。起動確認ができない場合は自動で前の版へ戻します。')
            QTimer.singleShot(250,self.close)

    def closeEvent(self,event):
        if self.desk._live and not self.desk._live.can_close():
            self.desk.navigate('live');event.ignore();return
        if self.desk._recording_panel and not self.desk._recording_panel.can_close():
            self.desk.navigate('record');event.ignore();return
        if not self.save_before_switch():event.ignore();return
        self.media_jobs.shutdown()
        if self.loaded_editor and not self.loaded_editor.close():event.ignore();return
        self.desk.shutdown();event.accept()


from .takes import takes_for


def main():
    from .avatar_gl import configure_opengl
    configure_opengl()
    app=QApplication(sys.argv)
    app.setApplicationName('Tanutsuna Studio');app.setOrganizationName('TanutsunaStudio')
    splash_image=QPixmap(520,180);splash_image.fill(QColor('#171b24'))
    painter=QPainter(splash_image);painter.setPen(QColor('#f1f4fa'))
    painter.drawText(splash_image.rect(),Qt.AlignmentFlag.AlignCenter,'たぬつな Studio\n起動しています…')
    painter.end();splash=QSplashScreen(splash_image);splash.show();app.processEvents()
    root=Path(os.environ.get('TANUTSUNA_TEST_HOME') or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    root.mkdir(parents=True,exist_ok=True)
    default=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))/'TanutsunaStudio'
    try:
        workspace=Workspace(root/'settings.json',default)
        window=StudioWindow(workspace,root)
    except Exception as exc:
        splash.close()
        import traceback
        (root/'startup-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        if os.environ.get('TANUTSUNA_DIAGNOSTIC'):return 1
        QMessageBox.critical(None,'起動できません',str(exc));return 1
    window.show()
    splash.finish(window)
    update_token=None
    if '--update-token' in sys.argv:
        try:update_token=sys.argv[sys.argv.index('--update-token')+1]
        except IndexError:update_token=None
    if update_token:
        def confirm_updated_startup():
            try:
                marker=root/'update-boot.json'
                marker.write_text(json.dumps({'token':update_token,'version':STUDIO_VERSION}),encoding='utf-8')
            except OSError:pass
        QTimer.singleShot(3000,confirm_updated_startup)
    QTimer.singleShot(2500,window.check_for_updates)
    if os.environ.get('TANUTSUNA_DIAGNOSTIC')=='vtuber':
        from .render_diagnostic import schedule_vtuber_check
        schedule_vtuber_check(app,window,root)
    elif os.environ.get('TANUTSUNA_DIAGNOSTIC')=='live':
        try:
            window.desk.navigate('live')
            from .live_av import av
            panel=window.desk._live
            assert window.desk.current_page=='live' and not panel.record.isChecked()
            (root/'live-startup-ok.json').write_text(json.dumps({'page':'live','avVersion':av.__version__,'recordingDefault':panel.record.isChecked()}),encoding='utf-8')
        except Exception:
            import traceback
            (root/'startup-error.log').write_text(traceback.format_exc(),encoding='utf-8');return 1
        QTimer.singleShot(300,window.close)
    elif os.environ.get('TANUTSUNA_DIAGNOSTIC')=='editor':
        from .render_diagnostic import schedule_editor_check
        schedule_editor_check(app,window,root)
    elif os.environ.get('TANUTSUNA_DIAGNOSTIC'):
        app.processEvents();(root/'startup-ok.txt').write_text('StudioWindow created',encoding='utf-8')
        QTimer.singleShot(100,app.quit)
    return app.exec()


if __name__=='__main__':sys.exit(main())
