#import <UIKit/UIKit.h>

@class PSInputClient;

@interface PSKeyboardViewController : UIViewController
@property (nonatomic, copy) void (^eventSender)(NSDictionary *event);
@property (nonatomic, strong) id inputClient;
@end
