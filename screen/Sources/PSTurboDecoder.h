#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>

/// High-performance JPEG decoder with ARM NEON SIMD acceleration.
/// Decodes directly into CoreAnimation native 32-bit BGRX pixel format
/// with zero copy and a reusable buffer pool.
@interface PSTurboDecoder : NSObject

- (instancetype)initWithPoolSize:(NSUInteger)count;

/// Decodes JPEG data into a CGImageRef formatted for direct CALayer presentation.
/// The caller owns the returned CGImageRef and must call CGImageRelease when finished.
- (CGImageRef)decode:(NSData *)jpeg;

@end
