#import "PSTurboDecoder.h"
#import <stdio.h>
#import <stdlib.h>
#import <setjmp.h>
#import "jpeglib.h"

typedef struct {
    void *data;
    size_t capacity;
    volatile int32_t inUse;
} PSBuffer;

typedef struct {
    struct jpeg_error_mgr pub;
    jmp_buf jump;
} PSErrorMgr;

static void PSErrorExit(j_common_ptr cinfo) {
    PSErrorMgr *err = (PSErrorMgr *)cinfo->err;
    longjmp(err->jump, 1);
}

static void PSEmitMessage(j_common_ptr cinfo, int msgLevel) {
    (void)cinfo;
    (void)msgLevel;
}

static void PSReleaseBuffer(void *info, const void *data, size_t size) {
    (void)data;
    (void)size;
    PSBuffer *buf = (PSBuffer *)info;
    buf->inUse = 0;
}

@implementation PSTurboDecoder {
    PSBuffer *_pool;
    NSUInteger _poolCount;
    NSLock *_poolLock;
    CGColorSpaceRef _colorSpace;
}

- (instancetype)initWithPoolSize:(NSUInteger)count {
    self = [super init];
    if (self) {
        _poolCount = MAX(count, (NSUInteger)3);
        _pool = (PSBuffer *)calloc(_poolCount, sizeof(PSBuffer));
        _poolLock = [[NSLock alloc] init];
        _colorSpace = CGColorSpaceCreateDeviceRGB();
    }
    return self;
}

- (void)dealloc {
    for (NSUInteger i = 0; i < _poolCount; i++) {
        if (_pool[i].data) free(_pool[i].data);
    }
    if (_pool) free(_pool);
    if (_colorSpace) CGColorSpaceRelease(_colorSpace);
}

- (PSBuffer *)acquireBufferOfSize:(size_t)needed {
    [_poolLock lock];
    PSBuffer *chosen = NULL;
    for (NSUInteger i = 0; i < _poolCount; i++) {
        if (_pool[i].inUse) continue;
        chosen = &_pool[i];
        break;
    }
    if (chosen) {
        chosen->inUse = 1;
        if (chosen->capacity < needed) {
            if (chosen->data) free(chosen->data);
            chosen->data = valloc(needed);
            chosen->capacity = chosen->data ? needed : 0;
        }
    }
    [_poolLock unlock];

    if (chosen && !chosen->data) {
        chosen->inUse = 0;
        return NULL;
    }
    return chosen;
}

- (CGImageRef)decode:(NSData *)jpeg {
    if (!jpeg || jpeg.length == 0) return NULL;

    struct jpeg_decompress_struct cinfo;
    PSErrorMgr jerr;

    cinfo.err = jpeg_std_error(&jerr.pub);
    jerr.pub.error_exit = PSErrorExit;
    jerr.pub.emit_message = PSEmitMessage;

    if (setjmp(jerr.jump)) {
        jpeg_destroy_decompress(&cinfo);
        return NULL;
    }

    jpeg_create_decompress(&cinfo);
    jpeg_mem_src(&cinfo, (const unsigned char *)[jpeg bytes], (unsigned long)[jpeg length]);

    if (jpeg_read_header(&cinfo, TRUE) != JPEG_HEADER_OK) {
        jpeg_destroy_decompress(&cinfo);
        return NULL;
    }

    // Direct native CoreAnimation format: BGRX (32-bit, Little Endian)
    cinfo.out_color_space = JCS_EXT_BGRX;
    cinfo.dct_method = JDCT_IFAST;
    cinfo.do_fancy_upsampling = FALSE;

    jpeg_start_decompress(&cinfo);

    size_t width = cinfo.output_width;
    size_t height = cinfo.output_height;
    size_t stride = width * 4;
    size_t needed = stride * height;

    PSBuffer *buf = [self acquireBufferOfSize:needed];
    if (!buf) {
        jpeg_abort_decompress(&cinfo);
        jpeg_destroy_decompress(&cinfo);
        return NULL;
    }

    uint8_t *base = (uint8_t *)buf->data;
    while (cinfo.output_scanline < cinfo.output_height) {
        JSAMPROW row = base + cinfo.output_scanline * stride;
        jpeg_read_scanlines(&cinfo, &row, 1);
    }

    jpeg_finish_decompress(&cinfo);
    jpeg_destroy_decompress(&cinfo);

    CGDataProviderRef provider = CGDataProviderCreateWithData(buf, buf->data, needed, PSReleaseBuffer);
    if (!provider) {
        buf->inUse = 0;
        return NULL;
    }

    CGImageRef image = CGImageCreate(
        width, height, 8, 32, stride, _colorSpace,
        kCGImageAlphaNoneSkipFirst | kCGBitmapByteOrder32Little,
        provider, NULL, false, kCGRenderingIntentDefault
    );
    CGDataProviderRelease(provider);

    if (!image) {
        buf->inUse = 0;
    }
    return image;
}

@end
