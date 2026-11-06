#import "AppDelegate.h"
#import "PSScreenViewController.h"

@interface PSNavigationController : UINavigationController
@end

@implementation PSNavigationController
- (UIViewController *)childViewControllerForStatusBarHidden {
    return self.topViewController;
}
@end

@implementation AppDelegate
- (BOOL)application:(UIApplication *)application
    didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
    NSDictionary *bundled = [NSDictionary dictionaryWithContentsOfFile:
        [[NSBundle mainBundle] pathForResource:@"ScreenDefaults" ofType:@"plist"]];
    if (bundled) [[NSUserDefaults standardUserDefaults] registerDefaults:bundled];

    self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
    PSScreenViewController *screen = [PSScreenViewController new];
    PSNavigationController *navigation = [[PSNavigationController alloc]
        initWithRootViewController:screen];
    navigation.navigationBar.barStyle = UIBarStyleBlack;
    navigation.navigationBar.barTintColor = [UIColor colorWithWhite:0.08 alpha:1];
    navigation.navigationBar.tintColor = [UIColor colorWithRed:0.40 green:0.86 blue:0.82 alpha:1];
    navigation.navigationBar.translucent = NO;
    self.window.rootViewController = navigation;
    [self.window makeKeyAndVisible];
    return YES;
}

- (BOOL)application:(UIApplication *)application openURL:(NSURL *)url
  sourceApplication:(NSString *)sourceApplication annotation:(id)annotation {
    if (!url) return NO;
    if ([url.scheme.lowercaseString isEqualToString:@"extendscreen"]) {
        NSURLComponents *comp = [NSURLComponents componentsWithURL:url resolvingAgainstBaseURL:NO];
        NSString *host = nil;
        NSString *code = nil;
        for (NSURLQueryItem *item in comp.queryItems) {
            if ([item.name.lowercaseString isEqualToString:@"host"]) host = item.value;
            else if ([item.name.lowercaseString isEqualToString:@"code"]) code = item.value;
        }
        if (host.length && code.length) {
            UINavigationController *nav = (UINavigationController *)self.window.rootViewController;
            if ([nav isKindOfClass:[UINavigationController class]] &&
                [nav.viewControllers.firstObject isKindOfClass:[PSScreenViewController class]]) {
                PSScreenViewController *screen = (PSScreenViewController *)nav.viewControllers.firstObject;
                [screen pairWithScannedServer:host code:code];
            }
            return YES;
        }
    }
    return NO;
}
@end
