#import <Foundation/Foundation.h>
#import <Security/Security.h>

@interface PSKeychainStore : NSObject
+ (BOOL)saveDeviceToken:(NSString *)token;
+ (NSString *)deviceTokenWithStatus:(OSStatus *)status;
+ (OSStatus)deviceTokenPresenceStatus;
+ (BOOL)removeDeviceToken;

+ (BOOL)saveCableToken:(NSString *)token;
+ (NSString *)cableTokenWithStatus:(OSStatus *)status;
+ (OSStatus)cableTokenPresenceStatus;
+ (BOOL)removeCableToken;
@end
