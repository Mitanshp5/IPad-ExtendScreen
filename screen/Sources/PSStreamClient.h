#import <Foundation/Foundation.h>

typedef void (^PSStreamFrameHandler)(NSData *jpeg, NSUInteger sequence);
typedef void (^PSStreamStatusHandler)(BOOL streaming, NSString *message, NSInteger httpStatus);
typedef void (^PSStreamCursorHandler)(NSDictionary *cursor);

@interface PSStreamClient : NSObject
@property (nonatomic, copy) PSStreamFrameHandler frameHandler;
@property (nonatomic, copy) PSStreamStatusHandler statusHandler;
@property (nonatomic, copy) PSStreamCursorHandler cursorHandler;
- (void)startWithServerURL:(NSString *)serverURL token:(NSString *)token fps:(NSUInteger)fps;
- (void)stop;
- (void)sendACK:(NSUInteger)sequence;
- (void)sendInputEvent:(NSDictionary *)event;
@end
