import AVFoundation
import Foundation

/// Records audio from the default microphone with voice activity detection (VAD).
/// When silence is detected after speech, recording stops automatically.
@MainActor
final class AudioRecorder: ObservableObject {

    enum State: Equatable {
        case idle
        case listening       // mic open, waiting for speech
        case recording       // speech detected, capturing
        case processing      // silence detected, finalising audio
    }

    @Published var state: State = .idle
    @Published var audioLevel: Float = 0     // 0–1 normalised RMS for UI
    @Published var error: String?

    /// Set by the recorder when speech ends. Observe with .onChange to handle it.
    @Published var completedAudio: Data?

    var isRecording: Bool { state == .listening || state == .recording }

    // VAD tuning — two thresholds for hysteresis to avoid noise triggering
    private let speechStartThreshold: Float = 0.012  // RMS must exceed this to START recording
    private let silenceThreshold: Float = 0.008      // RMS must drop below this to count as silence
    private let silenceDuration: TimeInterval = 1.5   // seconds of silence to auto-stop
    private let minSpeechDuration: TimeInterval = 0.4 // ignore very short blips
    private let maxRecordingDuration: TimeInterval = 30 // hard cap to prevent runaway recording

    private var audioEngine: AVAudioEngine?
    private var audioData = Data()
    private var sampleRate: Double = 16000

    private var speechStartTime: Date?
    private var lastSpeechTime: Date?
    private var silenceTimer: Timer?

    // Noise floor calibration — measured during first 0.5s of listening
    private var noiseFloor: Float = 0
    private var noiseCalibrationSamples: [Float] = []
    private var isCalibrating = false

    // MARK: - Start / Stop

