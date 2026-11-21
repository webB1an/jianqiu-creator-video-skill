// Use the system foreground model, including on Macs with an older build SDK.
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
#import <CoreImage/CoreImage.h>

// Declaring the selectors keeps the source buildable with SDKs predating macOS 14.
@protocol ForegroundInstances
- (NSIndexSet *)allInstances;
- (CVPixelBufferRef)generateScaledMaskForImageForInstances:(NSIndexSet *)instances
                                         fromRequestHandler:(VNImageRequestHandler *)handler
                                                      error:(NSError **)error CF_RETURNS_RETAINED;
@end

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 3) return 2;
        Class model = NSClassFromString(@"VNGenerateForegroundInstanceMaskRequest");
        if (!model) { fprintf(stderr, "System foreground masks require macOS 14 or later.\n"); return 1; }
        NSData *data = [NSData dataWithContentsOfFile:@(argv[1])];
        NSArray *files = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        CIContext *ctx = [CIContext contextWithOptions:nil];
        for (NSString *path in files) @autoreleasepool {
            NSError *error = nil;
            VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithURL:[NSURL fileURLWithPath:path] options:@{}];
            VNRequest *request = [[model alloc] init];
            if (![handler performRequests:@[request] error:&error]) {
                fprintf(stderr, "%s: %s\n", path.UTF8String, error.localizedDescription.UTF8String); return 1;
            }
            id<ForegroundInstances> instance = request.results.firstObject;
            if (!instance || !instance.allInstances.count) {
                fprintf(stderr, "No foreground detected in %s. Supply a box, key colour or mask.\n", path.UTF8String); return 1;
            }
            CVPixelBufferRef pixels = [instance generateScaledMaskForImageForInstances:instance.allInstances fromRequestHandler:handler error:&error];
            if (!pixels) { fprintf(stderr, "%s\n", error.localizedDescription.UTF8String); return 1; }
            CIImage *image = [CIImage imageWithCVPixelBuffer:pixels];
            NSString *name = [[path.lastPathComponent stringByDeletingPathExtension] stringByAppendingPathExtension:@"png"];
            NSURL *url = [NSURL fileURLWithPath:[@(argv[2]) stringByAppendingPathComponent:name]];
            CGColorSpaceRef space = CGColorSpaceCreateDeviceGray();
            BOOL ok = [ctx writePNGRepresentationOfImage:image toURL:url format:kCIFormatL8 colorSpace:space options:@{} error:&error];
            CGColorSpaceRelease(space);
            CVPixelBufferRelease(pixels);
            if (!ok) { fprintf(stderr, "%s\n", error.localizedDescription.UTF8String); return 1; }
        }
    }
    return 0;
}
