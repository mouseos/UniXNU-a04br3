# MT6630 Wi-FiのUniXNU接続（既定OFF、SDK・実機未検証）

旧移植のSDIO/WMT/WLANとnative BSD network sourceを`package/wifi`へ移した。
現UniXNUのP18にはWi-Fi driver自体が組み込まれておらず、旧版のscan ioctl修正だけでは到達できなかった。
旧版の実績はFW ready・runtime NVRAM・AP受信まで。今回の移植が接続成功したという意味ではない。

## 固定入力と役割

- `IMPORT.sources.json`は旧公開kernelのcommitと元ファイルhash。旧bootshimの物理配置を使う2つのBootFirmware header、opt-in、crypto includeとnonce bindingだけを現構成へ適応する。
- MediaTek固有SDIO/WMT/WLAN、board glue、native request codecは機種package。APSL NetworkingFamilyとMIT BearSSLの原著作権・ライセンス・source metadataを同梱する。IO80211 private C++ vtableの推測bindingはしない。
- `NativeMsdc` forkの`NATIVE_MSDC_PARTITION_READ=1`は汎用のnamed-partition読取API。既定0。初期にCRC・全entry範囲・重複領域を検証したGPTを使い、既存HostSession lock/bounce/transfer(false)で読む。別のeMMC owner、raw disk書込API、校正書込を追加しない。
- firmwareは公開`mouseos/android_vendor_benesse_a04br3`の`0c756f413e7011231c8fb793af0930311023f68d`から3ファイルを固定SHA256で取得する。WMTは既存A04WMT1 pack/validationを再利用し、WLANは元MTKW bytesのまま。runtimeは実HVR/FVRとchecksum/hashを別途検証してから送る。
- firmwareに個体MAC・校正値は含めない。実機GPTの`nvram`から既存libnvram形式を動的に解き、完全なrecord/checksum/MAC条件が成立しない場合は拒否する。利用者にバックアップ抽出や校正ファイル指定を要求しない。

## ビルドと起動の二重opt-in

NativeMsdcの対応固定commitをbaseの正規gitlinkへpinしてから、通常の公式kernel buildへ
環境変数`A04BR3_WIFI=1`を与える。`build-arm-driver.py NativeMsdc`と
`build-a04br3-board.py`の両方に同じ値が必要。既存SDK・Mesaを作り直す必要はないが、
両archiveとkernelのcompile/linkは必要。その他の値・未指定ではWi-Fiを組み込まない。

起動引数は`a04br3-wifi=1`。moduleがリンクされていても未指定ならGPIO、MMIO、TRNG、
NVRAM、firmware送信、network service起動を行わない。board_launch（root/Data mount後）で
workerを開始し、旧版のBSD ready待ち契約も維持する。

コンパイル前のprelink guardは`[kernel.prelink].archive_preflight`で明示的に登録する。
baseのoptional hookが、公式CLIで選択した実効profileとhash確認済みkernelcacheを渡す。
Wi-Fi有効時はNativePrelinkの実metadataから依存閉包を計算し、元のIONetworkingFamily／
IO80211Familyが直接root、追加`--root`、間接依存のいずれに含まれても拒否する。
profile/cacheが不明、依存欠落、重複ID、未対応extra_argsによる入力差替えも拒否する。
検査はbootstrap／archive compile前に行い、手動board archive起動にも同じguardがある。
古いbaseや手動呼出しで実効入力が渡らない場合は有効化を拒否し、近傍board.tomlや
古いprelink reportから推測しない。Wi-Fi未指定／非1のguard自体は入力を読まない。
汎用hookは未登録時に追加処理なし。NativeMsdcの正規gitlinkと同様、統合時には
このbase側hookも固定ソースに含める必要がある。

source-owned NetworkingFamilyとIO80211 classesは通常のkernel constructorで初期化する。
元のIONetworkingFamily/IO80211Familyをprelinkして重複させないこと。現board rootsはこの条件を満たす。
Private class名だけの偽装、接続状態・鍵ACK・scan結果の捏造は行わず、未対応要求は拒否する。

## nonceと秘密情報

