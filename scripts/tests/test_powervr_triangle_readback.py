#!/usr/bin/env python3
"""実際の画素判定関数をホストCコンパイラで検証する。SDK・GPU不要。"""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[2]
    source = root / "package/powervr/user/powervrprobed_vulkan.c"
    text = source.read_text()
    start = text.index("static int triangleReadbackCheck(")
    # 現在の関数直後の境界を使い、コピーした判定ロジックをテストしない。
    end = text.index("// Vertex + fragment execution", start)
    function = text[start:end]
    harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <assert.h>
enum { kWidth = 64, kHeight = 64 };
static int passes, failures;
static void probe_line(const char *format, ...) { (void)format; }
static int vkCheck(const char *name, int ok, const char *detail)
{
    (void)name; (void)detail;
    if (ok) passes++; else failures++;
    return ok;
}
''' + function + r'''
static uint8_t pixels[kWidth * kHeight * 4];
static void put(int x, int y, int triangle)
{
    uint8_t *p = &pixels[4 * (y * kWidth + x)];
    p[0] = triangle ? 255 : 0x22;
    p[1] = triangle ? 0 : 0x44;
    p[2] = triangle ? 255 : 0x66;
    p[3] = 255;
}
static void fill(int triangle)
{
    for (int y = 0; y < kHeight; y++)
        for (int x = 0; x < kWidth; x++)
            put(x, y, triangle && x >= 8 && y >= 8 && x + y < 63);
}
int main(void)
{
    fill(1);
    assert(triangleReadbackCheck(pixels, "ideal-triangle"));
    assert(passes == 2 && failures == 0);

    fill(0);
    const int directOK = triangleReadbackCheck(pixels, "linear-direct");
    assert(!directOK && passes == 3 && failures == 1);
    fill(1);
    const int copyOK = triangleReadbackCheck(pixels, "linear-copy");
    assert(copyOK && passes == 5 && failures == 1);
    assert(!(directOK && copyOK));
    // 成功したコピー判定でも先行する直接判定の失敗カウンタは残る。

    put(8, 8, 0);
    assert(!triangleReadbackCheck(pixels, "wrong-interior-pixel"));
    assert(passes == 6 && failures == 2);
    fill(1);
    pixels[0] = 0;
    assert(!triangleReadbackCheck(pixels, "wrong-background-pixel"));
    assert(passes == 7 && failures == 3);

    fill(1);
    for (int x = 8; x <= 55; x++) put(x, 63 - x, 1);
    assert(triangleReadbackCheck(pixels, "triangle-colored-edge"));
    assert(passes == 9 && failures == 3);
    // 既存仕様は対角48画素を判定から除外する。背景色の対角はidealで検証済み。
    for (int x = 8; x <= 55; x++) pixels[4 * ((63 - x) * kWidth + x)] = 0x7b;
    assert(triangleReadbackCheck(pixels, "excluded-edge"));
    assert(passes == 11 && failures == 3);
    puts("PASS: ideal triangle, background-only, wrong interior/background pixel, edge tolerance, retained direct failure");
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="powervr-triangle-") as temporary:
        c_file = Path(temporary) / "check.c"
        binary = Path(temporary) / "check"
        c_file.write_text(harness)
        compiler = shlex.split(os.environ.get("CC", "cc"))
        subprocess.run(compiler + ["-std=c11", "-Wall", "-Wextra", "-Werror",
                                  str(c_file), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
