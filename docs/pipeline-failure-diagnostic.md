# render pipeline失敗診断

`--pipeline-failure-diag` は既定オフで、Metal frontendが最初の2回のrender pipeline失敗を受けたとき、stage・NSErrorの説明・descriptorの要約を `PF` 行へ出力する。descriptor・archiveの事前検証とruntimeの失敗を含む。descriptorの全フィールドやシェーダーデータは記録しない。行は63文字以下に分割し、長い説明は上限で切り詰める。配送確認はないため無出力は失敗なしの証明にならない。

同一Mesa・既存bridgeを保ち、共有SSHビルドとは別の出力へ診断bundleを作るには、固定checkoutから以下を実行する。TREEは既存runnerの `out/a04br3/tree`、DRIVERは既存のiOS Mesa dylib、OUTは未存在の絶対パス。固定ハッシュが一致しなければ停止する。XcodeとiOS SDKが必要。

```sh
bash scripts/build-pipeline-failure-isolated.sh TREE DRIVER OUT
```

timingとfailureの両診断を有効にし、shader captureは有効にしない。failure診断はexception出力とno-copy失敗callbackも有効にする。署名されたbundle、trust.json、使用入力のSHA-256と設定を `OUT` に残す。bridgeとMesa dylibの再利用は記録されるが、ハッシュ一致だけではそのビルドの完全な出自を証明しない。実機試験は別工程。

固定NativeMetalの検証は `NATIVE_METAL_ROOT=/path/to/NativeMetal python3 scripts/tests/check_metal_pipeline_failure.py`、両switchの確認は `python3 scripts/tests/test_pipeline_timing_switch.py`。これらはホストでの限定試験で、iOSの実リンクや実機動作を証明しない。
