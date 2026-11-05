#import <Foundation/Foundation.h>

static NSString * const PSServerURLKey = @"ExtendScreenServerURL";
static NSString * const PSFPSKey = @"ExtendScreenFPS";
static NSString * const PSInputKey = @"ExtendScreenInputEnabled";
static NSString * const PSShowFPSKey = @"ExtendScreenShowFPS";
static NSString * const PSModeKey = @"ExtendScreenConnectionMode";

static const NSInteger PSModeWiFi = 0;
static const NSInteger PSModeCable = 1;
static const uint16_t PSCableListenPort = 8765;
