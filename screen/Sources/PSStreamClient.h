#import <Foundation/Foundation.h>

typedef void (^PSStreamFrameHandler)(NSData *jpeg, NSUInteger sequence);
typedef void (^PSStreamStatusHandler)(BOOL streaming, NSString *message, NSInteger httpStatus);

@interface PSStreamClient : NSObject
@property (nonatomic, copy) PSStreamFrameHandler frameHandler;
@property (nonatomic, copy) PSStreamStatusHandler statusHandler;
- (void)startWithServerURL:(NSString *)serverURL token:(NSString *)token fps:(NSUInteger)fps;
- (void)stop;
- (void)sendACK:(NSUInteger)sequence;
- (void)sendInputEvent:(NSDictionary *)event;
@end
