#import "PSConnectionClient.h"
#import "PSKeychainStore.h"
#import "PSScreenPolicy.h"

static const NSUInteger PSMaximumHealthBytes = 8192;

@interface PSConnectionClient ()
@property (nonatomic, strong) NSURLSession *session;
@property (nonatomic, strong) NSURLSessionDataTask *task;
@property (nonatomic, strong) NSMutableData *received;
@property (nonatomic, copy) PSConnectionCompletion completion;
@property (nonatomic, copy) NSString *expectedVersion;
@property (nonatomic) NSInteger status;
@property (nonatomic) BOOL pairing;
@property (nonatomic) BOOL finished;
@end

@implementation PSConnectionClient
- (void)finish:(BOOL)success message:(NSString *)message {
    if (self.finished) return;
    self.finished = YES;
    PSConnectionCompletion callback = self.completion;
    self.completion = nil;
    [self.task cancel];
    [self.session invalidateAndCancel];
    self.task = nil;
    self.session = nil;
    self.received = nil;
    if (callback) callback(success, message);
}

- (void)cancel {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self cancel]; });
        return;
    }
    if (self.finished) return;
    self.finished = YES;
    [self.task cancel];
    [self.session invalidateAndCancel];
    self.task = nil;
    self.session = nil;
    self.received = nil;
    self.completion = nil;
}

- (BOOL)prepareOrigin:(NSString *)serverURL plain:(BOOL *)plain result:(NSURLComponents **)result {
    NSURLComponents *parts = [NSURLComponents componentsWithString:
        [serverURL stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]];
    if (!parts || !parts.host.length) return NO;
    NSString *scheme = parts.scheme.lowercaseString;
    BOOL isPlain = [scheme isEqualToString:@"http"];
    long long port = parts.port ? parts.port.longLongValue : (isPlain ? 80 : 443);
    unsigned safePort = port > 0 && port <= 65535 ? (unsigned)port : 0;
    BOOL userInfo = parts.user != nil || parts.password != nil;
    BOOL valid = isPlain ?
        PSPlainOriginIsSafe(scheme.UTF8String, parts.host.UTF8String, safePort,
                            parts.path.UTF8String, userInfo, parts.query != nil, parts.fragment != nil) :
        PSOriginIsSafe(scheme.UTF8String, parts.host.UTF8String, safePort,
                       parts.path.UTF8String, userInfo, parts.query != nil, parts.fragment != nil);
    if (!valid || !parts.URL) return NO;
    if (plain) *plain = isPlain;
    if (result) *result = parts;
    return YES;
}

- (void)beginRequestURL:(NSURL *)url method:(NSString *)method body:(NSData *)body {
    NSURLSessionConfiguration *configuration = [NSURLSessionConfiguration ephemeralSessionConfiguration];
    configuration.URLCache = nil;
    configuration.URLCredentialStorage = nil;
    configuration.HTTPCookieStorage = nil;
    configuration.HTTPShouldSetCookies = NO;
    configuration.requestCachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
    configuration.timeoutIntervalForRequest = 10;
    configuration.timeoutIntervalForResource = 15;
    configuration.HTTPMaximumConnectionsPerHost = 1;
    configuration.allowsCellularAccess = NO;
    self.session = [NSURLSession sessionWithConfiguration:configuration delegate:(id<NSURLSessionDelegate>)self delegateQueue:[NSOperationQueue mainQueue]];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    request.HTTPMethod = method;
    [request setValue:@"application/json" forHTTPHeaderField:@"Accept"];
    [request setValue:@"no-store" forHTTPHeaderField:@"Cache-Control"];
    if (body) {
        request.HTTPBody = body;
        [request setValue:@"application/json; charset=utf-8" forHTTPHeaderField:@"Content-Type"];
    }
    self.received = [NSMutableData data];
    self.status = 0;
    self.task = [self.session dataTaskWithRequest:request];
    [self.task resume];
}

- (void)loadExpectedVersion {
    NSDictionary *defaults = [NSDictionary dictionaryWithContentsOfFile:
        [[NSBundle mainBundle] pathForResource:@"ScreenDefaults" ofType:@"plist"]];
    self.expectedVersion = [defaults[@"ExpectedVersion"] isKindOfClass:[NSString class]] ?
        defaults[@"ExpectedVersion"] : nil;
}

- (void)testServerURL:(NSString *)serverURL completion:(PSConnectionCompletion)completion {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self testServerURL:serverURL completion:completion]; });
        return;
    }
    [self cancel];
    self.finished = NO;
    self.pairing = NO;
    self.completion = completion;
    [self loadExpectedVersion];
    if (!self.expectedVersion.length) {
        [self finish:NO message:@"The bundled host contract is missing. Reinstall ExtendScreen."];
        return;
    }
    NSURLComponents *parts = nil;
    if (![self prepareOrigin:serverURL plain:NULL result:&parts]) {
        [self finish:NO message:@"Use the host origin only, such as http://192.168.0.108:8644. HTTP requires a private numeric LAN address."];
        return;
    }
    parts.path = @"/health";
    parts.query = nil;
    parts.fragment = nil;
    [self beginRequestURL:parts.URL method:@"GET" body:nil];
}

