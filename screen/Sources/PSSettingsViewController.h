#import <UIKit/UIKit.h>

@interface PSSettingsViewController : UIViewController
@property (nonatomic, copy) void (^wifiPairingForgotten)(void);
@end
