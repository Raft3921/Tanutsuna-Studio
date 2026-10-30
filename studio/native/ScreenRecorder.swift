// macOS 15+: ScreenCaptureKit + VideoToolbox; three independent media files.
import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreMedia
import CoreGraphics
import CoreVideo

func emit(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value), let line = String(data: data, encoding: .utf8) {
        print(line); fflush(stdout)
    }
}
final class Track {
    let writer: AVAssetWriter
    let input: AVAssetWriterInput
    let isVideo: Bool
    var adaptor: AVAssetWriterInputPixelBufferAdaptor?
    var latest: CVPixelBuffer?
    var epoch: UInt64 = 0
    var first: CMTime? = nil
    var frames = 0
    var dropped = 0
    init(url: URL, video: Bool) throws {
        isVideo = video
        writer = try AVAssetWriter(outputURL: url, fileType: video ? .mp4 : .m4a)
        var settings: [String: Any]
        if video {
            settings = [AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: 1920, AVVideoHeightKey: 1080,
                AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 10_000_000, AVVideoExpectedSourceFrameRateKey: 50,
                    AVVideoMaxKeyFrameIntervalKey: 100, AVVideoAllowFrameReorderingKey: false],
                AVVideoEncoderSpecificationKey: ["EnableHardwareAcceleratedVideoEncoder": true]]
        } else {
            settings = [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 48000, AVNumberOfChannelsKey: 2, AVEncoderBitRateKey: 192000]
        }
        input = AVAssetWriterInput(mediaType: video ? .video : .audio, outputSettings: settings)
        input.expectsMediaDataInRealTime = true
        guard writer.canAdd(input) else { throw NSError(domain: "Recorder", code: 1, userInfo: [NSLocalizedDescriptionKey: "エンコーダーを初期化できません"] ) }
        writer.add(input)
        if video { writer.movieFragmentInterval = CMTime(value: 2, timescale: 1) }
        if video { adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: nil) }
    }
    func append(_ sample: CMSampleBuffer, role: String) {
        guard sample.isValid, CMSampleBufferDataIsReady(sample) else { return }
        let pts = CMSampleBufferGetPresentationTimeStamp(sample)
        if first == nil {
            guard writer.startWriting() else { return }
            writer.startSession(atSourceTime: isVideo ? .zero : pts); first = pts
            epoch = DispatchTime.now().uptimeNanoseconds
            emit(["type": "track", "role": role, "pts": pts.seconds])
        }
        if isVideo { latest = CMSampleBufferGetImageBuffer(sample); return }
        if input.isReadyForMoreMediaData {
            if input.append(sample) { frames += 1 }
        } else { dropped += 1 }
    }
    func videoTick() {
        guard isVideo, first != nil, let pixel = latest, writer.status == .writing else { return }
        let elapsed = Double(DispatchTime.now().uptimeNanoseconds - epoch) / 1_000_000_000
        let target = Int(elapsed * 50)
        // Reuse the last GPU surface when the desktop does not change. Constant 50fps,
        // with only a bounded catch-up burst when a scheduling interval was missed.
        var count = 0
        while frames <= target && count < 5 && input.isReadyForMoreMediaData {
            if adaptor!.append(pixel, withPresentationTime: CMTime(value: Int64(frames), timescale: 50)) {
                frames += 1; count += 1
            } else { break }
        }
        if target - frames > 5 { dropped += 1 }
    }
    func finish() async throws {
        if first == nil { return }
        if writer.status == .failed { throw writer.error! }
        input.markAsFinished()
        await writer.finishWriting()
        if writer.status != .completed { throw writer.error ?? NSError(domain: "Recorder", code: 2) }
    }
}
@available(macOS 15.0, *)
final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    var stream: SCStream?
    var tracks: [SCStreamOutputType: Track] = [:]
    let queue = DispatchQueue(label: "tanutsuna.capture", qos: .userInitiated)
    var failure: Error?
    var timer: DispatchSourceTimer?
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        failure = error
        emit(["type": "error", "message": error.localizedDescription])
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        if type == .screen {
            guard let items = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
                let status = items.first?[.status] as? Int, status == SCFrameStatus.complete.rawValue else { return }
        }
        tracks[type]?.append(sampleBuffer, role: type == .screen ? "screen" : type == .audio ? "desktop" : "voice")
    }
    func start(folder: URL, displayIndex: Int, desktop: Bool, mic: Bool, device: String?, origin: CGPoint?) async throws {
        if mic {
            guard await AVCaptureDevice.requestAccess(for: .audio) else {
                throw NSError(domain: "Recorder", code: 3, userInfo: [NSLocalizedDescriptionKey: "マイクの許可が必要です"])
            }
        }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard displayIndex >= 0 && displayIndex < content.displays.count else { throw NSError(domain: "Recorder", code: 4) }
        let selected = origin == nil ? content.displays[displayIndex] : content.displays.first(where: { abs($0.frame.minX-origin!.x)<1 && abs($0.frame.minY-origin!.y)<1 })
        guard let display = selected else { throw NSError(domain: "Recorder", code: 6, userInfo: [NSLocalizedDescriptionKey:"選択したディスプレイを識別できません"]) }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.width = 1920; config.height = 1080
        config.minimumFrameInterval = CMTime(value: 1, timescale: 50)
        config.queueDepth = 3
        config.pixelFormat = kCVPixelFormatType_32BGRA
        config.showsCursor = true
        config.scalesToFit = true
        config.capturesAudio = desktop
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48000; config.channelCount = 2
        config.captureMicrophone = mic
        if let id = device, !id.isEmpty { config.microphoneCaptureDeviceID = id }
        tracks[.screen] = try Track(url: folder.appendingPathComponent("screen.mp4"), video: true)
        if desktop { tracks[.audio] = try Track(url: folder.appendingPathComponent("desktop-source.m4a"), video: false) }
        if mic { tracks[.microphone] = try Track(url: folder.appendingPathComponent("voice-source.m4a"), video: false) }
        stream = SCStream(filter: filter, configuration: config, delegate: self)
        for type in tracks.keys { try stream!.addStreamOutput(self, type: type, sampleHandlerQueue: queue) }
        timer = DispatchSource.makeTimerSource(queue: queue)
        timer!.schedule(deadline: .now(), repeating: .milliseconds(20), leeway: .milliseconds(1))
        timer!.setEventHandler { [weak self] in self?.tracks[.screen]?.videoTick() }
        timer!.resume()
        try await stream!.startCapture()
        emit(["type": "ready", "width": 1920, "height": 1080, "fps": 50])
    }
    func stop() async throws {
        try await stream?.stopCapture()
        timer?.cancel()
        // Drain callbacks before marking writer inputs finished.
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in queue.async { c.resume() } }
        for (type, track) in tracks {
            try await track.finish()
            emit(["type": "stats", "role": type == .screen ? "screen" : type == .audio ? "desktop" : "voice",
                  "samples": track.frames, "dropped": track.dropped])
        }
        if let error = failure { throw error }
        emit(["type": "done"])
    }
}
@main struct Main {
    static func main() async {
        guard #available(macOS 15.0, *) else { emit(["type":"error","message":"macOS 15以降が必要です"]); exit(1) }
        do {
            let args = CommandLine.arguments
            if args.contains("--list") {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                emit(["type": "devices", "displays": content.displays.enumerated().map { ["index": $0.offset, "id": Int($0.element.displayID), "width": $0.element.width, "height": $0.element.height] }])
                return
            }
            guard args.count >= 5 else { throw NSError(domain: "Recorder", code: 5) }
            let recorder = Recorder()
            try await recorder.start(folder: URL(fileURLWithPath: args[1]), displayIndex: Int(args[2]) ?? 0,
                                     desktop: args[3] == "1", mic: args[4] == "1", device: args.count > 5 ? args[5] : nil, origin: args.count > 7 ? CGPoint(x: Double(args[6]) ?? 0, y: Double(args[7]) ?? 0) : nil)
            // Blocking stdin must not occupy Swift's main executor or the sample queue.
            await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
                DispatchQueue.global(qos: .utility).async { _ = readLine(); c.resume() }
            }
            try await recorder.stop()
        } catch { emit(["type":"error","message":error.localizedDescription]); exit(1) }
    }
}
