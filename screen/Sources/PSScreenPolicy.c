#include "PSScreenPolicy.h"

/* Keep this policy freestanding: the legacy SDK stubs omit some libc
 * indirect exports.  No system-symbol replacement is linked into the app. */
static int PSEqual(const char *left, const char *right) {
    while (*left && *left == *right) { ++left; ++right; }
    return *left == *right;
}

static int PSLowerHex(unsigned char value) {
    return (value >= '0' && value <= '9') || (value >= 'a' && value <= 'f');
}

int PSPairingCodeIsSafe(const char *code) {
    if (!code) return 0;
    for (unsigned i = 0; i < 8; ++i) if (code[i] < '0' || code[i] > '9') return 0;
    return code[8] == 0;
}

int PSDeviceTokenIsSafe(const char *token) {
    if (!token) return 0;
    for (unsigned i = 0; i < 64; ++i) if (!PSLowerHex((unsigned char)token[i])) return 0;
    return token[64] == 0;
}

int PSOriginIsSafe(const char *scheme, const char *host, unsigned port,
                   const char *path, int userInfo, int query, int fragment) {
    if (!scheme || !PSEqual(scheme, "https") || !host || !*host || !port || port > 65535)
        return 0;
    if (userInfo || query || fragment || (path && *path && !PSEqual(path, "/")))
        return 0;
    for (const unsigned char *p = (const unsigned char *)host; *p; ++p)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
              (*p >= '0' && *p <= '9') || *p == '.' || *p == '-' || *p == ':'))
            return 0;
    return 1;
}

int PSPlainOriginIsSafe(const char *scheme, const char *host, unsigned port,
                        const char *path, int userInfo, int query, int fragment) {
    if (!scheme || !PSEqual(scheme, "http") ||
        !PSOriginIsSafe("https", host, port, path, userInfo, query, fragment)) return 0;
    unsigned octets[4];
    const char *cursor = host;
    for (unsigned index = 0; index < 4; ++index) {
        unsigned value = 0, digits = 0;
        const char *first = cursor;
        while (*cursor >= '0' && *cursor <= '9') {
            if (++digits > 3) return 0;
            value = value * 10 + (unsigned)(*cursor++ - '0');
        }
        if (!digits || value > 255 || (digits > 1 && *first == '0')) return 0;
        octets[index] = value;
        if (index < 3) { if (*cursor++ != '.') return 0; }
        else if (*cursor) return 0;
    }
    return octets[0] == 10 || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31) ||
           (octets[0] == 192 && octets[1] == 168);
}
