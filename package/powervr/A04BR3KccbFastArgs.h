#pragma once
#include <stddef.h>
#include <string.h>

// The ARM XNU CommandLine has 1024 bytes including NUL. Only exact 0/1
// values are accepted, and any duplicate or mixed spelling disables it.
static bool a04br3_kccb_fast_args(const char *args)
{
  if (!args) return false;
  size_t length = 0;
  while (length < 1024 && args[length]) ++length;
  if (length == 1024) return false;
  bool seen = false, enabled = false;
  for (size_t pos = 0; pos < length;) {
    while (pos < length && (args[pos] == ' ' || args[pos] == '\t')) ++pos;
    size_t start = pos;
    while (pos < length && args[pos] != ' ' && args[pos] != '\t') ++pos;
    size_t end = pos, equal = start;
    while (equal < end && args[equal] != '=') ++equal;
    size_t n = equal - start;
    bool match = (n == sizeof("a4kf") - 1 && !memcmp(args + start, "a4kf", n)) ||
                 (n == sizeof("a04br3-kccb-fast") - 1 && !memcmp(args + start, "a04br3-kccb-fast", n));
    if (!match) continue;
    if (seen || equal + 2 != end || args[equal] != '=' ||
        (args[equal + 1] != '0' && args[equal + 1] != '1')) return false;
    seen = true;
    enabled = args[equal + 1] == '1';
  }
  return seen && enabled;
}
