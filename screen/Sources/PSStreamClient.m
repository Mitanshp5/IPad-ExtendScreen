#import "PSStreamClient.h"
#import "PSScreenPolicy.h"
#import <sys/socket.h>
#import <netinet/in.h>
#import <netinet/tcp.h>
#import <arpa/inet.h>
#import <netdb.h>
#import <unistd.h>
#import <fcntl.h>
#import <errno.h>

static const NSUInteger PSMaximumFrameBytes = 8 * 1024 * 1024;

@interface PSStreamClient ()
@property (nonatomic, strong) dispatch_queue_t netQueue;
@property (nonatomic, strong) NSLock *sendLock;
@property (nonatomic) int sockFD;
@property (nonatomic) BOOL stopped;
@end

@implementation PSStreamClient

- (instancetype)init {
    self = [super init];
    if (self) {
        _netQueue = dispatch_queue_create("local.extendscreen.rawstream", DISPATCH_QUEUE_SERIAL);
        _sendLock = [[NSLock alloc] init];
        _sockFD = -1;
        _stopped = YES;
    }
    return self;
}

- (void)report:(BOOL)streaming message:(NSString *)message status:(NSInteger)status {
    PSStreamStatusHandler handler = self.statusHandler;
    if (!handler) return;
    dispatch_async(dispatch_get_main_queue(), ^{ handler(streaming, message, status); });
}

- (void)stop {
    [self.sendLock lock];
    self.stopped = YES;
    int fd = self.sockFD;
    self.sockFD = -1;
    [self.sendLock unlock];
    if (fd >= 0) {
        shutdown(fd, SHUT_RDWR);
        close(fd);
    }
}

- (void)sendACK:(NSUInteger)sequence {
    [self.sendLock lock];
    if (self.sockFD >= 0 && !self.stopped) {
        char buf[32];
        int len = snprintf(buf, sizeof(buf), "ACK %lu\n", (unsigned long)sequence);
        send(self.sockFD, buf, (size_t)len, 0);
    }
    [self.sendLock unlock];
}

- (void)sendInputEvent:(NSDictionary *)event {
    if (!event) return;
    NSData *json = [NSJSONSerialization dataWithJSONObject:event options:0 error:NULL];
    if (!json || json.length > 2048) return;
    [self.sendLock lock];
    if (self.sockFD >= 0 && !self.stopped) {
        send(self.sockFD, json.bytes, json.length, 0);
        send(self.sockFD, "\n", 1, 0);
    }
    [self.sendLock unlock];
}

static BOOL ReadExact(int fd, void *buf, size_t length) {
    uint8_t *ptr = (uint8_t *)buf;
    size_t total = 0;
    while (total < length) {
        ssize_t n = recv(fd, ptr + total, length - total, 0);
        if (n <= 0) {
            if (n < 0 && errno == EINTR) continue;
            return NO;
        }
        total += (size_t)n;
    }
    return YES;
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

    NSString *host = parts.host;
    uint16_t port = parts.port ? (uint16_t)parts.port.unsignedShortValue : 8644;
    NSString *tokenCopy = [token copy];

    dispatch_async(self.netQueue, ^{
        [self stop];
        self.stopped = NO;

        // Resolve host
        struct addrinfo hints, *res = NULL;
        memset(&hints, 0, sizeof(hints));
        hints.ai_family = AF_INET;
        hints.ai_socktype = SOCK_STREAM;
        char portStr[16];
        snprintf(portStr, sizeof(portStr), "%u", port);

        if (getaddrinfo(host.UTF8String, portStr, &hints, &res) != 0 || !res) {
            [self report:NO message:@"Could not resolve PC host address." status:0];
            return;
        }

        int fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
        if (fd < 0) {
            freeaddrinfo(res);
            [self report:NO message:@"Could not create socket." status:0];
            return;
        }

        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));

        struct timeval tv = { .tv_sec = 6, .tv_usec = 0 };
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

        if (connect(fd, res->ai_addr, res->ai_addrlen) < 0) {
            close(fd);
            freeaddrinfo(res);
            [self report:NO message:@"Could not connect to PC host." status:0];
            return;
        }
        freeaddrinfo(res);

        [self.sendLock lock];
        self.sockFD = fd;
        [self.sendLock unlock];

        // Send HTTP GET request to negotiate the raw stream protocol
        char req[512];
        int reqLen = snprintf(req, sizeof(req),
                              "GET /raw HTTP/1.1\r\n"
                              "Host: %s:%u\r\n"
                              "Authorization: Bearer %s\r\n"
                              "Connection: close\r\n\r\n",
                              host.UTF8String, port, tokenCopy.UTF8String);
        if (send(fd, req, (size_t)reqLen, 0) != reqLen) {
            close(fd);
            [self report:NO message:@"Failed to send stream handshake." status:0];
            return;
        }

        // Read response headers until \r\n\r\n
        char headerBuf[2048];
        size_t hLen = 0;
        BOOL foundHeader = NO;
        while (hLen < sizeof(headerBuf) - 1) {
            char ch = 0;
            ssize_t r = recv(fd, &ch, 1, 0);
            if (r <= 0) break;
            headerBuf[hLen++] = ch;
            headerBuf[hLen] = '\0';
            if (hLen >= 4 &&
                headerBuf[hLen - 4] == '\r' && headerBuf[hLen - 3] == '\n' &&
                headerBuf[hLen - 2] == '\r' && headerBuf[hLen - 1] == '\n') {
                foundHeader = YES;
                break;
            }
        }

        if (!foundHeader) {
            close(fd);
            [self report:NO message:@"Stream handshake response timed out." status:0];
            return;
        }

        if (!strstr(headerBuf, "200")) {
            close(fd);
            NSInteger status = 500;
            if (strstr(headerBuf, "401")) status = 401;
            else if (strstr(headerBuf, "403")) status = 403;
            else if (strstr(headerBuf, "503")) status = 503;
            [self report:NO message:@"The host rejected the stream connection." status:status];
            return;
        }

        // Read 16-byte binary header: EXTSCR01 (8 bytes) + width (4 bytes) + height (4 bytes)
        uint8_t magicBuf[16];
        if (!ReadExact(fd, magicBuf, 16) || memcmp(magicBuf, "EXTSCR01", 8) != 0) {
            close(fd);
            [self report:NO message:@"Invalid stream header from PC host." status:0];
            return;
        }

        [self report:YES message:@"" status:200];

        // Frame receive loop
        // Each frame: [4 bytes sequence (uint32_be)][4 bytes length (uint32_be)][payload bytes]
        while (!self.stopped) {
            uint32_t netSeq = 0, netLen = 0;
            if (!ReadExact(fd, &netSeq, 4) || !ReadExact(fd, &netLen, 4)) {
                break;
            }
            uint32_t seq = ntohl(netSeq);
            uint32_t len = ntohl(netLen);
            if (len == 0 || len > PSMaximumFrameBytes) {
                break;
            }

            NSMutableData *frameData = [NSMutableData dataWithLength:len];
            if (!ReadExact(fd, frameData.mutableBytes, len)) {
                break;
            }

            PSStreamFrameHandler handler = self.frameHandler;
            if (handler && !self.stopped) {
                handler(frameData, (NSUInteger)seq);
            }
        }

        close(fd);
        [self.sendLock lock];
        self.sockFD = -1;
        BOOL stopped = self.stopped;
        [self.sendLock unlock];

        if (!stopped) {
            [self report:NO message:@"Screen stream disconnected. Reconnecting..." status:0];
        }
    });
}

- (void)dealloc {
    [self stop];
}

@end
