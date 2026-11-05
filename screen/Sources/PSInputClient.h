#import <Foundation/Foundation.h>

typedef void (^PSInputFailureHandler)(NSString *message);

@interface PSInputClient : NSObject
@property (nonatomic, copy) PSInputFailureHandler failureHandler;
- (void)configureWithServerURL:(NSString *)serverURL token:(NSString *)token enabled:(BOOL)enabled;
- (void)sendEvent:(NSDictionary *)event;
- (void)cancel;
@end