現ADTの`os.urandom(64)`は生成時値であり、同一配布imageの再起動毎に新しいとは扱わない。
Wi-FiのSNonceは既存source-kernel `read_random` 32 bytesと、実機MT8167 TRNGの新しい64 bytesを
SHA256で混合する。TRNGは旧bootshimと同じregister/gate根拠を使い、専用lockの下で取得する。
元gate/controlを復元し、全0・全1・隣接反復・timeout・mapping不成立では失敗する。
これは簡易stuck-source検査でありentropy品質認証ではない。実機でのTRNG取得は未検証。
SDIO lock外で実行し、固定値・時刻・MAC・公開seedだけへのfallbackはない。
seed・nonce・鍵はログに出さず、一時bufferを消去する。

## host試験

機種repoから実行する（LLVM 18、C/C++ compiler、ASan/UBSanが必要）。

```sh
python3 scripts/tests/test_a04br3_wifi_build.py
python3 scripts/tests/test_a04br3_wifi_provenance.py
python3 scripts/tests/test_a04br3_wifi_prelink.py --native-prelink-source /absolute/path/to/UniXNU/kits/NativePrelink
python3 scripts/tests/test_a04br3_wifi_board.py
python3 scripts/tests/test_a04br3_wifi_driver.py
python3 scripts/tests/test_a04br3_wifi_entropy.py
python3 scripts/tests/test_a04br3_wifi_nvram.py
python3 -m unittest discover -s package/wifi/network/tests
```

base repoでは`python3 scripts/tests/check_archive_preflight.py`、
NativeMsdc repoでは`python3 tests/test-partition-read.py`を実行する。
新しいprelink fixtureは実Mach-O／prelink XMLと指定した正規NativePrelink parserを使い、
個人checkoutへの暗黙依存やmetadata parserの成功stubを持たない。
実CPP/portable helperを試験するが、IOKit/DLIL/SDIO/AP/clockはhost fixture。
SDK compile/link、実FW ready、AP表示、auth/assoc、DHCP、実IP送受信は別の実機判定が必要。
旧iOS16由来carrierと現在iPadOS17の境界は実要求で再確認する。2.4GHz subset等の旧実装制限を維持し、
5GHz・roaming・rekey・WPA3等を実装済みとは扱わない。

参照: 公式`docs/porting/network.md`のboard worker/backend/OS integration分離、
`UniXNU-sheng` WifiProbe/WifiNet（516a780473a2ac59c8b438e132bb60204dda9e22）。
ath12k/PCIe用のregisterや校正値をMT6630/SDIOへ流用していない。

## 再開時点の検証範囲

safe-stop引継ぎのdevice 179ファイル／NativeMsdc 3ファイルは再開前のhashと一致した。
元の変更を退避・resetせず、source checkpointをリポジトリ外の調査ログへ保存した。
停止直前のBootFirmware pointer bounds修正を含め、board／SDIOのAArch64 compile、
entropy、runtime NVRAM、network 26 regressions、NativeMsdcの実API／policy試験を再実行した。
保存済みの固定kernelcacheでも現profileの29 rootsから31 modules／7 kernel interfacesを
確認し、元network familyを選択しないことを検査した。これはSDK linkや無線接続の検証ではない。
実機操作・runner操作は行っていない。独立reviewの承認済みとは扱わない。

## レビュー用source分割

import本体／出自記録とboard integrationは別単位でレビューする。
`package/wifi/THIRD_PARTY.md`と`PORT.sources.json`が原本hash、適応後hash、
Apple／BearSSL／host fixtureのライセンス対応を記録する。host fixtureのMIT本文は
固定donorの`LICENSE.port`を改変せず同梱する。WiFi C++の全翻訳単位には元familyの
source metadataどおり`__PRIVATE_SPI__=1`を明示し、実builderの生成引数を試験する。

SDK検証用にはNativeMsdc API、汎用preflight、NativeMsdc gitlink統合、device import、
device glue/testsを分けた固定source候補を用意する。親worktreeのHEAD/indexや他者の
gitlinkを動かさずにレビューする。SDK buildはその候補3repoを一致させた上で行う。
通常の公式手順は`A04BR3_WIFI=1 ./unixnu build kernel -D <device checkout>`であり、
実効kernelcacheは`--kernelcache`または既存の公式解決経路を使う。後から独自に
rootやcacheを差し替えるextra_argsはWiFi guardが拒否する。

独立レビューで見つかったprivate SPI指定とfixtureライセンス本文の欠落は修正した。
source blockerの解消とSDKリンク成功は別であり、実SDK compile/linkは未実施。
firmwareの配布条件や実機接続の確認をこのsourceレビューの承認として代用しない。
