import SwiftUI

struct GlossaryView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("Glossary")
                    .font(.system(size: 14, weight: .semibold))
                Spacer()
                Button { state.isGlossaryOpen = false } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(.secondary)
                        .frame(width: 24, height: 24)
                        .background(Color.secondary.opacity(0.1))
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(GlossaryEntry.all.enumerated()), id: \.offset) { idx, entry in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(entry.term)
                                .font(.system(size: 13, weight: .semibold))
                            Text(entry.definition)
                                .font(.system(size: 12))
                                .foregroundColor(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(.vertical, 10)
                        if idx < GlossaryEntry.all.count - 1 {
                            Divider()
                        }
                    }
                }
                .padding(20)
            }
        }
        .frame(width: 520)
        .frame(maxHeight: 560)
    }
}

// MARK: - Data

private struct GlossaryEntry {
    let term: String
    let definition: String

    static let all: [GlossaryEntry] = [
        GlossaryEntry(
            term: "Token",
            definition: "The basic unit of text a language model processes. Roughly ¾ of an English word on average. A sentence of 10 words is approximately 13–14 tokens."),
        GlossaryEntry(
            term: "Context Window",
            definition: "The maximum number of tokens a model can attend to at once — both your input and the model's output combined. Larger windows let the model remember more of the conversation."),
        GlossaryEntry(
            term: "Temperature",
            definition: "Controls the randomness of output. At 0 the model always picks the most likely next token (deterministic). At 2 it samples very freely, producing creative but potentially incoherent text."),
        GlossaryEntry(
            term: "Top-P (Nucleus Sampling)",
            definition: "Restricts token selection to the smallest set whose cumulative probability exceeds P. A value of 0.9 means only tokens covering 90% of the probability mass are considered at each step."),
        GlossaryEntry(
            term: "Top-K",
            definition: "Limits token selection to the K most probable candidates at each generation step. Lower values make output more focused; higher values allow more variety."),
        GlossaryEntry(
            term: "Repeat Penalty",
            definition: "A multiplier applied to already-seen tokens to discourage repetition. Values above 1.0 make the model less likely to reuse recent words or phrases."),
        GlossaryEntry(
            term: "Max Tokens",
            definition: "The maximum number of tokens the model will generate in a single response. Does not affect how much conversation history is retained."),
        GlossaryEntry(
            term: "Inference",
            definition: "The act of running a trained model to produce output. Distinct from training — inference uses existing weights to generate text without updating them."),
        GlossaryEntry(
            term: "LLM (Large Language Model)",
            definition: "A neural network trained on large text corpora to predict and generate natural language. Examples include Llama, Mistral, Qwen, and Phi."),
        GlossaryEntry(
            term: "MLX",
            definition: "Apple's open-source machine learning framework optimised for Apple Silicon. Used by Loca to run models natively on the Neural Engine and GPU."),
        GlossaryEntry(
            term: "GGUF",
            definition: "A binary model file format used by llama.cpp. Designed for fast loading and efficient CPU/GPU inference. Replaces the older GGML format."),
        GlossaryEntry(
            term: "Quantization",
            definition: "Reducing the numerical precision of model weights (e.g., from 32-bit float to 4-bit integer). Shrinks model size and speeds up inference at a small cost to output quality."),
        GlossaryEntry(
            term: "System Prompt",
            definition: "Instructions given to the model before the conversation starts. Sets the model's persona, constraints, or context. Loca's built-in prompts are mode-aware and include hardware context."),
        GlossaryEntry(
            term: "Embedding",
            definition: "A fixed-length numeric vector representing the meaning of a piece of text. Used in semantic search and retrieval-augmented generation to find conceptually similar content."),
        GlossaryEntry(
            term: "RAG (Retrieval-Augmented Generation)",
            definition: "A technique that injects retrieved documents into the prompt before asking the model to answer, grounding responses in specific source material."),
        GlossaryEntry(
            term: "Fine-tuning",
            definition: "Further training a pre-trained model on a smaller, domain-specific dataset to specialise its behaviour for a particular task."),
        GlossaryEntry(
            term: "VRAM",
            definition: "Video RAM — memory on the GPU used for storing model weights and activations during inference. Larger models require more VRAM."),
        GlossaryEntry(
            term: "Unified Memory",
            definition: "Apple Silicon's shared memory pool used by both the CPU and GPU. Eliminates the need to copy data between separate CPU RAM and GPU VRAM, making larger models practical on Mac."),
        GlossaryEntry(
            term: "Hallucination",
            definition: "When a model generates plausible-sounding but factually incorrect information with apparent confidence. A fundamental limitation of current LLMs."),
        GlossaryEntry(
            term: "Prompt",
            definition: "The full input sent to a language model, including the system prompt, conversation history, and the user's current message."),
        GlossaryEntry(
            term: "VAD (Voice Activity Detection)",
            definition: "An algorithm that detects when a person is speaking versus silent. Loca uses VAD to automatically segment audio before passing it to the speech-to-text model."),
        GlossaryEntry(
            term: "Whisper",
            definition: "OpenAI's open-source speech recognition model. Loca uses mlx-whisper, an Apple Silicon–optimised port, for local speech-to-text transcription."),
    ]
}
