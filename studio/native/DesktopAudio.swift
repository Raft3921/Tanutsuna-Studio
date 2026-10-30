import Foundation
import ScreenCaptureKit
import CoreMedia
import AVFoundation

@available(macOS 15.0, *)
final class DesktopAudio: NSObject, SCStreamOutput, SCStreamDelegate {
    var stream: SCStream?
    let queue=DispatchQueue(label:"tanutsuna.live.audio")
    let output=FileHandle.standardOutput
    var announced=false
    func announce() { if !announced { output.write(Data("48000 2 32 float\n".utf8));announced=true } }
    func stream(_ stream: SCStream, didStopWithError error: Error) { exit(2) }
    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, let desc=CMSampleBufferGetFormatDescription(sample) else { return }
        let format=AVAudioFormat(cmAudioFormatDescription:desc)
        let count=CMSampleBufferGetNumSamples(sample)
        guard count>0,let buffer=AVAudioPCMBuffer(pcmFormat:format,frameCapacity:AVAudioFrameCount(count)) else { return }
        buffer.frameLength=AVAudioFrameCount(count)
        guard CMSampleBufferCopyPCMDataIntoAudioBufferList(sample,at:0,frameCount:Int32(count),into:buffer.mutableAudioBufferList)==noErr else { return }
        // ScreenCaptureKit is configured for 48kHz stereo Float32.
        guard let channels=buffer.floatChannelData else { return }
        var interleaved=[Float](repeating:0,count:count*2)
        for index in 0..<count {
            if format.isInterleaved {
                interleaved[index*2]=channels[0][index*Int(format.channelCount)]
                interleaved[index*2+1]=channels[0][index*Int(format.channelCount)+min(1,Int(format.channelCount)-1)]
            } else {
                interleaved[index*2]=channels[0][index]
                interleaved[index*2+1]=channels[min(1,Int(format.channelCount)-1)][index]
            }
        }
        announce()
        interleaved.withUnsafeBytes { bytes in output.write(Data(bytes)) }
    }
    func start() async throws {
        let content=try await SCShareableContent.excludingDesktopWindows(false,onScreenWindowsOnly:true)
        guard let display=content.displays.first else { throw NSError(domain:"LiveAudio",code:1) }
        let config=SCStreamConfiguration();config.width=2;config.height=2
        config.minimumFrameInterval=CMTime(value:1,timescale:1);config.capturesAudio=true
        config.sampleRate=48000;config.channelCount=2;config.excludesCurrentProcessAudio=true
        stream=SCStream(filter:SCContentFilter(display:display,excludingWindows:[]),configuration:config,delegate:self)
        try stream!.addStreamOutput(self,type:.audio,sampleHandlerQueue:queue)
        try await stream!.startCapture()
        queue.sync { self.announce() }
    }
}
@main struct Main {
    static func main() async {
        guard #available(macOS 15.0, *) else { exit(1) }
        do {
            let recorder=DesktopAudio();try await recorder.start()
            await withCheckedContinuation { (c:CheckedContinuation<Void,Never>) in
                DispatchQueue.global().async { _=readLine();c.resume() }
            }
            try await recorder.stream?.stopCapture()
        } catch { FileHandle.standardError.write(Data("PC音を開始できません。画面収録の許可を確認してください。\n".utf8));exit(1) }
    }
}
