#import "PSStreamClient.h"
#import "PSScreenPolicy.h"

static const NSUInteger PSMaximumFrameBytes = 4 * 1024 * 1024;
static const NSUInteger PSMaximumHeaderBytes = 2048;
static const NSUInteger PSMaximumBufferedBytes = 8 * 1024 * 1024;

@interface PSStreamClient () <NSURLSessionDataDelegate>
@property (nonatomic, strong) NSOperationQueue *delegateQueue;
@property (nonatomic, strong) NSURLSession *session;
@property (nonatomic, strong) NSURLSessionDataTask *task;
@property (nonatomic, strong) NSMutableData *buffer;
@property (nonatomic) NSUInteger expectedLength;
@property (nonatomic) NSUInteger expectedSequence;
@property (nonatomic) NSUInteger frameCounter;
@property (nonatomic) NSInteger httpStatus;
@property (nonatomic) BOOL stopped;
@end

@implementation PSStreamClient
- (instancetype)init {
    self = [super init];
    if (self) {
        _delegateQueue = [NSOperationQueue new];
        _delegateQueue.maxConcurrentOperationCount = 1;
        _delegateQueue.name = @"local.extendscreen.stream";
        _stopped = YES;
    }
    return self;
}

- (void)startWithServerURL:(NSString *)serverURL token:(NSString *)token fps:(NSUInteger)fps {
    if (!PSDeviceTokenIsSafe(token.UTF8String)) {
        [self report:NO message:@"Pair this iPad with the PC host first." status:0];
        return;
    }
    NSURLComponents *parts = [NSURLComponents componentsWithString:
        [serverURL stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]];
    BOOL valid = parts && parts.host.length;
    if (valid) {
        NSString *scheme = parts.scheme.lowercaseString;
        BOOL plain = [scheme isEqualToString:@"http"];
        long long port = parts.port ? parts.port.longLongValue : (plain ? 80 : 443);
        unsigned safePort = port > 0 && port <= 65535 ? (unsigned)port : 0;
        BOOL userInfo = parts.user != nil || parts.password != nil;
        valid = plain ?
            PSPlainOriginIsSafe(scheme.UTF8String, parts.host.UTF8String, safePort,
                                parts.path.UTF8String, userInfo, parts.query != nil, parts.fragment != nil) :
            PSOriginIsSafe(scheme.UTF8String, parts.host.UTF8String, safePort,
                           parts.path.UTF8String, userInfo, parts.query != nil, parts.fragment != nil);
    }
    if (!valid) {
        [self report:NO message:@"The saved host address is not a valid private LAN origin. Open Settings." status:0];
        return;
    }
    parts.path = @"/stream";
    parts.query = [NSString stringWithFormat:@"fps=%lu", (unsigned long)MAX((NSUInteger)5, MIN((NSUInteger)60, fps))];
    parts.fragment = nil;
    NSURL *url = parts.URL;
    if (!url) {
        [self report:NO message:@"The stream URL could not be built." status:0];
        return;
    }

    [self.delegateQueue addOperationWithBlock:^{
        [self stopNow];
        self.stopped = NO;
        self.buffer = [NSMutableData data];
        self.expectedLength = 0;
        self.expectedSequence = 0;
        self.frameCounter = 0;
        self.httpStatus = 0;

        NSURLSessionConfiguration *configuration = [NSURLSessionConfiguration ephemeralSessionConfiguration];
        configuration.URLCache = nil;
        configuration.URLCredentialStorage = nil;
        configuration.HTTPCookieStorage = nil;
        configuration.HTTPShouldSetCookies = NO;
        configuration.requestCachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
        configuration.timeoutIntervalForRequest = 15;
        configuration.timeoutIntervalForResource = 86400;
        configuration.HTTPMaximumConnectionsPerHost = 1;
        configuration.allowsCellularAccess = NO;
        self.session = [NSURLSession sessionWithConfiguration:configuration delegate:self delegateQueue:self.delegateQueue];
        NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
        request.HTTPMethod = @"GET";
        [request setValue:[NSString stringWithFormat:@"Bearer %@", token] forHTTPHeaderField:@"Authorization"];
        [request setValue:@"multipart/x-mixed-replace" forHTTPHeaderField:@"Accept"];
        [request setValue:@"no-store" forHTTPHeaderField:@"Cache-Control"];
        self.task = [self.session dataTaskWithRequest:request];
        [self.task resume];
    }];
}

- (void)stop {
    [self.delegateQueue addOperationWithBlock:^{ [self stopNow]; }];
}

- (void)stopNow {
    if (self.stopped) return;
    self.stopped = YES;
    [self.task cancel];
    [self.session invalidateAndCancel];
    self.task = nil;
    self.session = nil;
    self.buffer = nil;
}

- (void)report:(BOOL)streaming message:(NSString *)message status:(NSInteger)status {
    PSStreamStatusHandler handler = self.statusHandler;
    if (!handler) return;
    dispatch_async(dispatch_get_main_queue(), ^{ handler(streaming, message, status); });
}

- (void)failWithMessage:(NSString *)message status:(NSInteger)status {
    if (self.stopped) return;
    self.stopped = YES;
    [self.task cancel];
    [self.session invalidateAndCancel];
    self.task = nil;
    self.session = nil;
    self.buffer = nil;
    [self report:NO message:message status:status];
}

- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
    willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request
    completionHandler:(void (^)(NSURLRequest *))completionHandler {
    completionHandler(nil);
    [self failWithMessage:@"The host requested a redirect. It was refused." status:self.httpStatus];
}

- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
    didReceiveChallenge:(NSURLAuthenticationChallenge *)challenge
    completionHandler:(void (^)(NSURLSessionAuthChallengeDisposition, NSURLCredential *))completionHandler {
    if (!self.stopped && [challenge.protectionSpace.authenticationMethod isEqualToString:NSURLAuthenticationMethodServerTrust])
        completionHandler(NSURLSessionAuthChallengePerformDefaultHandling, nil);
    else completionHandler(NSURLSessionAuthChallengeCancelAuthenticationChallenge, nil);
}

- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task
    didReceiveResponse:(NSURLResponse *)response
    completionHandler:(void (^)(NSURLSessionResponseDisposition))completionHandler {
    NSHTTPURLResponse *http = [response isKindOfClass:[NSHTTPURLResponse class]] ? (NSHTTPURLResponse *)response : nil;
    self.httpStatus = http.statusCode;
    NSString *mime = response.MIMEType.lowercaseString ?: @"";
    if (!http || http.statusCode != 200) {
        completionHandler(NSURLSessionResponseCancel);
        NSString *message = (http.statusCode == 401 || http.statusCode == 403) ?
            @"The host rejected this iPad's pairing. Open Settings and pair again." :
            (http.statusCode == 503 ?
             @"The PC host is busy or screen capture failed. Check the host window." :
             [NSString stringWithFormat:@"The host returned HTTP %ld.", (long)http.statusCode]);
        [self failWithMessage:message status:http.statusCode];
        return;
    }
    if ([mime hasPrefix:@"application/json"] || [mime hasPrefix:@"text/"]) {
        completionHandler(NSURLSessionResponseCancel);
        [self failWithMessage:[NSString stringWithFormat:
            @"The host returned '%@' instead of an MJPEG stream.",
            response.MIMEType ?: @"unknown"] status:http.statusCode];
        return;
    }
    completionHandler(NSURLSessionResponseAllow);
    [self report:YES message:@"" status:http.statusCode];
}

- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:(NSData *)data {
    if (self.stopped) return;
    if (self.buffer.length > PSMaximumBufferedBytes ||
        data.length > PSMaximumBufferedBytes - self.buffer.length) {
        [self failWithMessage:@"The stream buffer exceeded its safety limit." status:self.httpStatus];
        return;
    }
    [self.buffer appendData:data];
    static const uint8_t soiBytes[2] = {0xFF, 0xD8};
    static const uint8_t eoiBytes[2] = {0xFF, 0xD9};
    NSData *soi = [NSData dataWithBytes:soiBytes length:2];
    NSData *eoi = [NSData dataWithBytes:eoiBytes length:2];
    NSData *latestFrame = nil;
    NSUInteger latestSequence = 0;

    while (!self.stopped) {
        if (self.buffer.length < 2) break;
        // Find the next JPEG start marker. Any multipart boundary, headers or
        // leftover separator bytes before it are discarded.
        NSRange start = [self.buffer rangeOfData:soi options:0
                                           range:NSMakeRange(0, self.buffer.length)];
        if (start.location == NSNotFound) {
            if (self.buffer.length > PSMaximumHeaderBytes) {
                [self failWithMessage:@"The stream did not contain a JPEG frame." status:self.httpStatus];
            }
            break;
        }
        if (start.location > 0) {
            [self.buffer replaceBytesInRange:NSMakeRange(0, start.location)
                                   withBytes:NULL length:0];
        }
        if (self.buffer.length < 4) break;
        NSRange end = [self.buffer rangeOfData:eoi options:0
                                         range:NSMakeRange(2, self.buffer.length - 2)];
        if (end.location == NSNotFound) {
            if (self.buffer.length > PSMaximumFrameBytes) {
                [self failWithMessage:@"The stream frame was too large." status:self.httpStatus];
            }
            break;
        }
        NSUInteger frameLength = end.location + end.length;
        latestFrame = [self.buffer subdataWithRange:NSMakeRange(0, frameLength)];
        [self.buffer replaceBytesInRange:NSMakeRange(0, frameLength)
                               withBytes:NULL length:0];
        self.frameCounter++;
        latestSequence = self.frameCounter;
    }

    if (latestFrame && !self.stopped) {
        PSStreamFrameHandler handler = self.frameHandler;
        if (handler) {
            dispatch_async(dispatch_get_main_queue(), ^{ handler(latestFrame, latestSequence); });
        }
    }
}

- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:(NSError *)error {
    if (self.stopped) return;
    NSString *message = @"The screen stream ended. Reconnecting...";
    if (error) {
        if (error.code == NSURLErrorTimedOut)
            message = @"The screen stream timed out. Reconnecting...";
        else if (error.code == NSURLErrorCannotConnectToHost || error.code == NSURLErrorCannotFindHost)
            message = @"Cannot reach the PC host. Reconnecting...";
        else if (error.code != NSURLErrorCancelled)
            message = @"The screen stream was interrupted. Reconnecting...";
    }
    [self failWithMessage:message status:self.httpStatus];
}

- (void)dealloc {
    [self.task cancel];
    [self.session invalidateAndCancel];
}
@end
