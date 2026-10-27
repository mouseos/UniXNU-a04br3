# PowerVR KCCB予約と非同期kickの設計案

状態: 提案。GPUの実行経路は変更しない。P40で `scheduler_commit` のwallはid 1が16729.275ms、id 4が約15932msだった。resource flush/prepareは数ms。scheduler内のKCCB待機時間とCPU時間は未測定であり、この案の効果は未確定。CPU使用の退行は禁止する。

基準は機種リポジトリ `8a1f0b9`。Linux比較元は `unixnu/_research/linux-pvr-src/SOURCE_COMMIT` で識別する `pvr_ccb.c` と `pvr_queue.c`。機種実装は `NativePowerVR.cpp`、`NativePowerVRJobs.cpp`、`kernel/pvr_cccb.c`。FWのreturn slotがこの機種で更新されないという既存の観察に依存するため、Linuxのper-slot応答を前提にしない。

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
