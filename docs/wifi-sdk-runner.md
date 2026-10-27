# WiFi SDK固定ソースの隔離ビルド準備

この手順は既存macOS runnerの所有者が実行する。SSH接続、実機操作、既存checkoutのresetは行わない。準備スクリプト自身はビルドしない。

固定公開ソース（すべて `wifi-sdk` branch）:

|役割|URL|固定SHA|
|---|---|---|
|base|https://github.com/mouseos/UniXNU|9121ed78a2bb3d3f1385b2e6c63256dd37ebe62e|
|device|https://github.com/mouseos/UniXNU-a04br3|ef48e0f88820d7be221c711322ad48f3fc8473ad|
|NativeMsdc|https://github.com/mouseos/NativeMsdc|2beb6ac980824c2f461317a963aca17701bc1362|

前提: macOS、XcodeのmacOS/iPhoneOS SDK、既存公式ビルドで生成した完全な `.cache/fakeroot`、元の展開済み21H461 kernelcache、GitHub取得可能なネットワーク、十分な空き容量。既存ビルドのSDK更新とコピーを並行させない。SDKの絶対symlinkはコピー元へ書き戻す危険があるため拒否する。拒否された場合、元SDKを変更して通さず所有者が独立したSDK準備を判断する。

既存runner記録ではbaseは `$GITHUB_WORKSPACE`、deviceは `$GITHUB_WORKSPACE/device`、treeは `$GITHUB_WORKSPACE/out/a04br3/tree`。DESTは未使用の `$RUNNER_TEMP/wifi-sdk-9121ed78-ef48e0f` に分離する。kernelcacheは既存の `out/ipsw/21H461/kernelcache.decompressed` を明示する。存在やhashが異なる場合は停止し、実在する同じhashの入力を所有者が指定し直す。環境変数がない対話SSHシェルでは既存runnerの記録済み値を設定する。ホスト名や絶対パスを推測して接続しない。

```sh
python3 /path/to/prepare-a04br3-wifi-sdk.py \
  --existing-base "$GITHUB_WORKSPACE" \
  --existing-device "$GITHUB_WORKSPACE/device" \
  --existing-tree "$GITHUB_WORKSPACE/out/a04br3/tree" \
  --kernelcache "$GITHUB_WORKSPACE/out/ipsw/21H461/kernelcache.decompressed" \
  --destination "$RUNNER_TEMP/wifi-sdk-9121ed78-ef48e0f" \
  --data-volume-uuid CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36
```

これは前提検査のみ。通過した同じ引数に `--prepare` を追加すると、既存2 checkoutと必要な初期化済みsubmoduleの作業ファイル・Git objectsを独立コピーし、コピー先だけで公開branch/pinをfetch・適用する。git cloneやsubmodule updateは準備中には呼ばない。worktree/submoduleの外部gitdir参照はコピー先内部の通常の `.git` directoryへ実体化する。元のconfig/hooks/認証情報/reflogはコピーしない。既存Git alternatesや未初期化submoduleは停止条件。コピー対象はHEAD管理ファイルとGit objects/refsで、元の未追跡ファイル・build/cache出力は混ぜない。dirtyな追跡ファイルはコピー先だけで固定pinへ復元し、元のHEAD/index/statusも前後比較する。SDKとkernelcacheも別途独立コピーする。公開branchの移動、pin不一致、kernelcache不一致、SDKコピー中の変更は停止条件。失敗先を自動削除しない。再試行は原因確認後に別の未使用DESTを選ぶ。

生成された `wifi-sdk-manifest.json` と `build-wifi-sdk.sh` を確認した後、所有者が次を実行する。

```sh
"$RUNNER_TEMP/wifi-sdk-9121ed78-ef48e0f/build-wifi-sdk.sh"
```

公式 `defconfig` と `build kernel` のみ実行する。`A04BR3_WIFI=1` と明示Data/cache UUIDはこの子プロセスのclean環境へ設定し、継承した他のXNU設定やlocal-source overrideは除外する。Data UUIDは必須で、環境変数から暗黙に補わない。上記Data UUIDは現在の既存Data seedに対応し、seed変更時は実測UUIDを指定する。`--cache-volume-uuid` は任意で、固定baseと同じ `8846FE78-2910-4297-AF97-D59BB6F8FC16` が既定。指定した有効値はmanifestへ記録する。実機用bootargの追加やpayload/firmware生成は行わない。公式prelink事前検査は実際のkernelcacheとprofileで元Apple networking familyとの競合を検査する。

SDKと元kernelcacheを再利用する一方、既存OBJ・xnu-work・生成archiveはコピーしない。既存コンパイル記録の絶対ソースパスを別directoryへ移植すると旧checkoutへ戻るためである。新しい出力先の初回kernel/bootstrapとarchive compileは必要。参照ソースは公式pinで新規取得する。SDKのinstallhdrs/firehose既存成果物はコピー先で再利用されるが、通常ビルドによるヘッダー更新はコピー先だけに行われる。

既存の隔離directoryでcompileを再試行する場合は再コピーせず、次でlauncherだけ更新できる。元の3 pin、recursive submoduleのHEAD/clean状態、Git metadataの独立性、kernelcache hashと公式コマンドを確認してから更新する。検査失敗時は既存launcherを上書きしない。SDK/OBJ/sourceは更新しない。

```sh
python3 /path/to/prepare-a04br3-wifi-sdk.py \
  --destination "$RUNNER_TEMP/wifi-sdk-9121ed78-ef48e0f" \
  --data-volume-uuid CBB0DD3D-2FAC-4AC9-9C16-1D0F7D15CE36 \
  --refresh-launcher
"$RUNNER_TEMP/wifi-sdk-9121ed78-ef48e0f/build-wifi-sdk.sh"
```

`GITHUB_TOKEN` はビルド開始時にPython launcherが実行環境から取得し、子プロセスの環境だけへ渡す。値はlauncher/manifest/コマンド引数へ保存しない。`set -x` やtoken値を含むコマンドを使わず、主担当の既存環境で提供する。token不在なら従来どおり匿名取得するためAPIのrate limitは残る。Python launcher自体は `python3 -I` で起動する。

ホスト試験:

```sh
python3 scripts/tests/test_a04br3_wifi_sdk_prepare.py
# 固定baseの実際のowner-data-volume.h生成コードまで実行:
python3 scripts/tests/test_a04br3_wifi_sdk_prepare.py --base-source /path/to/UniXNU
```

スクリプトの配布元commitと、ビルド対象の固定device pinは別物。新しい準備スクリプトが存在する作業branchのcommitからこのファイルを取得し、対象deviceは上表のef48e0fのまま用いる。

本スクリプトのホスト試験合格は、実SDKでのcompile/link成功やWiFi実機動作を意味しない。SDK完了時にはmanifest、build log、kernel/prelink成果物とhashを保存して別途判定する。
