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
@end
