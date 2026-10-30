"""Bounded real-time mixer, shared capture recording and independent reconnecting publisher."""
from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit
import copy
import json
import io
import os
import queue
import sys
import threading
import time
import wave
import numpy as np
from PySide6.QtCore import QObject,Signal
from PySide6.QtGui import QImage
from .live_av import av
from .takes import create_take,write_take,publish_take

RATE=48000
FPS=60
ROLES=('desktop','voice','bgm','sfx')


def stream_settings(server,key=''):
    parsed=urlsplit(server.strip())
    hosts={'a.rtmp.youtube.com':'a.rtmp.youtube.com','b.rtmp.youtube.com':'b.rtmp.youtube.com',
           'a.rtmps.youtube.com':'a.rtmps.youtube.com','b.rtmps.youtube.com':'b.rtmps.youtube.com'}
    if parsed.scheme not in ('rtmp','rtmps') or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('YouTube Studioの「ストリームURL」を入力してください。動画の視聴URLは使用できません。')
    try:
        allowed_ports=(None,1935) if parsed.scheme=='rtmp' else (None,443)
        if parsed.port not in allowed_ports:raise ValueError()
    except ValueError:raise ValueError('YouTubeの標準のストリームURLを入力してください。')
    parts=parsed.path.strip('/').split('/')
    if parts[0]!='live2' or len(parts)>2:raise ValueError('配信先はYouTubeの /live2 を使用してください。')
    embedded=parts[1] if len(parts)==2 else ''
    key=key.strip()
    if embedded and key and key!=embedded:raise ValueError('URLに含まれるキーと入力したキーが異なります。')
    key=key or embedded
    if not key or any(ord(c)<33 or c in '/?#' for c in key):raise ValueError('ストリームURLに加え、YouTube Studioのストリームキーを入力してください。')
    port=1935 if parsed.scheme=='rtmp' else 443
    return f'{parsed.scheme}://{hosts[parsed.hostname]}:{port}/live2',key


def destination(server,key):
    server,key=stream_settings(server,key)
    return server+'/'+key


class AudioRing:
    """At most 250ms of live input; underflow is silence, not a stalled video clock."""
    def __init__(self):self.lock=threading.Lock();self.data=np.zeros((0,2),np.float32);self.dropped=0;self.peak=0.
    def push(self,data):
        data=np.nan_to_num(np.asarray(data,np.float32),nan=0.,posinf=1.,neginf=-1.).reshape(-1,2)
        with self.lock:
            self.peak=float(np.max(np.abs(data))) if len(data) else 0.
            self.data=np.concatenate((self.data,data))
            excess=max(0,len(self.data)-RATE//4)
            self.dropped+=excess
            if excess:self.data=self.data[excess:]
    def read(self,n):
        with self.lock:
            count=min(n,len(self.data));out=np.zeros((n,2),np.float32)
            out[:count]=self.data[:count];self.data=self.data[count:];return out
    def clear(self):
        with self.lock:self.data=np.zeros((0,2),np.float32);self.peak=0.;self.dropped=0


class FileAudio:
    """Incremental, bounded MP3/WAV decode, no speaker output and no feedback loop."""
    def __init__(self):self.lock=threading.Lock();self.path=None;self.repeat=False;self.ring=AudioRing();self.stop=threading.Event();self.thread=None;self.error=''
    def play(self,path,repeat=False):
        self.close();self.stop=threading.Event();self.path=str(path);self.repeat=repeat;self.error=''
        self.thread=threading.Thread(target=self._decode,args=(self.path,repeat,self.stop),daemon=True);self.thread.start()
    def _decode(self,path,repeat,stop):
        try:
            while not stop.is_set():
                with av.open(path) as container:
                    read_samples=False
                    resampler=av.AudioResampler(format='fltp',layout='stereo',rate=RATE)
                    for frame in container.decode(audio=0):
                        if stop.is_set():return
                        for audio in resampler.resample(frame):
                            data=audio.to_ndarray().T.copy();offset=0;read_samples=True
                            while offset<len(data) and not stop.is_set():
                                with self.ring.lock:room=RATE//5-len(self.ring.data)
                                if room<=0:stop.wait(.01);continue
                                count=min(room,len(data)-offset);self.ring.push(data[offset:offset+count]);offset+=count
                if not read_samples:raise ValueError('empty audio')
                if not repeat:return
        except Exception:
            if self.stop is stop and not stop.is_set():
                self.path=None;self.error='音声ファイルを再生できません。別のファイルを選んでください。'
    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=2)
        self.thread=None;self.ring.clear();self.path=None


