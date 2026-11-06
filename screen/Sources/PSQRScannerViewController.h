#import <UIKit/UIKit.h>

typedef void (^PSQRScanResultHandler)(NSString *serverURL, NSString *pairingCode);

@interface PSQRScannerViewController : UIViewController
@property (nonatomic, copy) PSQRScanResultHandler scanHandler;
@end
