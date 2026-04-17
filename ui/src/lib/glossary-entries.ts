/**
 * Glossary entries — source of truth is Loca-SwiftUI/Sources/Loca/Views/GlossaryView.swift.
 * When adding or editing an entry, update both files in the same PR
 * (see the PR template's UI parity checklist).
 */
export interface GlossaryEntry {
  term: string;
  definition: string;
}

export const GLOSSARY_ENTRIES: readonly GlossaryEntry[] = [
  {
    term: 'Token',
    definition:
      'The basic unit of text a language model processes. Roughly ¾ of an English word on average. A sentence of 10 words is approximately 13–14 tokens.',
  },
  {
    term: 'Context Window',
    definition:
      "The maximum number of tokens a model can attend to at once — both your input and the model's output combined. Larger windows let the model remember more of the conversation.",
  },
  {
    term: 'Temperature',
    definition:
      'Controls the randomness of output. At 0 the model always picks the most likely next token (deterministic). At 2 it samples very freely, producing creative but potentially incoherent text.',
  },
  {
    term: 'Top-P (Nucleus Sampling)',
    definition:
      'Restricts token selection to the smallest set whose cumulative probability exceeds P. A value of 0.9 means only tokens covering 90% of the probability mass are considered at each step.',
  },
  {
    term: 'Top-K',
    definition:
      'Limits token selection to the K most probable candidates at each generation step. Lower values make output more focused; higher values allow more variety.',
  },
  {
    term: 'Repeat Penalty',
    definition:
      'A multiplier applied to already-seen tokens to discourage repetition. Values above 1.0 make the model less likely to reuse recent words or phrases.',
  },
  {
    term: 'Max Tokens',
    definition:
      'The maximum number of tokens the model will generate in a single response. Does not affect how much conversation history is retained.',
  },
  {
    term: 'Inference',
    definition:
      'The act of running a trained model to produce output. Distinct from training — inference uses existing weights to generate text without updating them.',
  },
  {
    term: 'LLM (Large Language Model)',
    definition:
      'A neural network trained on large text corpora to predict and generate natural language. Examples include Llama, Mistral, Qwen, and Phi.',
  },
  {
    term: 'MLX',
    definition:
      "Apple's open-source machine learning framework optimised for Apple Silicon. Used by Loca to run models natively on the Neural Engine and GPU.",
  },
  {
    term: 'GGUF',
    definition:
      'A binary model file format used by llama.cpp. Designed for fast loading and efficient CPU/GPU inference. Replaces the older GGML format.',
  },
  {
    term: 'Quantization',
    definition:
      'Reducing the numerical precision of model weights (e.g., from 32-bit float to 4-bit integer). Shrinks model size and speeds up inference at a small cost to output quality.',
  },
  {
    term: 'System Prompt',
    definition:
      "Instructions given to the model before the conversation starts. Sets the model's persona, constraints, or context. Loca's built-in prompts are mode-aware and include hardware context.",
  },
  {
    term: 'Embedding',
    definition:
      'A fixed-length numeric vector representing the meaning of a piece of text. Used in semantic search and retrieval-augmented generation to find conceptually similar content.',
  },
  {
    term: 'RAG (Retrieval-Augmented Generation)',
    definition:
      'A technique that injects retrieved documents into the prompt before asking the model to answer, grounding responses in specific source material.',
  },
  {
    term: 'Fine-tuning',
    definition:
      'Further training a pre-trained model on a smaller, domain-specific dataset to specialise its behaviour for a particular task.',
  },
  {
    term: 'VRAM',
    definition:
      'Video RAM — memory on the GPU used for storing model weights and activations during inference. Larger models require more VRAM.',
  },
  {
    term: 'Unified Memory',
    definition:
      "Apple Silicon's shared memory pool used by both the CPU and GPU. Eliminates the need to copy data between separate CPU RAM and GPU VRAM, making larger models practical on Mac.",
  },
  {
    term: 'Hallucination',
    definition:
      'When a model generates plausible-sounding but factually incorrect information with apparent confidence. A fundamental limitation of current LLMs.',
  },
  {
    term: 'Prompt',
    definition:
      "The full input sent to a language model, including the system prompt, conversation history, and the user's current message.",
  },
  {
    term: 'VAD (Voice Activity Detection)',
    definition:
      'An algorithm that detects when a person is speaking versus silent. Loca uses VAD to automatically segment audio before passing it to the speech-to-text model.',
  },
  {
    term: 'Whisper',
    definition:
      "OpenAI's open-source speech recognition model. Loca uses mlx-whisper, an Apple Silicon–optimised port, for local speech-to-text transcription.",
  },
];
