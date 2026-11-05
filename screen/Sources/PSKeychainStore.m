#import "PSKeychainStore.h"
#import "PSScreenPolicy.h"

static NSString * const PSService = @"local.extendscreen";
static NSString * const PSDeviceAccount = @"device-access-token";
static NSString * const PSCableAccount = @"cable-access-token";

@implementation PSKeychainStore
+ (NSMutableDictionary *)queryForAccount:(NSString *)account {
    return [@{(__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
              (__bridge id)kSecAttrService: PSService,
              (__bridge id)kSecAttrAccount: account} mutableCopy];
}

+ (BOOL)saveToken:(NSString *)token account:(NSString *)account {
    if (!PSDeviceTokenIsSafe(token.UTF8String)) return NO;
    NSMutableDictionary *query = [self queryForAccount:account];
    NSDictionary *values = @{(__bridge id)kSecValueData:[token dataUsingEncoding:NSUTF8StringEncoding],
        (__bridge id)kSecAttrAccessible:(__bridge id)kSecAttrAccessibleWhenUnlockedThisDeviceOnly};
    OSStatus result = SecItemUpdate((__bridge CFDictionaryRef)query, (__bridge CFDictionaryRef)values);
    if (result == errSecItemNotFound) {
        [query addEntriesFromDictionary:values];
        result = SecItemAdd((__bridge CFDictionaryRef)query, NULL);
    }
    return result == errSecSuccess;
}

+ (NSString *)tokenWithStatus:(OSStatus *)status account:(NSString *)account {
    NSMutableDictionary *query = [self queryForAccount:account];
    query[(__bridge id)kSecReturnData] = @YES;
    query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    CFTypeRef found = NULL;
    OSStatus result = SecItemCopyMatching((__bridge CFDictionaryRef)query, &found);
    id value = CFBridgingRelease(found);
    NSString *token = result == errSecSuccess && [value isKindOfClass:[NSData class]] ?
                      [[NSString alloc] initWithData:value encoding:NSUTF8StringEncoding] : nil;
    if (result == errSecSuccess && !PSDeviceTokenIsSafe(token.UTF8String)) result = errSecDecode;
    if (status) *status = result;
    return result == errSecSuccess ? token : nil;
}

+ (OSStatus)presenceStatusForAccount:(NSString *)account {
    NSMutableDictionary *query = [self queryForAccount:account];
    query[(__bridge id)kSecReturnAttributes] = @YES;
    query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    return SecItemCopyMatching((__bridge CFDictionaryRef)query, NULL);
}

+ (BOOL)removeAccount:(NSString *)account {
    OSStatus result = SecItemDelete((__bridge CFDictionaryRef)[self queryForAccount:account]);
    return result == errSecSuccess || result == errSecItemNotFound;
}

+ (BOOL)saveDeviceToken:(NSString *)token {
    return [self saveToken:token account:PSDeviceAccount];
}
+ (NSString *)deviceTokenWithStatus:(OSStatus *)status {
    return [self tokenWithStatus:status account:PSDeviceAccount];
}
+ (OSStatus)deviceTokenPresenceStatus {
    return [self presenceStatusForAccount:PSDeviceAccount];
}
+ (BOOL)removeDeviceToken {
    return [self removeAccount:PSDeviceAccount];
}

+ (BOOL)saveCableToken:(NSString *)token {
    return [self saveToken:token account:PSCableAccount];
}
+ (NSString *)cableTokenWithStatus:(OSStatus *)status {
    return [self tokenWithStatus:status account:PSCableAccount];
}
+ (OSStatus)cableTokenPresenceStatus {
    return [self presenceStatusForAccount:PSCableAccount];
}
+ (BOOL)removeCableToken {
    return [self removeAccount:PSCableAccount];
}
@end
