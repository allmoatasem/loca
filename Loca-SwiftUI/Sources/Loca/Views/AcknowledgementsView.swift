import SwiftUI

struct AcknowledgementsView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("Acknowledgments")
                    .font(.system(size: 14, weight: .semibold))
                Spacer()
                Button { state.isAcknowledgementsOpen = false } label: {
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
                VStack(alignment: .leading, spacing: 24) {
                    Text("Loca v0.10.2")
                        .font(.system(size: 22, weight: .bold))
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.bottom, 4)

                    AckSection(title: "Inference", items: [
                        AckItem(name: "mlx-lm",    author: "Apple",                         license: "MIT",        url: "https://github.com/ml-explore/mlx-lm"),
                        AckItem(name: "llama.cpp",  author: "Georgi Gerganov",               license: "MIT",        url: "https://github.com/ggml-org/llama.cpp"),
                        AckItem(name: "Ollama",     author: "Jeffrey Morgan & contributors", license: "MIT",        url: "https://github.com/ollama/ollama"),
                    ])
                    AckSection(title: "Python Backend", items: [
                        AckItem(name: "FastAPI",            author: "Sebastián Ramírez",             license: "MIT",        url: "https://github.com/fastapi/fastapi"),
                        AckItem(name: "Uvicorn",            author: "Encode",                        license: "BSD-3",      url: "https://github.com/encode/uvicorn"),
                        AckItem(name: "httpx",              author: "Encode",                        license: "BSD-3",      url: "https://github.com/encode/httpx"),
                        AckItem(name: "Pydantic",           author: "Samuel Colvin & contributors",  license: "MIT",        url: "https://github.com/pydantic/pydantic"),
                        AckItem(name: "trafilatura",        author: "Adrien Barbaresi",              license: "Apache 2.0", url: "https://github.com/adbar/trafilatura"),
                        AckItem(name: "pypdf",              author: "Mathieu Fenniak & contributors",license: "BSD-3",      url: "https://github.com/py-pdf/pypdf"),
                        AckItem(name: "huggingface_hub",    author: "Hugging Face",                  license: "Apache 2.0", url: "https://github.com/huggingface/huggingface_hub"),
                        AckItem(name: "PyYAML",             author: "Kirill Simonov",                license: "MIT",        url: "https://github.com/yaml/pyyaml"),
                        AckItem(name: "python-multipart",   author: "Andrew Dunham & contributors",  license: "Apache 2.0", url: "https://github.com/Kludex/python-multipart"),
                        AckItem(name: "Playwright",         author: "Microsoft",                     license: "Apache 2.0", url: "https://github.com/microsoft/playwright-python"),
                    ])
                    AckSection(title: "Tools & Services", items: [
                        AckItem(name: "llmfit",       author: "Alex Jones",              license: "MIT",       url: "https://github.com/AlexsJones/llmfit"),
                        AckItem(name: "SearXNG",      author: "SearXNG contributors",    license: "AGPL-3.0",  url: "https://github.com/searxng/searxng"),
                        AckItem(name: "Hugging Face", author: "Hugging Face",            license: nil,         url: "https://huggingface.co"),
                    ])
                    AckSection(title: "Frontend", items: [
                        AckItem(name: "Prism.js", author: "Lea Verou & contributors", license: "MIT", url: "https://github.com/PrismJS/prism"),
                    ])
                }
                .padding(20)
            }

            Divider()

            Text("Loca is built on the shoulders of these open source projects. Thank you.")
                .font(.system(size: 11))
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
        }
        .frame(width: 520)
        .frame(maxHeight: 560)
    }
}

// MARK: - Supporting types

private struct AckItem {
    let name: String
    let author: String
    let license: String?
    let url: String
}

private struct AckSection: View {
    let title: String
    let items: [AckItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(title.uppercased())
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(.secondary)
                .tracking(0.8)
                .padding(.bottom, 8)

            VStack(spacing: 0) {
                ForEach(Array(items.enumerated()), id: \.offset) { idx, item in
                    HStack(spacing: 8) {
                        Text(item.name)
                            .font(.system(size: 13, weight: .medium))
                            .frame(width: 150, alignment: .leading)
                        Text(item.author)
                            .font(.system(size: 12))
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                        Spacer()
                        if let license = item.license {
                            Text(license)
                                .font(.system(size: 10))
                                .foregroundColor(.secondary)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Color.secondary.opacity(0.1))
                                .clipShape(RoundedRectangle(cornerRadius: 4))
                        }
                        if let url = URL(string: item.url) {
                            Link("↗", destination: url)
                                .font(.system(size: 12))
                                .foregroundColor(.accentColor)
                        }
                    }
                    .padding(.vertical, 7)
                    if idx < items.count - 1 {
                        Divider()
                    }
                }
            }
        }
    }
}
