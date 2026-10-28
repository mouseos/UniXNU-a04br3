// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>

// Parse the first exact token, using XNU's space/tab argument separators.
// Return false only when absent. A malformed or out-of-range token is present
// but disables sampling, so it cannot fall back to a different process.
static inline bool live_pc_uint_arg(const char *args, const char *name, uint32_t *pid) {
    unsigned nameLen = 0;
    while (name[nameLen]) ++nameLen;
    *pid = 0;
    if (!args) return false;
    while (*args) {
        while (*args == ' ' || *args == '\t') ++args;
        if (!*args) break;
        const char *start = args;
        while (*args && *args != ' ' && *args != '\t' && *args != '=') ++args;
        const char *nameEnd = args;
        unsigned i = 0;
        while (i < nameLen && start + i < nameEnd && start[i] == name[i]) ++i;
        if (i == nameLen && start + i == nameEnd) {
            if (*args != '=') return true;
            ++args;
            if (*args < '0' || *args > '9') return true;
            uint32_t value = 0;
            while (*args && *args != ' ' && *args != '\t') {
                if (*args < '0' || *args > '9') return true;
                unsigned digit = (unsigned)(*args++ - '0');
                if (value > (INT32_MAX - digit) / 10u) return true;
                value = value * 10u + digit;
            }
            *pid = value;
            return true;
        }
        while (*args && *args != ' ' && *args != '\t') ++args;
    }
    return false;
}
static inline bool live_pc_pid_arg(const char *args, uint32_t *pid) {
    return live_pc_uint_arg(args, "a04br3-live-pc-pid", pid);
}
