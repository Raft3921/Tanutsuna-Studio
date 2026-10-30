import Foundation
import AppKit
import ScreenCaptureKit
import CoreMedia
import CoreVideo

@available(macOS 15.0, *)
final class WindowSource:NSObject,SCStreamOutput,SCStreamDelegate,@unchecked Sendable {
    var stream:SCStream?
    let lock=NSLock()
    var latest:CVPixelBuffer?
    var stopping=false
    let width:Int,height:Int
    init(width:Int,height:Int){self.width=width;self.height=height}
    func stream(_ stream:SCStream,didStopWithError error:Error){exit(2)}
    func stream(_ stream:SCStream,didOutputSampleBuffer sample:CMSampleBuffer,of type:SCStreamOutputType){
        guard type == .screen,let pixel=CMSampleBufferGetImageBuffer(sample) else{return}
        if let attachments=CMSampleBufferGetSampleAttachmentsArray(sample,createIfNecessary:false) as? [[SCStreamFrameInfo:Any]],
           let status=attachments.first?[.status] as? Int,status != SCFrameStatus.complete.rawValue{return}
        lock.lock();latest=pixel;lock.unlock()
    }
    func start(window:SCWindow) async throws {
        let config=SCStreamConfiguration();config.width=width;config.height=height
        config.minimumFrameInterval=CMTime(value:1,timescale:60);config.queueDepth=3
        config.pixelFormat=kCVPixelFormatType_32BGRA;config.scalesToFit=true;config.showsCursor=true;config.capturesAudio=false
        stream=SCStream(filter:SCContentFilter(desktopIndependentWindow:window),configuration:config,delegate:self)
        try stream!.addStreamOutput(self,type:.screen,sampleHandlerQueue:DispatchQueue(label:"tanutsuna.window.frames"))
        try await stream!.startCapture()
        FileHandle.standardOutput.write(Data("\(width) \(height) BGRA\n".utf8))
        DispatchQueue.global(qos:.userInitiated).async { self.writeFrames() }
    }
    func writeFrames(){
        // One retained latest surface and one pipe write; never queue raw frames.
        while true {
            lock.lock();let stop=stopping;let pixel=latest;latest=nil;lock.unlock()
            if stop{return}
            if let buffer=pixel {
                CVPixelBufferLockBaseAddress(buffer,.readOnly)
                if let base=CVPixelBufferGetBaseAddress(buffer),CVPixelBufferGetWidth(buffer)==width,CVPixelBufferGetHeight(buffer)==height {
                    let stride=CVPixelBufferGetBytesPerRow(buffer)
                    var bytes=Data(count:width*height*4)
                    bytes.withUnsafeMutableBytes { destination in
                        for row in 0..<height { memcpy(destination.baseAddress!.advanced(by:row*width*4),base.advanced(by:row*stride),width*4) }
                    }
                    CVPixelBufferUnlockBaseAddress(buffer,.readOnly)
                    FileHandle.standardOutput.write(bytes)
                }else{CVPixelBufferUnlockBaseAddress(buffer,.readOnly)}
            }
            Thread.sleep(forTimeInterval:1.0/60)
        }
    }
    func stop()async{
        lock.withLock { stopping=true }
        try? await stream?.stopCapture()
    }
}
@main struct Main {
    static func main()async{
        guard #available(macOS 15.0,*)else{exit(1)}
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        do{
            let args=CommandLine.arguments
            let content=try await SCShareableContent.excludingDesktopWindows(false,onScreenWindowsOnly:false)
            if args.contains("--list"){
                let rows=content.windows.filter{$0.frame.width>=1 && $0.frame.height>=1 && ($0.windowLayer==0 || ($0.owningApplication?.applicationName.lowercased().contains("java") ?? false) || ($0.title?.lowercased().contains("minecraft") ?? false)) && $0.owningApplication?.processID != ProcessInfo.processInfo.processIdentifier}.map { window -> [String:Any] in
                    ["id":Int(window.windowID),"pid":Int(window.owningApplication?.processID ?? 0),"app":window.owningApplication?.applicationName ?? "アプリ","title":window.title ?? "","width":Int(window.frame.width),"height":Int(window.frame.height)]
                }
                FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:rows));return
            }
            guard args.count==5,args[1]=="--capture",let id=UInt32(args[2]),let width=Int(args[3]),let height=Int(args[4]),[1280,1920].contains(width),[720,1080].contains(height),let window=content.windows.first(where:{$0.windowID==id}) else{exit(3)}
            let capture=WindowSource(width:width,height:height);try await capture.start(window:window)
            await withCheckedContinuation{(continuation:CheckedContinuation<Void,Never>)in DispatchQueue.global().async{_=readLine();continuation.resume()}}
            await capture.stop()
        }catch{FileHandle.standardError.write(Data("ウィンドウを取得できません。画面収録の許可とゲームの起動状態を確認してください。\n".utf8));exit(1)}
    }
}
