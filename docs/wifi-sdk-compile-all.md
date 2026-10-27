# WiFi SDK compile-only全件診断

通常buildの動作は変更しない。これは主担当が既存macOS SDK runnerで明示実行する診断で、kernel/firmwareのlink、firmware取得、archive更新、実機操作を行わない。compile成功はWiFi実機動作を意味しない。

前提は、同じ絶対pathでbootstrap済みの隔離treeと、診断対象commitへ更新したdevice checkout。tree内の `IOPlatformExpert.cpo.json` と `kern_exec.o.json`、XNU生成headerとSDKが必要。元runnerを編集したり、他directory由来OBJの絶対pathを手修正したりしない。古いrecordのcwdが選択tree外なら開始前に拒否する。

```sh
WORK="$RUNNER_TEMP/wifi-sdk-9121ed78-ef48e0f"
A04BR3_WIFI=1 python3 "$WORK/UniXNU-a04br3/scripts/check-a04br3-wifi-sdk-all.py" \
  --tree "$WORK/out/a04br3/tree" \
  --device "$WORK/UniXNU-a04br3" \
  --output "$RUNNER_TEMP/wifi-sdk-compile-all-attempt1"
```

outputは未使用directoryでsource/treeの外にする。失敗時のexit statusは1だが、compilerによる各ファイルのエラー後も最後まで続行する。通常buildの `command_for`、C/C++ warning flags、`source_files`、BearSSL C source listをASTから読み取る。board builder本体はimport/実行しないのでGPU/他driverのcompileやfirmware取得を伴わない。通常と同じsource・warning設定を使い、診断専用に `-ferror-limit=0` だけを追加してTU単位のエラー上限を解除する。依存ファイル生成は無効化し、objectは全て診断directoryへ出す。

対象は `build_wifi` のC++ translation unit全部とBearSSL C translation unit全部。生成済みfirmware埋込みassemblyはC/C++警告監査の対象外。

- `diagnostics.log`: 全sourceのstdout/stderrを切り捨てず集約。
- `NNN-name.stderr.log` / `.stdout.log`: 各sourceの完全出力。
- `NNN-name.command.json`: 実コンパイル引数。
- `report.json`: 各sourceのhash、終了値、warning group、合計失敗数と入力compiler records。

診断objectは通常archiveへ追加しない。全件成功でも通常のarchive/kernel/prelink buildを別途実行する必要がある。

## 今回の静的監査と限界

Apple importの `.cpp`、`.h`、`.inc` 全体でbitwise式のenum所属と、unbraced制御文前後のtab展開列を点検した。型の異なるpower flags、output status/command合成を修正し、入力thread/NMI/tap等の紛らわしい字下げをbracesで明示した。同じenum内のlink/filter/checksum/state flagや、整数macro同士の式は維持している。

焦点を絞った試験では、実sourceから式/guardを抽出し、host/ARM64でコンパイル、mask値や分岐を確認し、旧式に戻した際の警告を再現する。IOMbufMemoryCursorのguardはローカルClangでは旧形でも警告しないため、予防的な意味保存のbraces修正として区別する。すべてのpreprocessor分岐やSDK固有診断を静的走査だけで保証しない。残る警告は上記実SDK全件診断で確定する。

```sh
python3 scripts/tests/test_a04br3_wifi_import_warnings.py
python3 scripts/tests/test_a04br3_wifi_sdk_all.py
python3 scripts/tests/test_a04br3_wifi_lladdr_guard.py
python3 scripts/tests/test_a04br3_wifi_enum_options.py
python3 scripts/tests/test_a04br3_wifi_provenance.py
```

診断ツールのhost fixtureは、実production ruleを読み、2ファイルを失敗させる実行可能なsynthetic compilerでも後続C/C++ source全部を試行すること、5000文字超stderrの先頭/末尾保持、既定off、既存output保護、stale record拒否、通常object/dependencyを更新しないことを検証する。これは本物のSDK全件コンパイルの代用ではない。
