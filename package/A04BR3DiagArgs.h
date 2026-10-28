// SPDX-License-Identifier: MIT
#pragma once
// Long names take precedence, including an explicit zero. Short aliases allow
// board diagnostics to coexist within the kernel command-line length limit.
template<class Parse> unsigned a04br3_diag_arg(Parse parse, const char *longName, const char *shortName) {
  unsigned value=0;
  if (!parse(longName, &value, sizeof(value))) parse(shortName, &value, sizeof(value));
  return value;
}
