#import "PSCableServer.h"

#import <arpa/inet.h>
#import <errno.h>
#import <netinet/in.h>
#import <netinet/tcp.h>
#import <sys/socket.h>
#import <sys/time.h>
#import <unistd.h>

static const NSUInteger PSCableMaximumLine = 8192;
static const NSUInteger PSCableMaximumFrame = 4 * 1024 * 1024;

@implementation PSCableServer {
    int _listenFD;
    int _clientFD;
    BOOL _running;
    BOOL _hostAcceptsInput;
}

- (instancetype)init {
    self = [super init];
    if (self) {
        _listenFD = -1;
        _clientFD = -1;
        _hostAcceptsInput = YES;
    }
    return self;
}

- (BOOL)isRunning {
    @synchronized (self) {
        return _running;
    }
}

- (BOOL)startOnPort:(uint16_t)port {
    if (![NSThread isMainThread]) {
        __block BOOL result = NO;
        dispatch_sync(dispatch_get_main_queue(), ^{
            result = [self startOnPort:port];
        });
        return result;
    }
    [self stop];

    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return NO;
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_len = sizeof(address);
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(fd, (struct sockaddr *)&address, sizeof(address)) != 0) {
        close(fd);
        return NO;
    }
    if (listen(fd, 2) != 0) {
        close(fd);
        return NO;
    }
    @synchronized (self) {
        _listenFD = fd;
        _clientFD = -1;
        _running = YES;
    }
    [NSThread detachNewThreadSelector:@selector(acceptLoop) toTarget:self withObject:nil];
    [self reportStatus:[NSString stringWithFormat:@"USB cable listener ready on 127.0.0.1:%u. Waiting for the PC...",
                        (unsigned)port] streaming:NO];
    return YES;
}

- (void)stop {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self stop]; });
        return;
    }
    int listenFD = -1;
    int clientFD = -1;
    @synchronized (self) {
        if (!_running && _listenFD < 0 && _clientFD < 0) return;
        _running = NO;
        listenFD = _listenFD;
        clientFD = _clientFD;
        _listenFD = -1;
        _clientFD = -1;
    }
    if (listenFD >= 0) {
        shutdown(listenFD, SHUT_RDWR);
        close(listenFD);
    }
    if (clientFD >= 0) shutdown(clientFD, SHUT_RDWR);
}

- (void)acceptLoop {
    @autoreleasepool {
        while (YES) {
            int listenFD = -1;
            @synchronized (self) {
                if (!_running) break;
                listenFD = _listenFD;
            }
            if (listenFD < 0) break;
            int client = accept(listenFD, NULL, NULL);
            if (client < 0) {
                @synchronized (self) {
                    if (!_running) break;
                }
                if (errno == EINTR) continue;
                [NSThread sleepForTimeInterval:0.15];
                continue;
            }
            int one = 1;
            setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));
            setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
            @synchronized (self) {
                if (!_running) {
                    close(client);
                    break;
                }
                if (_clientFD >= 0) {
                    close(client);
                    continue;
                }
                _clientFD = client;
                _hostAcceptsInput = YES;
            }
            [self reportStatus:@"USB cable connected..." streaming:NO];
            [self handleClient:client];
            @synchronized (self) {
                if (_clientFD == client) _clientFD = -1;
            }
            close(client);
            @synchronized (self) {
                if (!_running) break;
            }
            [self reportStatus:@"USB cable disconnected. Waiting for the PC..." streaming:NO];
        }
    }
}

