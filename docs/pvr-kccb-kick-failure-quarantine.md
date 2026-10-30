# KCCB KICK失敗時の隔離候補

基準は機種source `f2dee958a180ae6fee7b84a44baf9d74dcd22aff`。
短時間poll候補は含めない。既存実装はCCCB書込み後にKCCBを送るが、
Linux由来のvoid shimは送信エラーをログだけで捨て、submitは成功fenceを返す。
Linux側は送信前にKCCB slotを予約しており、この前提は現行shimにない。

KICKのring-full `-EBUSY` は書込み前の検出なので従来通り有界に再試行する。
timeoutはenqueue後で、FWが遅れて実行する可能性があるため再送しない。
送信失敗はジョブ層へ伝え、以降のkickを止め、バッチの全fenceを失敗として
記録してsubmitを `-EIO` にする。実際にCCCBへ書いた各ジョブと組のfragmentは
失敗・隔離とし、contextをfaultyにしてジョブ参照を再起動まで保持する。
送信済みか不明なジョブも同様に扱う。未着手のジョブだけは解放する。
VMのmap/unmapと共通のVM lockで該当VMを固定し、追加参照を一度取り、以降の
map/unmapとそのVMに対する新規submitを拒否する。close後もページテーブル・
マッピング・BOとジョブが残る。後からUFOが進んでも隔離ジョブは退役しない。
保持は意図的なリークであり復旧は再起動。成功時の経路は従来通り。

ホストfixtureは実際のkick/retry/隔離関数と完了workerを使用し、正常、部分成功、
paired kick、未送信EBUSY、遅延完了し得るtimeout、重複隔離、UFO後の非退役、
未送信fence失敗の経路を検証する。ハードウェア・FWの停止や実機性能は検証しない。
呼出し元のVM操作との並行順序、他のFWCCB/cleanup経路の所有権、ユーザー側が
保持するメモリの変更、close/reopenや回復の実機動作は独立レビュー・統合検証が必要。
この候補の固定commitは実機投入の承認を意味しない。

## 独立レビュー後の実機投入禁止条件

**P1未解決：本候補は実機投入不可。** VMとジョブの参照保持はFWの停止を
意味しない。同じIOSurface／host BOは別のVM・接続・プロセスやCPUからも参照・
変更・再利用され得る。submitを `-EIO` にしてもその再利用は防げない。
Darwin winsysで `kIOReturnIOError` を `VK_ERROR_DEVICE_LOST` に変換して
fenceを公開しないが、Vulkan/Metal側と共有メモリ所有者がdevice-lostを受けて
どう動くか、またFWが停止したかは証明していない。これはエラー伝播の改善であり
共有資源隔離やDMA安全性の保証ではない。新接続や他のGPU経路の停止も未保証。

実機に進むには、対象SoCで確実なGPU/FW停止・完了確認を取得し、それまで
すべての参照先（別VM・別接続・共有BO・scanoutを含む）の再利用を防ぐ契約、
または同等の隔離機構を設計・実装・検証する必要がある。停止方法と所有者への
エラー伝播、CPUアクセス、破棄・再接続・resetとの競合を独立レビューする。
現在のtimeoutを再送せず単に成功扱いにしない条件も維持する。追加の同期待機を
無期限で入れる案も、停止の保証やシステムの進行を示さないため採用しない。

closeは隔離済みjobだけなら完了しないjobを待たず直ちにfileから切り離し、
グローバルjob一覧およびVM保持は維持する。通常jobも残る場合は従来の期限を
使う。host fixtureはこの2ケースを実関数で確認するが、実機shutdownや競合の
安全性を証明するものではない。
