# 充電 owner の時間切れ観測候補

実機初回 UART では firmware handoff の last=293848740、exit=396754327、
now=406275539、freq=13000000。最終 pet から handoff まで 8.648215 秒、
20 秒制限まで約 11.351785 秒。XNU の最初の snapshot は valid=ff、
raw=30,00,8c,a4,45,51,00,38、result=1 (Deadline)、kicks=0。
取得完了時刻や各 read 時刻は旧版にないため、handoff 期限切れと、
snapshot 全体 1 秒制限を区別できない。`history=1` は fault code 0 の
観測であり故障 bit 1 を示さない。UART の他処理出力の順序のみで
スレッドの停止時間を確定しない。

この候補は snapshot、handoff、20 秒制限、1 秒制限、電圧・電流・
温度の保護を変更しない。開始前の同期ログを削減し、handoff 検証、
service 開始、初期化、資源確認、step と各 read の時刻を記録する。
時刻は XNU の同じ nanoseconds 時計で比較する。handoff は物理カウンタで
経過を求めたうえで XNU 時計の deadline へ変換する。移行後の時間切れが
既に判明したときは I2C 初期化自体を行わない。

診断が原因を解消する保証はない。スケジューリングや I2C の期限を
緩める根拠は未取得。`a04br3-gauge-voltage=1` の LC709203F 測定は
owner の step が Maintained のときだけ同一 lock 内で行う。そのため
owner が先に止まる場合、電圧は得られない。現在 fastboot にいる端末では
このコードは実行されない。対象 SDK ビルド、実機起動、継続 pet、
電圧および充電改善は未検証。
