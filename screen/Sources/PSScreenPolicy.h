#ifndef PS_SCREEN_POLICY_H
#define PS_SCREEN_POLICY_H

/* Freestanding checks shared by the native client and host-side tests. */
int PSOriginIsSafe(const char *scheme, const char *host, unsigned port,
                   const char *path, int userInfo, int query, int fragment);
int PSPlainOriginIsSafe(const char *scheme, const char *host, unsigned port,
                        const char *path, int userInfo, int query, int fragment);
int PSPairingCodeIsSafe(const char *code);
int PSDeviceTokenIsSafe(const char *token);

#endif
