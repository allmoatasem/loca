/**
 * Open-source projects Loca leans on. Kept in sync with the legacy HTML
 * ack panel (src/static/index.html) and Loca-SwiftUI's
 * AcknowledgementsView.swift — editing one without the other is a parity
 * bug.
 */

export interface AckItem {
  name: string;
  author: string;
  license?: string;
  url: string;
}

export interface AckSection {
  title: string;
  items: AckItem[];
}

export const ACK_SECTIONS: AckSection[] = [
  {
    title: 'Inference',
    items: [
      { name: 'mlx-lm',    author: 'Apple',                         license: 'MIT', url: 'https://github.com/ml-explore/mlx-lm' },
      { name: 'llama.cpp', author: 'Georgi Gerganov',               license: 'MIT', url: 'https://github.com/ggml-org/llama.cpp' },
      { name: 'Ollama',    author: 'Jeffrey Morgan & contributors', license: 'MIT', url: 'https://github.com/ollama/ollama' },
    ],
  },
  {
    title: 'Python Backend',
    items: [
      { name: 'FastAPI',          author: 'Sebastián Ramírez',              license: 'MIT',        url: 'https://github.com/fastapi/fastapi' },
      { name: 'Uvicorn',          author: 'Encode',                         license: 'BSD-3',      url: 'https://github.com/encode/uvicorn' },
      { name: 'httpx',            author: 'Encode',                         license: 'BSD-3',      url: 'https://github.com/encode/httpx' },
      { name: 'Pydantic',         author: 'Samuel Colvin & contributors',   license: 'MIT',        url: 'https://github.com/pydantic/pydantic' },
      { name: 'trafilatura',      author: 'Adrien Barbaresi',               license: 'Apache 2.0', url: 'https://github.com/adbar/trafilatura' },
      { name: 'pypdf',            author: 'Mathieu Fenniak & contributors', license: 'BSD-3',      url: 'https://github.com/py-pdf/pypdf' },
      { name: 'huggingface_hub',  author: 'Hugging Face',                   license: 'Apache 2.0', url: 'https://github.com/huggingface/huggingface_hub' },
      { name: 'PyYAML',           author: 'Kirill Simonov',                 license: 'MIT',        url: 'https://github.com/yaml/pyyaml' },
      { name: 'python-multipart', author: 'Andrew Dunham & contributors',   license: 'Apache 2.0', url: 'https://github.com/Kludex/python-multipart' },
      { name: 'Playwright',       author: 'Microsoft',                      license: 'Apache 2.0', url: 'https://github.com/microsoft/playwright-python' },
    ],
  },
  {
    title: 'Voice',
    items: [
      { name: 'mlx-whisper', author: 'Apple & contributors', license: 'MIT', url: 'https://github.com/ml-explore/mlx-examples/tree/main/whisper' },
      { name: 'mlx-audio',   author: 'Apple & contributors', license: 'MIT', url: 'https://github.com/ml-explore/mlx-audio' },
    ],
  },
  {
    title: 'Memory',
    items: [
      { name: 'MemPalace',  author: 'MemPalace contributors',    license: 'MIT',        url: 'https://github.com/MemPalace/mempalace' },
      { name: 'ChromaDB',   author: 'Chroma Core contributors',  license: 'Apache 2.0', url: 'https://github.com/chroma-core/chroma' },
      { name: 'sqlite-vec', author: 'Alex Garcia',               license: 'MIT',        url: 'https://github.com/asg017/sqlite-vec' },
    ],
  },
  {
    title: 'Tools & Services',
    items: [
      { name: 'llmfit',       author: 'Alex Jones',           license: 'MIT',      url: 'https://github.com/AlexsJones/llmfit' },
      { name: 'SearXNG',      author: 'SearXNG contributors', license: 'AGPL-3.0', url: 'https://github.com/searxng/searxng' },
      { name: 'Hugging Face', author: 'Hugging Face',                              url: 'https://huggingface.co' },
    ],
  },
  {
    title: 'Frontend',
    items: [
      { name: 'Svelte',     author: 'Svelte contributors',                    license: 'MIT', url: 'https://github.com/sveltejs/svelte' },
      { name: 'Vite',       author: 'Evan You & contributors',                license: 'MIT', url: 'https://github.com/vitejs/vite' },
      { name: 'marked',     author: 'Christopher Jeffrey & contributors',     license: 'MIT', url: 'https://github.com/markedjs/marked' },
      { name: 'DOMPurify',  author: 'Cure53',                                  license: 'Apache 2.0', url: 'https://github.com/cure53/DOMPurify' },
      { name: 'KaTeX',      author: 'Khan Academy & contributors',             license: 'MIT', url: 'https://github.com/KaTeX/KaTeX' },
      { name: 'Prism.js',   author: 'Lea Verou & contributors',                license: 'MIT', url: 'https://github.com/PrismJS/prism' },
    ],
  },
];