def video_codec(width,height):
    names=['h264_videotoolbox'] if sys.platform=='darwin' else ['h264_nvenc','h264_qsv','h264_amf']
    for name in names+['libx264']:
        try:
            codec=av.CodecContext.create(name,'w');codec.width=width;codec.height=height
            codec.pix_fmt='nv12' if name in ('h264_qsv','h264_amf') else 'yuv420p';codec.time_base=Fraction(1,FPS);codec.framerate=Fraction(FPS,1)
            codec.bit_rate=12_000_000 if width==1920 else 6_000_000;codec.gop_size=FPS*2;codec.max_b_frames=0
            codec.thread_count=2;codec.flags|=av.codec.context.Flags.global_header
            codec.options={'preset':'ultrafast','tune':'zerolatency'} if name=='libx264' else {}
            codec.open();return codec
        except Exception:continue
    raise RuntimeError('配信用のH.264エンコーダーを開始できません。')


@dataclass
class PacketData:
    kind:str
    data:bytes
    pts:int
    dts:int
    duration:int
    time_base:Fraction
    key:bool
    extra:bytes


def packet_data(kind,packet,codec):
    return PacketData(kind,bytes(packet),packet.pts,packet.dts,packet.duration,packet.time_base,packet.is_keyframe,bytes(codec.extradata or b''))


class PacketWriter:
    def __init__(self,url,width,height,templates,timeout=15):
        # RTMPS needs TCP + TLS + RTMP handshakes. A 3s deadline caused
        # otherwise healthy but slower links to enter a reconnect loop.
        self.container=av.open(url,'w',format='flv',options={'rw_timeout':str(int(timeout*1000000))},timeout=(10,timeout))
        self.video=self.container.add_stream_from_template(templates[0],opaque=True)
        self.audio=self.container.add_stream_from_template(templates[1],opaque=True)
        self.started=False;self.origin=None
    def write(self,item):
        if not self.started:
            if item.kind!='video' or not item.key:return False
            self.origin=float(item.dts*item.time_base);self.started=True
        # Drop pre-roll before the first video keyframe when reconnecting.
        delta=round(self.origin/item.time_base)
        if item.dts<delta:return False
        packet=av.Packet(item.data);packet.pts=item.pts-delta;packet.dts=item.dts-delta
        packet.duration=item.duration;packet.time_base=item.time_base;packet.is_keyframe=item.key
        packet.stream=self.video if item.kind=='video' else self.audio
        self.container.mux(packet);return True
    def close(self):self.container.close()


class Publisher:
    def __init__(self,url,width,height,notify,templates):
        self.url=url;self.width=width;self.height=height;self.notify=notify;self.templates=templates
        self.queue=queue.Queue(maxsize=90);self.stop=threading.Event();self.thread=threading.Thread(target=self.run,daemon=True)
    def start(self):self.thread.start()
    def offer(self,item):
        if self.stop.is_set():return
        try:self.queue.put_nowait(item)
        except queue.Full:
            try:self.queue.get_nowait()
            except queue.Empty:pass
            try:self.queue.put_nowait(item)
            except queue.Full:pass
    def safe_error(self,exc):
        detail=f'{type(exc).__name__}: {exc}'.replace(self.url,'[配信URL]')
        secret=self.url.rsplit('/',1)[-1]
        if secret:detail=detail.replace(secret,'[キー]')
        return detail[:240]
    def run(self):
        writer=None;attempt=0
        try:
            while not self.stop.is_set():
                if writer is None:
                    self.notify('connecting' if not attempt else 'reconnecting')
                    try:writer=PacketWriter(self.url,self.width,self.height,self.templates)
                    except Exception as exc:
                        self.notify('connection_error',self.safe_error(exc))
                        attempt+=1
                        if self.stop.wait(min(10,2**min(attempt,3))):break
                        continue
                try:item=self.queue.get(timeout=.15)
                except queue.Empty:continue
                try:
                    was=writer.started;sent=writer.write(item)
                    if sent and not was:self.notify('live');attempt=0
                except Exception as exc:
                    detail=self.safe_error(exc)
                    try:writer.close()
                    except Exception:pass
                    writer=None;attempt+=1
                    # Do not send a backlog of old frames after the connection recovers.
                    while not self.queue.empty():
                        try:self.queue.get_nowait()
                        except queue.Empty:break
                    self.notify('reconnecting',detail)
                    if self.stop.wait(min(10,2**min(attempt,3))):break
        finally:
            if writer:
                try:writer.close()
                except Exception:pass
            self.notify('stopped')
    def close(self):self.stop.set()


