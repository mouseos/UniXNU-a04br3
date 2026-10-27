# PowerVR KCCB予約と非同期kickの設計案

状態: 提案。GPUの実行経路は変更しない。P40で `scheduler_commit` のwallはid 1が16729.275ms、id 4が約15932msだった。resource flush/prepareは数ms。scheduler内のKCCB待機時間とCPU時間は未測定であり、この案の効果は未確定。CPU使用の退行は禁止する。

基準は機種リポジトリ `8a1f0b9`。Linux比較元は torvalds/linux の commit `734bef548a96e8774d6eef5dfa2026c6edc4e80b` の `drivers/gpu/drm/imagination/pvr_ccb.c` と `pvr_queue.c`（[pvr_ccb.c](https://github.com/torvalds/linux/blob/734bef548a96e8774d6eef5dfa2026c6edc4e80b/drivers/gpu/drm/imagination/pvr_ccb.c)、[pvr_queue.c](https://github.com/torvalds/linux/blob/734bef548a96e8774d6eef5dfa2026c6edc4e80b/drivers/gpu/drm/imagination/pvr_queue.c)。ローカル `unixnu/_research/linux-pvr-src/SOURCE_COMMIT` および同ディレクトリのファイルでも照合可能）。機種実装は `NativePowerVR.cpp`、`NativePowerVRJobs.cpp`、`kernel/pvr_cccb.c`。FWのreturn slotがこの機種で更新されないという既存の観察に依存するため、Linuxのper-slot応答を前提にしない。

## 現行契約と問題

`gLock` はKCCB write offset、実行カウンタの目標、FW heapを保護する。128枠のリングは空判定のため1枠を空ける。`gJobs.lock` はジョブのCCCB、依存、登録と完了処理を保護する。現行 `native_powervr_kccb_submit` は `gLock` を解放してから実行カウンタを待つが、ジョブ送信側は `gJobs.lock` を保持したままである。LinuxはCCCBとKCCBを事前に確保し、ジョブを保持したまま実行待ちなしでkickする。現行の `void` kick wrapperは失敗をログに記録するだけで伝播しない。

ジョブはcontext、HWRT、host BOを保持する。UFO到達だけで資源を解放せず、reset通知の順序確認とhost cache同期を経てretireする。timeoutのjobもGPUが参照する可能性が残る限り保持する。これは非同期kick後も維持する。

## 予約契約

以下は実装する際の必須条件であり、実装済みを意味しない。

1. 空きは `capacity = slot_count - 1`、`used = (write - read) & mask`、`free = capacity - used - reserved`。`mask + 1` が2の冪でread/writeが範囲内であることを検証し、不正時はデバイスを隔離する。`reserved` は全KCCB利用者に共通で、`gLock` 内で変更する。
2. ジョブのbatchは、同一呼出しで発行するkick数を合算して**CCCB・seqno・依存状態を変更する前**に一括予約する。paired geometry/fragmentは1 kick。最大件数だけを予約する場合も、余分な枠を確実に返す。予約は所有者とdevice generationに紐付け、消費・cancelを一度だけ行う。
3. KCCBを共有する同期MMU flush、FW cleanup、free-list更新、selftestも同じ `used + reserved` 制約に従う。全batch予約がcleanupに必要な枠を恒久的に塞がないよう、同期制御要求用の枠を確保するか、予約開始前に全要求を公平に順序付ける。片方を選ぶまで実装しない。空き待ちは `gJobs.lock` 外で行い、期限とデバイス喪失を検出する。CPU使用を増やすポーリング追加は不可。
4. 予約待ちの間に他の送信がCCCB・queue・contextを変える可能性がある。復帰後に `gJobs.lock` を取り、queueの生存、context faulty、CCCB容量、依存関係、device generationを再検証する。失敗時は予約を返し、未公開ジョブの参照を解放する。これらを原子的に再検証できない実装は不可。
5. 予約消費とcommand公開は `gLock` 内で行い、command clean → barrier → write offset公開 → clean/barrier → MTS kick の順を維持する。各sync commandは予約済み枠を横取りしない。消費済み枠はcancelできない。global execution targetは同期ユーザーと共有し、wrapおよび遅延完了を検証する。
6. 成功とはKCCBへの公開受付であり、GPU描画完了ではない。公開済jobはclient closeやtimeout後も、UFOまたは安全なreset/停止の証明までcontext、HWRT、BOとCCCBを保持する。completion workerのreset note順序とhost cache同期後にのみ成功fenceを公開する。
7. 複数kickを一括公開する途中でdevice lostその他の異常が発生した場合、公開済みと未公開を区別する。公開済みを単純解放・rollbackしてはいけない。未公開分は予約とCCCB/依存/seqnoの状態を復元できる場合だけ解除する。復元不可能なら対象context/queueをfaultyにし、GPUがアクセスしうる資源を隔離する。部分batchに対するユーザーABIの成功・失敗とfenceの返却規則は設計上の未決事項であり、定義・検証前に実装を有効化しない。
8. `pvr_device_lost` と予約・送信を同一の排他規則とgenerationで同期させる。失われたgenerationのreservationは新generationに持ち越さない。FWが進行中のcommandを参照しているときはreset完了やDMA停止の証拠なしにメモリを再利用しない。

## 検証と有効化

実装は既定オフの明示的フラグにする。オフ時には元の制御フロー、backend、所有権を維持する。実コードを対象に、リング満杯・wrap・複数送信者と同期制御要求、予約取り消し、device lostの各公開境界、paired jobs、部分送信、timeout/reset/late UFO、file close、データ可視性、fence状態を試験する。モデルだけでは実装の保証にならない。

実機で有効化する前に、この機種のFWで複数counted kickが順序どおり消費されること、MMU同期の完了意味、reset時のメモリ寿命を確認する。P40と同一操作・同一GPU backendでscheduler wall、KCCB待ち、lock待ち、CPU時間、GPU completion、描画結果を対照する。P40のscheduler wallだけではKCCB待機や改善幅を示さない。CPU退行、欠落、timeout、描画不一致、FW fault、資源解放の不確実性があれば採用しない。

独立レビューは親担当が依頼する。レビューと実機確認の完了までは本設計を安全な修正と呼ばない。

## 独立レビューのP1: fenceの結果とpending submitの寿命

現行 `recordFailed()` は失敗fenceを64件の循環配列に記録し、`native_powervr_fence_wait()` は未検出かつ `fence < nextFence` を成功にする。65件目以降に追い出された失敗は成功に見える。これは現行コードの有限履歴とWaitFence条件から導かれる既存の障害条件で、P40で発生したという観測ではない。`retired[]` の64件は遅延resetの関連付け用でもあり、failed配列だけ拡張しても後着resetの識別問題は解決しない。

提案のABI v1では各client fileの**公開済みfenceごとの状態台帳**をclose完了まで保持する。状態は `pending`、`success`、`failure`、`unknown` のいずれか。失敗やunknownの追い出しを成功に変換しない。未知・破損・後着resetを一意に対応できない場合は成功でなくエラーを返す。成功はFWCCB処理との同期、UFO、host cache同期、reset照合が済んだ後に一方向に確定し、既に返した成功を後から取り消せないことをABI上明示する。後着resetの照合に必要なcontext世代・job ID・DMの識別も、同じ有効期間に保持する。ID再利用時の曖昧さはfail-closedとする。

メモリ上限は無制限にしない。新規batchのfence数・台帳・後着reset識別子をCCCB変更・KCCB予約の前に確保し、上限や割当失敗はbatch全体を拒否する。失敗した割当でfence番号を発行しない。64bit番号のwrapや予約済magicとの衝突を検出し、再利用せず新規送信を拒否する。成功結果の圧縮や外部release APIを採る場合は、古い失敗・unknownを成功に変えない証明と互換性レビューを別途要する。

`NativePowerVRUserClient::clientClose()` は新規呼出しを拒み、外部methodがconnection参照を保持する間は `free()` を遅らせると現行コメントにある。`ptePrephase()` は一時的にclient lockを放しても復帰後に `closing_` を確認する。しかしこれは予約待ち全区間の保護を自動的に保証しない。新設する `PendingSubmit` はclient method参照に加え、独立したjobs-file参照、各job/context/HWRT参照、VM自体とGPU VA mappingのpin、BOの物理ページとimportのpin、CCCBを保持し、closeとdevice lostから一意に見えるregistryへ待機前に登録する。既存のhost BO snapshotはBOを保持するが、VM内のGPU mappingまで固定する証明にはならない。mapping変更・unmapとpinの排他契約を実装し、確認できない場合は待機中および公開済みGPU利用中のunmapを拒否する。

closeはclient lockの `closing_` を立てpending waitをwakeし、lockを放して取消・drainを行う。まだ公開されていない要求は世代と予約を取消し、ピンを解放できる。公開済み要求はclientやfileの解放に巻き込まず、UFO完了または確認されたreset/停止まで参照とmappingを保持する。device lostも同様に受付を止め、未公開だけ取消し、公開済みは隔離する。closeとlostの並行実行で両者が二重に解放しない所有権の移譲が必要。pending登録から外部method返却、close後のworker退役までの参照が実機driverで証明されるまで非同期化は実施しない。

## 独立レビューのP2: 起床・ロック・HWRT・可視性

予約待ちには状態変化を保護する `gLock` と同一のwait predicateを用い、predicate検査とwait登録を原子的に行う。read offset前進の観測、予約のcancel/消費、lost、close、timeoutでwakeする。interrupt未使用なら既存completion workerによるread offset監視を検討するが、周期監視のCPU増加を測るまでは採用しない。wakeは予約の成功を意味せずpredicateを再確認する。lost wakeupと長期飢餓、同期control要求の進捗が未解決なら有効化しない。

ロック順はclient lock → jobs lock → KCCB lockへの既存経路を棚卸しする。FWCCB側は既存の `fwccbLock` → jobs try-lockであり、逆向きの待機を導入しない。KCCB空き待ちやclose待ち中にclient、jobs、FWCCB lockを保持しない。client lockを外す際はpendingの独立参照を先に取得し、再取得後にclose・世代・mapping・queueを再検証する。KCCB lock取得中はjobs lock取得を試みず、通知後に上位lockを別途取得する。現行 `hwrtUpdate()` はreader 8件に達するとjobs lock保持のまま無期限待機する。予約の前にHWRT依存の容量を調べ、容量不足なら全lockと未消費予約を返して期限付き待機・再検証する設計が必要。既にCCCBを変更した後のwaitは許さない。失敗・resetによる依存終了は単なる成功UFOと区別する。

公開順序は対象host BOのCPU→GPU同期を完了してから、CCCB payload・dependency・UFO updateを所定のWC/cache方式で書き、必要barrierを経てKCCB command、write offset、MTS kickを順に公開する。MMU page-table更新と必要なTLB/MMU ackが先行しなければならない。GPU→CPU方向はUFO確認後、後着resetを照合し、host cache同期を完了してからfence成功を外部公開する。新設する非同期化で現行の `dc civac`・clean・barrierを削除や並べ替えしない。WCとcached aliasの前提、書き込み範囲、他のCPUによる同時writeは別途証明する。

## 再現可能な検査範囲

`python3 scripts/tests/test_kccb_reservation_model.py` は共有枠の抽象状態探索に加え、65件以上の失敗、有限履歴の反例、close/lostとpending段階、公開済み参照の隔離、満杯・wrap・cancel・wake predicate、8 reader境界、cache公開順を独立した抽象仕様モデルで検査する。モデルは想定した制約を検査するもので、XNU実装・arm64メモリモデル・実FWの実証ではない。実行時trace、CPU、性能改善、描画成功は含まない。実コードに変更する場合は同じ条件を実関数・競合・fault injectionで再実行する必要がある。
