#import "PSLogger.h"
#import <Foundation/Foundation.h>
#import <signal.h>
#import <unistd.h>
#import <string.h>

static NSFileHandle *sLogHandle = nil;
static dispatch_once_t sLogOnceToken = 0;

static void PSInitLogFile(void) {
    dispatch_once(&sLogOnceToken, ^{
        NSString *path = @"/tmp/ExtendScreen.log";
        [[NSFileManager defaultManager] createFileAtPath:path contents:nil attributes:nil];
        sLogHandle = [NSFileHandle fileHandleForWritingAtPath:path];
        if (!sLogHandle) {
            NSArray *dirs = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES);
            if (dirs.count) {
                path = [dirs[0] stringByAppendingPathComponent:@"ExtendScreen.log"];
                [[NSFileManager defaultManager] createFileAtPath:path contents:nil attributes:nil];
                sLogHandle = [NSFileHandle fileHandleForWritingAtPath:path];
            }
        }
    });
}

void PSLog(NSString *format, ...) {
    va_list args;
    va_start(args, format);
    NSString *message = [[NSString alloc] initWithFormat:format arguments:args];
    va_end(args);

    NSLog(@"[ExtendScreen] %@", message);

    PSInitLogFile();

    if (sLogHandle) {
        @synchronized (sLogHandle) {
            NSDateFormatter *df = [NSDateFormatter new];
            df.dateFormat = @"HH:mm:ss.SSS";
            NSString *entry = [NSString stringWithFormat:@"%@ %@\n", [df stringFromDate:[NSDate date]], message];
            [sLogHandle seekToEndOfFile];
            [sLogHandle writeData:[entry dataUsingEncoding:NSUTF8StringEncoding]];
            [sLogHandle synchronizeFile];
        }
    }
}

static void PSUncaughtExceptionHandler(NSException *exception) {
    PSLog(@"[CRASH] FATAL UNCAUGHT EXCEPTION: name=%@ reason=%@", exception.name, exception.reason);
    PSLog(@"[CRASH] UserInfo: %@", exception.userInfo);
    PSLog(@"[CRASH] Call stack symbols:\n%@", [exception.callStackSymbols componentsJoinedByString:@"\n"]);
}

static void PSSignalHandler(int sig) {
    const char *name = strsignal(sig);
    PSLog(@"[CRASH] FATAL SIGNAL RECEIVED: %d (%s)", sig, name ? name : "unknown");
    PSLog(@"[CRASH] Thread call stack symbols:\n%@", [[NSThread callStackSymbols] componentsJoinedByString:@"\n"]);
    signal(sig, SIG_DFL);
    kill(getpid(), sig);
}

void PSInstallCrashHandlers(void) {
    // Ignore SIGPIPE so broken sockets on Wi-Fi do not abruptly kill the app
    signal(SIGPIPE, SIG_IGN);

    NSSetUncaughtExceptionHandler(&PSUncaughtExceptionHandler);

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = PSSignalHandler;
    sigemptyset(&sa.sa_mask);

    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
    sigaction(SIGABRT, &sa, NULL);
    sigaction(SIGILL, &sa, NULL);
    sigaction(SIGFPE, &sa, NULL);

    PSLog(@"Crash handlers and signal masking (SIGPIPE) initialized successfully.");
}