class Archive:
    def __init__(self,project,width,height,avatar):
        self.folder,self.take=create_take(project,{'desktop':True,'voice':True,'avatar':avatar,'transcribe':False,'live':True})
        self.take['options'].update(bgm=True,sfx=True)
        self.take.update(width=width,height=height,fps=FPS,state='recording',timestamps={'screen':0,'desktop':0,'voice':0,'bgm':0,'sfx':0})
        self.take['avatar_transform']={};write_take(self.folder,self.take)
        self.output=av.open(str(self.folder/'screen.mp4'),'w',options={'movflags':'frag_keyframe+empty_moov+default_base_moof'})
        selected=video_codec(width,height);chosen=selected.name;pixel_format=selected.pix_fmt;selected=None
        self.video=self.output.add_stream(chosen,rate=FPS)
        self.video.width=width;self.video.height=height;self.video.pix_fmt=pixel_format;self.video.bit_rate=12_000_000 if width==1920 else 6_000_000;self.video.codec_context.thread_count=2
        if chosen=='libx264':self.video.options={'preset':'ultrafast','tune':'zerolatency'}
        self.waves={}
        for role in ROLES:
            stream=wave.open(str(self.folder/(role+'-source.wav')),'wb');stream.setnchannels(2);stream.setsampwidth(2);stream.setframerate(RATE);self.waves[role]=stream
        self.motion=(self.folder/'vtuber-motion.jsonl').open('w',encoding='utf-8') if avatar else None

        self.frames=0
    def write(self,image,audio,pose,transform):
        frame=image_frame(image);frame.pts=self.frames;frame.time_base=Fraction(1,FPS)
        for packet in self.video.encode(frame):self.output.mux(packet)
        for role,stream in self.waves.items():stream.writeframesraw((np.clip(audio[role],-1,1)*32767).astype('<i2').tobytes())
        if self.motion:
            if self.frames==0:self.motion.write(json.dumps({'orbit':transform.get('orbit',0)})+'\n')
            self.motion.write(json.dumps({'time':self.frames/FPS,'pose':pose})+'\n')
        self.take['avatar_transform']=dict(transform);self.frames+=1
        if self.frames%(FPS*2)==0:
            self.take['duration']=self.frames/FPS;write_take(self.folder,self.take)
            if self.motion:self.motion.flush()
    def close(self,error=''):
        try:
            for packet in self.video.encode():self.output.mux(packet)
            self.output.close()
        finally:
            for stream in self.waves.values():stream.close()
            if self.motion:self.motion.close()
            self.take.update(duration=self.frames/FPS,state='incomplete' if error else 'captured')
            if error:self.take['warnings'].append(error)
            write_take(self.folder,self.take)
        return self.folder,self.take


def image_frame(image):
    image=image.convertToFormat(QImage.Format.Format_RGB888)
    data=np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*3].reshape(image.height(),image.width(),3)
    return av.VideoFrame.from_ndarray(data,format='rgb24')


