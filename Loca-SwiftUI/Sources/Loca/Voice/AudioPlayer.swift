import AVFoundation
import Foundation

/// Plays audio data (WAV format) through the default output device.
@MainActor
final class AudioPlayer: ObservableObject {

    @Published var isPlaying = false

    /// Called when playback finishes naturally (not on manual stop).
    var onFinished: (() -> Void)?

    private var player: AVAudioPlayer?

    func play(_ data: Data) {
        stop()
        do {
            let audioPlayer = try AVAudioPlayer(data: data)
            audioPlayer.delegate = delegateHandler
            audioPlayer.prepareToPlay()
            audioPlayer.play()
            player = audioPlayer
            isPlaying = true
        } catch {
            isPlaying = false
        }
    }

    func playBase64(_ base64String: String) {
        guard let data = Data(base64Encoded: base64String) else { return }
        play(data)
    }

    func stop() {
        player?.stop()
        player = nil
        isPlaying = false
    }

    private lazy var delegateHandler: DelegateHandler = {
        DelegateHandler { [weak self] in
            Task { @MainActor in
                self?.isPlaying = false
                self?.player = nil
                self?.onFinished?()
            }
        }
    }()
}

private class DelegateHandler: NSObject, AVAudioPlayerDelegate {
    let onFinish: () -> Void
    init(onFinish: @escaping () -> Void) { self.onFinish = onFinish }
    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        onFinish()
    }
}
