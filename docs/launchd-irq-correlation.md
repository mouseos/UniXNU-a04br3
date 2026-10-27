# launchd のユーザーPCと停止スタックの診断

対象はP46 device d3dd371を基にした診断候補。汎用UniXNUは
`9c2a2f10240e3f55312be89b81f6ba9515064642`（親 `7e77cbb`、XNU基底
`d8b80295118ef25ac3a784134bcf95cd8e88109f`）を使う。機種側はこの文書を
含むdevice commitを固定して通常の `kits/a04br3` として組み合わせる。
P47のcheckout、依存pin、buildディレクトリは変更しない。

`XNU_IRQ_PC_STATS=1` の診断kernelに加え、起動引数 `ucp=1`、対象PID
（`native-irq-pid=1` または従来の `a04br3-home-perf=1` によるPID 1）と
`a04br3-proclog=10` が必要。停止スレッド情報にはさらに
`a04br3-home-perf=1` が必要。argsの実際の長さ・切詰めと最終payloadを
別途確認する。両方のゲートがなければ新しい標本は記録しない。

IRQ側では対象PIDのtimer 64回に1回、uniqueid・thread ID・procのidentity version・modeと
256-byteのPC枠を最大32種類まで数える。user modeのPCは割込み時の
ユーザーPC、kernel modeのPCは割込み時のunslid kernel PC。IRQ時に `machine.upcb` を読まない。ユーザーアドレスは
unslideせず出力され、ASLR情報を含む。PCを含むログ・解析結果はローカル限定、
`umask 077` と所有者のみ読書き可能な保存先で収集し、共有・公開しない。

heartbeatの `a04br3 uirq` は期間開始・終了、PID/uniqueid、epoch、表が
満杯だった件数、worker競合skipを出す。全32行を出す。`counts` の
`ticks/samples/busy/wrong_uid` は非同期の累積値で期間差も厳密な
transactionではない。IRQ側はtry-lock失敗なら即dropし、待機、メモリ確保、
出力、スタック走査は行わない。取得頻度・割込みマスク・周期性の偏りがあり、
ヒット比率をCPU時間比として扱わない。64サンプルごとの抽出によるaliasing、
表の飽和・競合・非表示期間を評価する。

`a04br3 perf launchd thread` と `pc` のUID・thread IDを同じheartbeatと
照合する。停止中のみ既存処理が取得した `k_valid=1` のkernel stackと
IRQ時PCは**別時刻の観測**であり、同じ呼出しや因果関係の証明ではない。
行のversionは `proc_pidversion` の観測値でありexec中の完全な画像識別子ではない。
スレッド変更、PID再利用、exec・shared-cacheのスライド変更も考慮する。
正確なuserシンボル対応にはそのbootの該当バイナリ/共有cache UUIDと配置・
slideが必要。kernelは実際にbootしたkernelのハッシュとunslidシンボルを使う。

検証: `python3 scripts/tests/test_user_irq_correlation.py`、
`python3 scripts/tests/test_home_irq_pc.py`、`python3 scripts/tests/test_home_perf.py`。
汎用側は `python3 scripts/tests/check_irq_pc_hook.py --xnu-source <固定XNU>`。
ホストfixtureは競合や欠落のロジック検証であり、XNU SDK実buildや実機での
IRQ安全性・オーバーヘッド・標本の代表性を証明しない。計測前に第三者レビュー、
同一設定で機能off/onの起動・時間・UART量を比較する。実SDKでflag off/onの両方が
ビルドされ、実機で安全性・性能・情報の妥当性を検証し、独立レビューが済むまで、
本番運用では有効にしない。今回flashは行わない。

## 固定XNUソースに基づく寿命の確認

対象はXNU `d8b80295118ef25ac3a784134bcf95cd8e88109f`。以下のパスは
そのソースルートからの相対パスで、他のXNU版へ一般化しない。

- `osfmk/arm/rtclock.c:337` はIRQ無効をassertし、`cpu_int_state` から
  PCとmodeを取得する。`osfmk/arm64/locore.s:1003` のIRQ入口は現行
  threadのpreemption countを増やしてから同状態を設定する。これらは通常の
  timer IRQにおける現行threadと保存フレームの根拠で、全例外・FIQに対する
  新しい安全保証ではない。
- `osfmk/kern/thread.c:1468` でthreadはtaskを参照し、終端状態で最後の
  thread参照が落ちた `thread_deallocate_complete` が `machine_thread_destroy`
  の後にtask参照を落とす（同:776、838、840）。`sched_prim.c:3695` は
  threadがcoreを離れる処理で終端queueへ渡す。現行threadをIRQ中に別threadが
  最終解放しないという根拠はこの順序による。新計測はthreadポインタを保持しない。
- `bsd_kern.c:92` の `get_bsdtask_info` は参照を取得しない。`kern_proc.c:1584`
  のprocはtaskに隣接し、`task.c:1961,2242` と `kern_proc.c:900-943` は
  task側holdを最終task解放まで保持する。`kern_exit.c:2534-2536` の切離し
  前後でNULLか旧procを観測し得るが、task holdが生きている限りproc/RO領域の
  最終解放を防ぐ。PIDとuniqueidは `kern_proc.c:1065,1694` の直接読取り。
  `TF_HASPROC` は通常のflagであり、exit中の観測が最新である保証はない。
  zone検査を含む内部ABIであり、対象版以外でのIRQ安全性は未検証。
- `arm64/pcb.c:263-292,379-391` はuser stateを作成時に割当て、thread
  解放時にNULL化してから解放する。一方 `arm64/status.c:1187,1600` のexec等
  でのflavor切替や再初期化とIRQが重なる際、値が意味のある直前のuser PCと
  保証する根拠はない。そのため旧実装28e8253のIRQ中upcb読取りを除去した。
  既存workerの停止threadスタック採取は別の既存経路であり、この変更では
  その実機安全性を証明しない。
- `kern_fork.c:994-1000` はexec shadowにPID・uniqueid・versionをコピーし、
  `kern_exec.c:6869` は後にversionを更新する。versionを各標本に追加したが、
  切替中の2 taskを常に区別できる保証はない。別時刻のスタックとの結び付けは
  UID・tid・時刻・画像情報を確認しても相関であり因果ではない。

## P52 統合時の mapping 記録

`ucp=1` かつ hook有効時、既存のtask診断workerはlaunchdについて
`a04br3 uirq mapping` を記録する。PID、uniqueid、proc version、採取時刻、
XNUのtask telemetry cacheに保持されたshared cache UUIDとslide、main Mach-O
header仮想アドレスを含む。固定XNUの `vm_shared_region_start_address` は
shared region初回mappingのUUIDをユーザーメモリから取得してtaskへ保持する。
診断workerはユーザーメモリを読まない。slideが未設定、採取中の値の違い、
またはtask lock取得失敗の場合、mappingは使えない。`stable=1` は二重読取りが
一致したことのみで、非原子的な書込みやexec競合、ABAを排除しない。

同一bootのUUID・slideと同じUID/versionのIRQ行を近接時刻の複数mapping行で
照合し、exec/画像変化が疑われる区間は未解決とする。UUID一致は実際に対応
するshared cacheを別途特定するための必要条件であり、それだけでは画像内
symbolや実行時の読み取り範囲を証明しない。cacheの各mapping範囲と同一UUIDを
検証し、256-byte bucket内に複数候補がある場合は全部を示す。main executable
はheaderアドレスのみでUUIDを取得していないため、追加の画像識別なしには
シンボルを確定しない。記録は任意指定時だけ行い、ログへのアクセスを制限する。