- (void)pairWithCode:(NSString *)code serverURL:(NSString *)serverURL completion:(PSConnectionCompletion)completion {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self pairWithCode:code serverURL:serverURL completion:completion]; });
        return;
    }
    [self cancel];
    self.finished = NO;
    self.pairing = YES;
    self.completion = completion;
    [self loadExpectedVersion];
    if (!self.expectedVersion.length) {
        [self finish:NO message:@"The bundled host contract is missing. Reinstall ExtendScreen."];
        return;
    }
    NSString *trimmed = [code stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (!PSPairingCodeIsSafe(trimmed.UTF8String)) {
        [self finish:NO message:@"Enter the eight-digit code printed by the PC host."];
        return;
    }
    NSURLComponents *parts = nil;
    if (![self prepareOrigin:serverURL plain:NULL result:&parts]) {
        [self finish:NO message:@"Use the host origin only, such as http://192.168.0.108:8644. HTTP requires a private numeric LAN address."];
        return;
    }
    parts.path = @"/pair";
    parts.query = nil;
    parts.fragment = nil;
    NSDictionary *payload = @{@"code": trimmed, @"name": @"iPad mini"};
    NSData *body = [NSJSONSerialization dataWithJSONObject:payload options:0 error:NULL];
    [self beginRequestURL:parts.URL method:@"POST" body:body];
}

- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
    willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request
    completionHandler:(void (^)(NSURLRequest *))completionHandler {
    completionHandler(nil);
    [self finish:NO message:@"The host requested a redirect. It was refused; enter the direct host address."];
}

- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
    didReceiveChallenge:(NSURLAuthenticationChallenge *)challenge
    completionHandler:(void (^)(NSURLSessionAuthChallengeDisposition, NSURLCredential *))completionHandler {
    if (!self.finished && [challenge.protectionSpace.authenticationMethod isEqualToString:NSURLAuthenticationMethodServerTrust])
        completionHandler(NSURLSessionAuthChallengePerformDefaultHandling, nil);
    else completionHandler(NSURLSessionAuthChallengeCancelAuthenticationChallenge, nil);
}

- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task
    didReceiveResponse:(NSURLResponse *)response
    completionHandler:(void (^)(NSURLSessionResponseDisposition))completionHandler {
    NSHTTPURLResponse *http = [response isKindOfClass:[NSHTTPURLResponse class]] ? (NSHTTPURLResponse *)response : nil;
    self.status = http.statusCode;
    if (self.finished || !http || ![response.MIMEType.lowercaseString isEqualToString:@"application/json"] ||
        response.expectedContentLength > (long long)PSMaximumHealthBytes) {
        completionHandler(NSURLSessionResponseCancel);
        [self finish:NO message:@"The host returned an unsupported or oversized response."];
    } else completionHandler(NSURLSessionResponseAllow);
}

- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:(NSData *)data {
    if (self.finished) return;
    if (data.length > PSMaximumHealthBytes - self.received.length) {
        [self finish:NO message:@"The host response exceeded the safe size limit."];
        return;
    }
    [self.received appendData:data];
}

- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:(NSError *)error {
    if (self.finished) return;
    if (error) {
        NSString *message = @"Could not reach the PC host. Check Wi-Fi and the address, then try again.";
        if (error.code == NSURLErrorTimedOut) message = @"The PC host timed out. Check that screen-host.py is still running.";
        if (error.code == NSURLErrorAppTransportSecurityRequiresSecureConnection)
            message = @"iOS blocked this HTTP connection. Do not disable transport security globally.";
        [self finish:NO message:message];
        return;
    }
    id decoded = [NSJSONSerialization JSONObjectWithData:self.received options:0 error:NULL];
    NSDictionary *json = [decoded isKindOfClass:[NSDictionary class]] ? decoded : nil;
    if (!json) {
        [self finish:NO message:@"The host returned malformed JSON. Its raw response was not displayed."];
        return;
    }
    if (self.status != 200) {
        if (self.status == 401 || self.status == 403)
            [self finish:NO message:@"The host rejected this iPad. Restart the host for a fresh pairing code and pair again."];
        else if (self.status == 429)
            [self finish:NO message:@"Too many attempts. Wait a minute and try again."];
        else
            [self finish:NO message:[NSString stringWithFormat:@"The host returned HTTP %ld.", (long)self.status]];
        return;
    }
    if (self.pairing) {
        NSString *token = [json[@"token"] isKindOfClass:[NSString class]] ? json[@"token"] : nil;
        if (!PSDeviceTokenIsSafe(token.UTF8String) || ![PSKeychainStore saveDeviceToken:token]) {
            [self finish:NO message:@"The host paired, but this iPad could not save the credential securely."];
            return;
        }
        [self finish:YES message:@"Paired. The screen should connect in a moment."];
        return;
    }
    if (![json[@"app"] isEqual:@"extendscreen"] || ![json[@"status"] isEqual:@"ok"] ||
        ![json[@"version"] isEqual:self.expectedVersion]) {
        [self finish:NO message:@"Unexpected host identity or version. Update ExtendScreen and the host script together."];
        return;
    }
    [self finish:YES message:[NSString stringWithFormat:@"Host reachable at %ldx%ld, %@ fps. Input: %@.",
        (long)[json[@"width"] integerValue], (long)[json[@"height"] integerValue],
        json[@"fps"] ?: @"?", [json[@"input"] boolValue] ? @"on" : @"off"]];
}
@end
