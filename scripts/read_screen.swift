import Foundation
import Vision

// Read visible text and normalized top-origin boxes from exported frame images.
guard CommandLine.arguments.count > 2 else { fatalError("read_screen OUTPUT.json FRAME.jpg [...]") }
struct Box: Codable {
    let text: String
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}
struct Result: Codable { let file: String; let words: [Box] }
var results = [Result]()
for name in CommandLine.arguments.dropFirst(2) {
    let cells: [Box] = try autoreleasepool {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = false
        request.minimumTextHeight = 0.006
        try VNImageRequestHandler(url:URL(fileURLWithPath:name)).perform([request])
        return ((request.results as? [VNRecognizedTextObservation]) ?? []).compactMap { observed in
            guard let match = observed.topCandidates(1).first else { return nil }
            let b = observed.boundingBox
            return Box(text:match.string,x:Double(b.minX),y:Double(1-b.maxY),width:Double(b.width),height:Double(b.height))
        }
    }
    results.append(Result(file:URL(fileURLWithPath:name).lastPathComponent, words:cells))
}
try JSONEncoder().encode(results).write(to:URL(fileURLWithPath:CommandLine.arguments[1]),options:.atomic)
