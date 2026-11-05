#import "PSInputClient.h"
#import "PSScreenPolicy.h"

static const NSUInteger PSMaximumQueuedInputEvents = 96;

@interface PSInputClient ()
@property (nonatomic, strong) NSURLSession *session;
@property (nonatomic, copy) NSString *serverURL;
@property (nonatomic, copy) NSString *token;
@property (nonatomic, strong) NSMutableArray *queue;
@property (nonatomic) BOOL enabled;
@property (nonatomic) BOOL sending;
@property (nonatomic) NSTimeInterval lastFailure;
@end

@implementation PSInputClient
- (instancetype)init {
    self = [super init];
    if (self) {
        _queue = [NSMutableArray array];
    }
    return self;
}

- (void)configureWithServerURL:(NSString *)serverURL token:(NSString *)token enabled:(BOOL)enabled {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{
            [self configureWithServerURL:serverURL token:token enabled:enabled];
        });
        return;
    }
    [self cancel];
    self.serverURL = [serverURL copy];
    self.token = [token copy];
    self.enabled = enabled && PSDeviceTokenIsSafe(token.UTF8String) && serverURL.length > 0;
    [self.queue removeAllObjects];
    self.sending = NO;
}

- (void)sendEvent:(NSDictionary *)event {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self sendEvent:event]; });
        return;
    }
    if (!self.enabled || !event) return;
    if (self.queue.count >= PSMaximumQueuedInputEvents) {
        NSUInteger victim = NSNotFound;
        for (NSUInteger index = 0; index < self.queue.count; index++) {
            NSString *kind = self.queue[index][@"type"];
            if ([kind isEqualToString:@"move"] || [kind isEqualToString:@"scroll"]) {
                victim = index;
                break;
            }
        }
        [self.queue removeObjectAtIndex:victim == NSNotFound ? 0 : victim];
    }
    [self.queue addObject:[event copy]];
    [self pump];
}

- (void)pump {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self pump]; });
        return;
    }
    if (self.sending || !self.enabled || self.queue.count == 0) return;
    NSURLComponents *parts = [NSURLComponents componentsWithString:self.serverURL];
    if (!parts.host.length) {
        [self.queue removeAllObjects];
        return;
    }
    parts.path = @"/input";
    parts.query = nil;
    parts.fragment = nil;
    NSURL *url = parts.URL;
    if (!url) {
        [self.queue removeAllObjects];
        return;
    }
    NSDictionary *event = self.queue[0];
    [self.queue removeObjectAtIndex:0];
    NSError *encodeError = nil;
    NSData *body = [NSJSONSerialization dataWithJSONObject:event options:0 error:&encodeError];
    if (!body) {
        [self pump];
        return;
    }
    if (!self.session) {
        NSURLSessionConfiguration *configuration = [NSURLSessionConfiguration ephemeralSessionConfiguration];
        configuration.URLCache = nil;
        configuration.URLCredentialStorage = nil;
        configuration.HTTPCookieStorage = nil;
        configuration.HTTPShouldSetCookies = NO;
        configuration.requestCachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
        configuration.timeoutIntervalForRequest = 3;
        configuration.timeoutIntervalForResource = 5;
        configuration.HTTPMaximumConnectionsPerHost = 1;
        configuration.allowsCellularAccess = NO;
        self.session = [NSURLSession sessionWithConfiguration:configuration];
    }
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    request.HTTPMethod = @"POST";
    request.HTTPBody = body;
    [request setValue:@"application/json; charset=utf-8" forHTTPHeaderField:@"Content-Type"];
    [request setValue:@"no-store" forHTTPHeaderField:@"Cache-Control"];
    [request setValue:[NSString stringWithFormat:@"Bearer %@", self.token] forHTTPHeaderField:@"Authorization"];
    self.sending = YES;
    __weak PSInputClient *weakSelf = self;
    NSURLSessionDataTask *task = [self.session dataTaskWithRequest:request
        completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
            dispatch_async(dispatch_get_main_queue(), ^{
                PSInputClient *strongSelf = weakSelf;
                if (!strongSelf) return;
                strongSelf.sending = NO;
                NSHTTPURLResponse *http = [response isKindOfClass:[NSHTTPURLResponse class]] ?
                    (NSHTTPURLResponse *)response : nil;
                BOOL ok = !error && http && http.statusCode >= 200 && http.statusCode < 300;
                if (!ok) {
                    NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
                    if (now - strongSelf.lastFailure > 5.0) {
                        strongSelf.lastFailure = now;
                        PSInputFailureHandler failure = strongSelf.failureHandler;
                        if (failure) {
                            failure((http && (http.statusCode == 401 || http.statusCode == 403)) ?
                                @"Input was rejected. Re-pair this iPad in Settings." :
                                @"Input could not reach the PC. Check the host.");
                        }
                    }
                }
                [strongSelf pump];
            });
        }];
    [task resume];
}

- (void)cancel {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self cancel]; });
        return;
    }
    self.enabled = NO;
    [self.queue removeAllObjects];
    self.sending = NO;
    [self.session invalidateAndCancel];
    self.session = nil;
    self.serverURL = nil;
    self.token = nil;
}

- (void)dealloc {
    [self.session invalidateAndCancel];
}
@end
