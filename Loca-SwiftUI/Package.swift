// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Loca",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "Loca", targets: ["Loca"]),
    ],
    dependencies: [],
    targets: [
        .executableTarget(
            name: "Loca",
            path: "Sources/Loca",
            resources: [
                .process("Resources"),
            ]
        ),
    ]
)