- (void)handleClient:(int)fd {
    struct timeval timeout;
    timeout.tv_sec = 60;
    timeout.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    NSString *magic = [self readLineFrom:fd maximum:16];
    if (![magic isEqualToString:@"ESP1"]) {
        [self sendLine:@{@"type": @"error", @"message": @"unsupported cable protocol"} to:fd];
        return;
    }
    NSString *line = [self readLineFrom:fd maximum:PSCableMaximumLine];
    if (!line.length) return;
    NSData *lineData = [line dataUsingEncoding:NSUTF8StringEncoding];
    id decoded = [NSJSONSerialization JSONObjectWithData:lineData options:0 error:NULL];
    NSDictionary *hello = [decoded isKindOfClass:[NSDictionary class]] ? decoded : nil;
    if (!hello || ![hello[@"type"] isEqual:@"hello"] ||
        (hello[@"version"] && [hello[@"version"] integerValue] != 1)) {
        [self sendLine:@{@"type": @"error", @"message": @"invalid cable handshake"} to:fd];
        return;
    }

    @synchronized (self) {
        _hostAcceptsInput = hello[@"input"] ? [hello[@"input"] boolValue] : YES;
    }
    if (![self sendLine:@{@"type": @"welcome", @"input": @YES, @"version": @"1.0.7"} to:fd]) return;
    [self reportStatus:@"USB cable connected. Streaming." streaming:YES];

    while (YES) {
        @autoreleasepool {
            uint32_t networkLength = 0;
            if (![self readAllFrom:fd buffer:&networkLength length:sizeof(networkLength)]) break;
            uint32_t length = ntohl(networkLength);
            if (length < 4 || length > PSCableMaximumFrame) break;
            NSMutableData *jpeg = [NSMutableData dataWithLength:length];
            if (!jpeg || ![self readAllFrom:fd buffer:jpeg.mutableBytes length:length]) break;
            @synchronized (self) {
                if (!_running) break;
            }
            [self reportFrame:jpeg];
        }
    }
}

- (NSString *)readLineFrom:(int)fd maximum:(NSUInteger)maximum {
    NSMutableData *data = [NSMutableData data];
    while (data.length < maximum) {
        unsigned char byte = 0;
        ssize_t received = recv(fd, &byte, 1, 0);
        if (received == 0) return nil;
        if (received < 0) {
            if (errno == EINTR) continue;
            return nil;
        }
        if (byte == '\n') {
            return [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
        }
        if (byte != '\r') [data appendBytes:&byte length:1];
    }
    return nil;
}

- (BOOL)readAllFrom:(int)fd buffer:(void *)buffer length:(NSUInteger)length {
    unsigned char *bytes = (unsigned char *)buffer;
    NSUInteger offset = 0;
    while (offset < length) {
        ssize_t received = recv(fd, bytes + offset, length - offset, 0);
        if (received == 0) return NO;
        if (received < 0) {
            if (errno == EINTR) continue;
            return NO;
        }
        offset += (NSUInteger)received;
    }
    return YES;
}

- (BOOL)sendAllTo:(int)fd buffer:(const void *)buffer length:(NSUInteger)length {
    const unsigned char *bytes = (const unsigned char *)buffer;
    NSUInteger offset = 0;
    while (offset < length) {
        ssize_t sent = send(fd, bytes + offset, length - offset, 0);
        if (sent < 0) {
            if (errno == EINTR) continue;
            return NO;
        }
        if (sent == 0) return NO;
        offset += (NSUInteger)sent;
    }
    return YES;
}

- (BOOL)sendLine:(NSDictionary *)payload to:(int)fd {
    NSData *json = [NSJSONSerialization dataWithJSONObject:payload options:0 error:NULL];
    if (!json || json.length > PSCableMaximumLine) return NO;
    NSMutableData *data = [json mutableCopy];
    [data appendBytes:"\n" length:1];
    return [self sendAllTo:fd buffer:data.bytes length:data.length];
}

- (void)sendEvent:(NSDictionary *)event {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self sendEvent:event]; });
        return;
    }
    if (!event) return;
    int fd = -1;
    @synchronized (self) {
        if (!_running || !_hostAcceptsInput) return;
        fd = _clientFD;
    }
    if (fd < 0) return;
    NSData *json = [NSJSONSerialization dataWithJSONObject:event options:0 error:NULL];
    if (!json || json.length > PSCableMaximumLine) return;
    NSMutableData *data = [json mutableCopy];
    [data appendBytes:"\n" length:1];
    @synchronized (self) {
        if (_clientFD == fd && _running) [self sendAllTo:fd buffer:data.bytes length:data.length];
    }
}

- (void)reportStatus:(NSString *)status streaming:(BOOL)streaming {
    dispatch_async(dispatch_get_main_queue(), ^{
        id<PSCableServerDelegate> delegate = self.delegate;
        if (delegate) [delegate cableServer:self status:status streaming:streaming];
    });
}

- (void)reportFrame:(NSData *)frame {
    id<PSCableServerDelegate> delegate = self.delegate;
    if (delegate) [delegate cableServer:self frame:frame];
}

- (void)dealloc {
    [self stop];
}
@end
