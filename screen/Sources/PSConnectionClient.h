#import <Foundation/Foundation.h>

typedef void (^PSConnectionCompletion)(BOOL success, NSString *message);

@interface PSConnectionClient : NSObject
- (void)testServerURL:(NSString *)serverURL completion:(PSConnectionCompletion)completion;
- (void)pairWithCode:(NSString *)code serverURL:(NSString *)serverURL completion:(PSConnectionCompletion)completion;
- (void)cancel;
@end
