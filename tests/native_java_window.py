"""Discover and capture a real Java window; no YouTube transmission."""
from pathlib import Path
import sys,subprocess,time,json,os
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PySide6.QtWidgets import QApplication
from studio.window_source import NativeWindowSource
fixture=ROOT/'build/JavaCaptureFixture.java'
fixture.write_text('''import javax.swing.*;import java.awt.*;
class JavaCaptureFixture { public static void main(String[] args){SwingUtilities.invokeLater(()->{
 JFrame f=new JFrame("Minecraft Java capture fixture");JLabel label=new JLabel("JAVA WINDOW",SwingConstants.CENTER);
 label.setOpaque(true);label.setBackground(new Color(18,120,70));label.setForeground(Color.WHITE);label.setFont(new Font("SansSerif",Font.BOLD,35));
 f.add(label);f.setSize(640,360);f.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);f.setVisible(true);
 new Timer(100,e->label.setText("JAVA "+System.currentTimeMillis())).start();});}}''')
process=subprocess.Popen(['java',str(fixture)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
app=QApplication([]);source=NativeWindowSource();source.helper=Path(os.environ.get('TANUTSUNA_WINDOW_HELPER',source.helper));rows=[];frames=[];errors=[]
source.listed.connect(lambda found:rows.__setitem__(slice(None),found));source.frame.connect(lambda frame:frames.append((frame.width(),frame.height())))
source.failed.connect(errors.append)
try:
 deadline=time.monotonic()+20;chosen=None;next_scan=0
 while time.monotonic()<deadline:
  app.processEvents();time.sleep(.01)
  if chosen is None and time.monotonic()>next_scan:source.enumerate();next_scan=time.monotonic()+.5
  if chosen is None:
   chosen=next((row for row in rows if row.title=='Minecraft Java capture fixture'),None)
   if chosen:source.start(chosen,1280,720)
  if len(frames)>=5:break
 assert chosen is not None,errors
 assert len(frames)>=5,(frames,errors)
 report={'javaWindowFound':True,'frames':len(frames),'frameSize':frames[-1],'errors':errors,'network':'none'}
 (ROOT/'build/native-java-window.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
finally:
 source.stop();process.terminate();process.wait(timeout=5)
 until=time.monotonic()+1
 while time.monotonic()<until:app.processEvents();time.sleep(.01)
