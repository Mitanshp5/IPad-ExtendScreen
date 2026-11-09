#import <Foundation/Foundation.h>

#ifdef __cplusplus
extern "C" {
#endif

void PSLog(NSString *format, ...) NS_FORMAT_FUNCTION(1, 2);
void PSInstallCrashHandlers(void);

#ifdef __cplusplus
}
#endif
