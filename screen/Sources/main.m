#import <UIKit/UIKit.h>
#import "AppDelegate.h"
#import "PSLogger.h"

int main(int argc, char *argv[]) {
    @autoreleasepool {
        PSInstallCrashHandlers();
        PSLog(@"=== ExtendScreen Launching (pid %d) ===", getpid());
        return UIApplicationMain(argc, argv, nil, NSStringFromClass([AppDelegate class]));
    }
}