    func start() {
        guard state == .idle else { return }
        error = nil
        audioData = Data()
        speechStartTime = nil
        lastSpeechTime = nil
        noiseFloor = 0
        noiseCalibrationSamples = []
        isCalibrating = true

        let engine = AVAudioEngine()
        let inputNode = engine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        sampleRate = inputFormat.sampleRate

        guard let recordFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: inputFormat.sampleRate,
            channels: 1,
            interleaved: false
        ) else {
            error = "Could not create audio format"
            return
        }

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: recordFormat) { [weak self] buffer, _ in
            guard let self else { return }
            let ptr = buffer.floatChannelData?[0]
            let count = Int(buffer.frameLength)
            guard let ptr, count > 0 else { return }

            let floats = UnsafeBufferPointer(start: ptr, count: count)
            let rms = Self.computeRMS(floats)

            // Collect raw audio bytes
            let bytes = Data(bytes: ptr, count: count * MemoryLayout<Float>.size)

            Task { @MainActor in
                self.processAudioBuffer(bytes, rms: rms)
            }
        }

        do {
            try engine.start()
            audioEngine = engine
            state = .listening

            // Poll for silence timeout
            silenceTimer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
                Task { @MainActor in
                    self?.checkSilenceTimeout()
                }
            }
        } catch {
            self.error = "Microphone access denied or unavailable: \(error.localizedDescription)"
        }
    }

    func stop() {
        silenceTimer?.invalidate()
        silenceTimer = nil
        guard let engine = audioEngine else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        audioEngine = nil
        state = .idle
        audioLevel = 0
        audioData = Data()
        speechStartTime = nil
        lastSpeechTime = nil
    }

    // MARK: - VAD logic

    private func processAudioBuffer(_ bytes: Data, rms: Float) {
        // Calibrate noise floor from the first ~0.5s
        if isCalibrating {
            noiseCalibrationSamples.append(rms)
            // ~0.5s worth of samples at 4096 buffer size / 16kHz ≈ ~2 samples
            if noiseCalibrationSamples.count >= 5 {
                noiseFloor = noiseCalibrationSamples.reduce(0, +) / Float(noiseCalibrationSamples.count)
                isCalibrating = false
            }
            audioLevel = 0
            return
        }

        // Effective threshold adapts to ambient noise
        let effectiveSpeechThreshold = max(speechStartThreshold, noiseFloor * 2.5)
        let effectiveSilenceThreshold = max(silenceThreshold, noiseFloor * 1.5)

        audioLevel = min(rms / max(effectiveSpeechThreshold * 3, 0.01), 1.0)

        let isSpeech = rms > effectiveSpeechThreshold
        let isSilence = rms < effectiveSilenceThreshold

        switch state {
        case .listening:
            if isSpeech {
                state = .recording
                speechStartTime = Date()
                lastSpeechTime = Date()
                audioData = Data()
                audioData.append(bytes)
            }

        case .recording:
            audioData.append(bytes)
            if !isSilence {
                // Any sound above silence threshold keeps the speech timer alive
                lastSpeechTime = Date()
            }

        case .idle, .processing:
            break
        }
    }

    private func checkSilenceTimeout() {
        guard state == .recording,
              let lastSpeech = lastSpeechTime,
              let speechStart = speechStartTime else { return }

        let silenceSoFar = Date().timeIntervalSince(lastSpeech)
        let totalDuration = Date().timeIntervalSince(speechStart)
        let speechDuration = lastSpeech.timeIntervalSince(speechStart)

        // Hard cap: force stop after max duration
        let shouldStop = (silenceSoFar >= silenceDuration && speechDuration >= minSpeechDuration)
                      || totalDuration >= maxRecordingDuration

        if shouldStop {
            state = .processing
            let wav = encodeWAV(floatData: audioData, sampleRate: sampleRate)
            audioData = Data()
            speechStartTime = nil
            lastSpeechTime = nil
            audioLevel = 0
            completedAudio = wav
        }
    }

    /// Resume listening after transcription/TTS is done.
    func resumeListening() {
        guard audioEngine != nil else { return }
        audioData = Data()
        speechStartTime = nil
        lastSpeechTime = nil
        // Re-calibrate noise floor
        noiseCalibrationSamples = []
        isCalibrating = true
        state = .listening
    }

    // MARK: - Audio encoding

    private static func computeRMS(_ samples: UnsafeBufferPointer<Float>) -> Float {
        var sum: Float = 0
        for s in samples { sum += s * s }
        return sqrt(sum / Float(max(samples.count, 1)))
    }

    private func encodeWAV(floatData: Data, sampleRate: Double) -> Data {
        let floatCount = floatData.count / MemoryLayout<Float>.size
        let floats = floatData.withUnsafeBytes {
            Array($0.bindMemory(to: Float.self).prefix(floatCount))
        }

        var int16Samples = [Int16]()
        int16Samples.reserveCapacity(floats.count)
        for sample in floats {
            let clamped = max(-1.0, min(1.0, sample))
            int16Samples.append(Int16(clamped * 32767))
        }

        let dataSize = int16Samples.count * 2
        var wav = Data()

        wav.append("RIFF".data(using: .ascii)!)
        wav.append(UInt32(36 + dataSize).littleEndianData)
        wav.append("WAVE".data(using: .ascii)!)

        wav.append("fmt ".data(using: .ascii)!)
        wav.append(UInt32(16).littleEndianData)
        wav.append(UInt16(1).littleEndianData)            // PCM
        wav.append(UInt16(1).littleEndianData)            // mono
        wav.append(UInt32(UInt32(sampleRate)).littleEndianData)
        wav.append(UInt32(UInt32(sampleRate) * 2).littleEndianData)
        wav.append(UInt16(2).littleEndianData)
        wav.append(UInt16(16).littleEndianData)

        wav.append("data".data(using: .ascii)!)
        wav.append(UInt32(dataSize).littleEndianData)
        int16Samples.withUnsafeBufferPointer { ptr in
            wav.append(Data(buffer: ptr))
        }

        return wav
    }
}

// MARK: - Helpers

private extension UInt32 {
    var littleEndianData: Data {
        var value = self.littleEndian
        return Data(bytes: &value, count: 4)
    }
}

private extension UInt16 {
    var littleEndianData: Data {
        var value = self.littleEndian
        return Data(bytes: &value, count: 2)
    }
}
