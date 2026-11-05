#import <Foundation/Foundation.h>

@class PSCableServer;

@protocol PSCableServerDelegate <NSObject>
- (void)cableServer:(PSCableServer *)server status:(NSString *)status streaming:(BOOL)streaming;
- (void)cableServer:(PSCableServer *)server frame:(NSData *)jpeg;
@end

/* Loopback-only listener used by USB cable mode.  The PC connects through
 * usbmuxd to 127.0.0.1:8765 on the iPad, sends length-prefixed JPEG frames,
 * and receives newline-delimited JSON input events back on the same socket. */
@interface PSCableServer : NSObject
@property (nonatomic, weak) id<PSCableServerDelegate> delegate;
@property (nonatomic, readonly, getter=isRunning) BOOL running;
- (BOOL)startOnPort:(uint16_t)port;
- (void)stop;
- (void)sendEvent:(NSDictionary *)event;
@end