class LiveEngine(QObject):
    state=Signal(str)
    connection_detail=Signal(str)
    levels=Signal(object)
    finished=Signal(object,object,str)
    def __init__(self,parent=None):
        super().__init__(parent);self.lock=threading.Lock();self.latest=None;self.stop_event=threading.Event();self.thread=None;self.publisher=None;self.generation=0
        self.inputs={key:AudioRing() for key in ('desktop','voice')};self.files={key:FileAudio() for key in ('bgm','sfx')}
        self.gains={key:1. for key in ROLES};self.mutes={key:False for key in ROLES};self.active=False;self.started_at=0
    def submit_frame(self,program,screen,pose,transform):
        with self.lock:self.latest=(program,screen,dict(pose),dict(transform))
    def set_mix(self,role,gain,muted):
        with self.lock:self.gains[role]=gain;self.mutes[role]=muted
    def start(self,url,width,height,project=None,avatar=True):
        if self.active:raise ValueError('配信は開始済みです。')
        self.stop_event.clear();self.generation+=1;self.active=True;self.started_at=time.monotonic()
        for source in self.inputs.values():source.clear()
        self.thread=threading.Thread(target=self.run,args=(url,width,height,project,avatar,self.generation),daemon=True);self.thread.start()
    def run(self,url,width,height,project,avatar,generation):
        archive=None;error='';folder=take=None
        try:
            selected=video_codec(width,height)
            template=av.open(io.BytesIO(),'w',format='matroska')
            vs=template.add_stream(selected.name,rate=FPS)
            vs.width=width;vs.height=height;vs.pix_fmt=selected.pix_fmt;vs.bit_rate=selected.bit_rate
            vs.codec_context.time_base=Fraction(1,FPS);vs.codec_context.gop_size=FPS*2;vs.codec_context.max_b_frames=0;vs.codec_context.thread_count=2
            vs.options={'preset':'ultrafast','tune':'zerolatency'} if selected.name=='libx264' else {}
            selected=None
            video=vs.codec_context
            aus=template.add_stream('aac',rate=44100);audio=aus.codec_context;audio.sample_rate=44100;audio.layout='stereo';audio.format='fltp';audio.bit_rate=128000;audio.time_base=Fraction(1,44100);audio.flags|=av.codec.context.Flags.global_header;audio.open()
            template.start_encoding()
            resampler=av.AudioResampler(format='fltp',layout='stereo',rate=44100,frame_size=1024)
            if project:archive=Archive(project,width,height,avatar)
            self.publisher=Publisher(url,width,height,lambda state,detail='':self.report_state(state,generation,detail),(vs,aus));self.publisher.start()
            index=0;epoch=time.monotonic();last_overload_notice=0.;overload_until=0.;overload_notified=False
            while not self.stop_event.is_set():
                wait=epoch+index/FPS-time.monotonic()
                if wait>0 and self.stop_event.wait(wait):break
                with self.lock:latest=self.latest;gains=dict(self.gains);mutes=dict(self.mutes)
                if latest is None:self.stop_event.wait(.01);epoch=time.monotonic();continue
                program,screen,pose,transform=latest
                raw={**{k:s.read(RATE//FPS) for k,s in self.inputs.items()},**{k:s.ring.read(RATE//FPS) for k,s in self.files.items()}}
                adjusted={k:(np.zeros_like(v) if mutes[k] else v*gains[k]) for k,v in raw.items()}
                mix=np.clip(sum(adjusted.values()),-1,1).astype(np.float32)
                if index%6==0:self.levels.emit({k:float(np.max(np.abs(v))) for k,v in adjusted.items()})
                if archive:
                    archive.take['live_mix']={k:{'volume':gains[k],'muted':mutes[k]} for k in ROLES}
                    archive.write(screen,raw,pose,transform)
                frame=image_frame(program);frame.pts=index;frame.time_base=Fraction(1,FPS)
                for packet in video.encode(frame):self.publisher.offer(packet_data('video',packet,video))
                af=av.AudioFrame.from_ndarray(mix.T.copy(),format='fltp',layout='stereo');af.sample_rate=RATE;af.pts=index*(RATE//FPS);af.time_base=Fraction(1,RATE)
                for converted in resampler.resample(af):
                    for packet in audio.encode(converted):self.publisher.offer(packet_data('audio',packet,audio))
                index+=1
                # Keep a real-time clock, but never build a multi-second frame queue.
                now=time.monotonic()
                if now-epoch-index/FPS>.25:
                    # A slow encoder can miss every frame deadline. Emitting a
                    # queued Qt event each time floods the UI thread and makes
                    # text entry lag further; report the condition at most once
                    # every five seconds instead.
                    overload_until=now+3.
                    if not overload_notified or now-last_overload_notice>=5.:
                        self.state.emit('overload');last_overload_notice=now;overload_notified=True
                    epoch=now-index/FPS
                elif overload_notified and now>overload_until:
                    self.state.emit('recovered');overload_notified=False
        except Exception as exc:
            self.last_error=(type(exc).__name__+': '+str(exc)).replace(url,'[配信先]').replace(url.rsplit('/',1)[-1],'[キー]')
            error='配信エンジンを継続できませんでした。保存済みの録画素材は保持しています。軽量設定・空き容量・入力機器を確認してください。'
        finally:
            if self.publisher:self.publisher.close()
            if archive:
                archive.take['stats']={role:{'dropped':source.dropped} for role,source in self.inputs.items()}
                try:folder,take=archive.close(error)
                except Exception:error='録画の終了処理に失敗しました。収録フォルダ内の元素材を保持しています。';folder,take=archive.folder,archive.take
            self.active=False;self.finished.emit(folder,take,error)
    def report_state(self,state,generation,detail=''):
        if generation!=self.generation or self.stop_event.is_set():return
        try:
            if detail:self.connection_detail.emit(detail)
            self.state.emit(state)
        except RuntimeError:pass

    def stop(self):
        self.stop_event.set()
        if self.publisher:self.publisher.close()
    def shutdown(self):
        self.stop()
        for source in self.files.values():source.close()
