// Apple Vision Face + Objectness + Attention Saliency focus detector for auto_focus.py on macOS.
#import <CoreVideo/CoreVideo.h>
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>

static NSArray<NSNumber *> *rectToTopLeft(CGRect r) {
    // Vision uses normalized bottom-left origin; convert to standard top-left [x0, y0, x1, y1].
    double x0 = fmax(0.0, fmin(1.0, r.origin.x));
    double y0 = fmax(0.0, fmin(1.0, 1.0 - (r.origin.y + r.size.height)));
    double x1 = fmax(0.0, fmin(1.0, r.origin.x + r.size.width));
    double y1 = fmax(0.0, fmin(1.0, 1.0 - r.origin.y));
    return @[@(x0), @(y0), @(x1), @(y1)];
}

static NSArray<NSNumber *> *heatmapPeakXY(VNSaliencyImageObservation *obs) {
    if (!obs || !obs.pixelBuffer) return nil;
    CVPixelBufferRef pb = obs.pixelBuffer;
    if (CVPixelBufferGetPixelFormatType(pb) != kCVPixelFormatType_OneComponent32Float) return nil;
    CVPixelBufferLockBaseAddress(pb, kCVPixelBufferLock_ReadOnly);
    size_t w = CVPixelBufferGetWidth(pb);
    size_t h = CVPixelBufferGetHeight(pb);
    size_t bpr = CVPixelBufferGetBytesPerRow(pb);
    const uint8_t *base = (const uint8_t *)CVPixelBufferGetBaseAddress(pb);
    float maxVal = 0.0f;
    for (size_t y = 0; y < h; y++) {
        const float *row = (const float *)(base + y * bpr);
        for (size_t x = 0; x < w; x++) {
            if (row[x] > maxVal) maxVal = row[x];
        }
    }
    double sumW = 0.0, sumX = 0.0, sumY = 0.0;
    if (maxVal > 1e-4f) {
        float thresh = maxVal * 0.65f;
        for (size_t y = 0; y < h; y++) {
            const float *row = (const float *)(base + y * bpr);
            for (size_t x = 0; x < w; x++) {
                float v = row[x];
                if (v >= thresh) {
                    double wt = (double)v * (double)v * (double)v;
                    sumW += wt;
                    sumX += wt * ((x + 0.5) / (double)w);
                    sumY += wt * ((y + 0.5) / (double)h);
                }
            }
        }
    }
    CVPixelBufferUnlockBaseAddress(pb, kCVPixelBufferLock_ReadOnly);
    if (sumW <= 1e-9) return nil;
    return @[@(sumX / sumW), @(sumY / sumW)];
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 2) {
            fprintf(stderr, "Usage: focus_vision <image-paths.json>\n");
            return 2;
        }
        NSData *data = [NSData dataWithContentsOfFile:@(argv[1])];
        if (!data) { fprintf(stderr, "Unable to read %s\n", argv[1]); return 1; }
        NSArray *paths = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        if (![paths isKindOfClass:[NSArray class]]) { fprintf(stderr, "Expected JSON array of paths\n"); return 1; }

        NSMutableArray *results = [NSMutableArray arrayWithCapacity:paths.count];
        for (NSString *path in paths) @autoreleasepool {
            NSError *err = nil;
            NSURL *url = [NSURL fileURLWithPath:path];
            VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithURL:url options:@{}];
            VNDetectFaceRectanglesRequest *faceReq = [[VNDetectFaceRectanglesRequest alloc] init];
            VNGenerateObjectnessBasedSaliencyImageRequest *objReq = [[VNGenerateObjectnessBasedSaliencyImageRequest alloc] init];
            VNGenerateAttentionBasedSaliencyImageRequest *salReq = [[VNGenerateAttentionBasedSaliencyImageRequest alloc] init];

            NSMutableArray *faces = [NSMutableArray array];
            NSMutableArray *objBoxes = [NSMutableArray array];
            NSMutableArray *salBoxes = [NSMutableArray array];
            id attnPeak = [NSNull null];
            if ([handler performRequests:@[faceReq, objReq, salReq] error:&err]) {
                for (VNFaceObservation *obs in faceReq.results) {
                    [faces addObject:rectToTopLeft(obs.boundingBox)];
                }
                VNSaliencyImageObservation *objObs = objReq.results.firstObject;
                if (objObs && objObs.salientObjects.count > 0) {
                    for (VNRectangleObservation *obj in objObs.salientObjects) {
                        [objBoxes addObject:rectToTopLeft(obj.boundingBox)];
                    }
                }
                VNSaliencyImageObservation *salObs = salReq.results.firstObject;
                if (salObs) {
                    NSArray<NSNumber *> *pk = heatmapPeakXY(salObs);
                    if (pk) attnPeak = pk;
                    if (salObs.salientObjects.count > 0) {
                        for (VNRectangleObservation *obj in salObs.salientObjects) {
                            [salBoxes addObject:rectToTopLeft(obj.boundingBox)];
                        }
                    }
                }
            }
            [results addObject:@{
                @"path": path,
                @"faces": faces,
                @"objects": objBoxes,
                @"salient": salBoxes,
                @"attn_peak": attnPeak,
                @"error": err ? err.localizedDescription : [NSNull null]
            }];
        }

        NSData *out = [NSJSONSerialization dataWithJSONObject:results options:0 error:NULL];
        fwrite(out.bytes, 1, out.length, stdout);
        fputc('\n', stdout);
    }
    return 0;
}
