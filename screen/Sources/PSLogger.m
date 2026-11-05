#import "PSLogger.h"
#import <Foundation/Foundation.h>

static NSFileHandle *sLogHandle = nil;
static dispatch_once_t sLogOnceToken = 0;

void PSLog(NSString *format, ...) {
    va_list args;
    va_start(args, format);
    NSString *message = [[NSString alloc] initWithFormat:format arguments:args];
    va_end(args);

    NSLog(@"[ExtendScreen] %@", message);

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

    if (sLogHandle) {
        @synchronized (sLogHandle) {
            NSDateFormatter *df = [NSDateFormatter new];
            df.dateFormat = @"HH:mm:ss.SSS";
            NSString *entry = [NSString stringWithFormat:@"%@ %@\n", [df stringFromDate:[NSDate date]], message];
            [sLogHandle seekToEndOfFile];
            [sLogHandle writeData:[entry dataUsingEncoding:NSUTF8StringEncoding]];
        }
    }
}
