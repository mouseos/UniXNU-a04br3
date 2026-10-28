#!/bin/bash
set -euo pipefail
# Run on an Xcode host with the iPhoneOS SDK and Metal toolchain installed.
here=$(cd "$(dirname "$0")" && pwd)
out=${1:?output directory required}
mkdir -p "$out"
sdk=$(xcrun --sdk iphoneos --show-sdk-path)
xcrun --sdk iphoneos metal -std=ios-metal2.0 -c "$here/xr550.metal" -o "$out/xr550.air"
xcrun --sdk iphoneos metallib "$out/xr550.air" -o "$out/xr550.metallib"
xcrun --sdk iphoneos clang++ -std=c++17 -fobjc-arc -arch arm64 -isysroot "$sdk" -miphoneos-version-min=17.0 -framework Foundation -framework Metal "$here/xr550.mm" -o "$out/xr550-probe"
