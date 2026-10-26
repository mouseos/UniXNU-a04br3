# LC709203F 電圧sidecar（既定OFF、実機未検証）

固定owner公開ref `46d183e` と固定FDは変更しない。これは次のkernel候補であり、
Chandra再レビュー・SDK compile・主担当の実機確認を経るまで公開済みownerの合格に含めない。

## 取得するもの

`a04br3-charger-owner=1` に加えて `a04br3-gauge-voltage=1` を明示した場合だけ、
BQ ownerが維持成功した直後、同じI2C2 lock内でLC709203Fを読み出す。
7bit address0x0b、read対象はIC Power Mode0x15とCell Voltage0x09だけ。
Operational mode=1、CRC-8、厳密3 byte FIFOが成立した場合だけvalid=1とする。
メーカー保証動作範囲2.5..4.5V外ではrawを残してvalid=0。これは充電policyの制御ではない。

出力は時刻、valid、raw、mode、gauge VDDのmV。失敗時はmV=unknown、常に
current=unknownとする。battery％、mA、mW、充電改善、温度を推定しない。
Sleep modeは過去値を新鮮な測定値にしない。mode変更・SDA wake pulse・RSOC初期化・
battery profile・APA・alarm設定は行わない。I2C command pointerのwrite phaseはあるが、
gauge設定dataのwriteはない。読み出しでも通信/wake条件へ影響し得るため、完全無副作用とは呼ばない。

初期版はowner停止時またはowner OFF時には読み出さない。したがって、これだけでは
owner ON/OFFの同条件比較はできず、給電改善の因果判定には足りない。基準測定が必要なら、
BQを再給餌せず独立にbus所有を引き受けるvoltage-only modeを別レビューで設計する。

1 transactionは2000 polls/10 ms、sample全体は100 msで失効する。read failure、
NACK、CRC不一致、FIFO不足/過剰、busy、期限切れはunknown。reset/retryは追加しない。
BQの既存結果を上書きせず、次回owner期限判定と停止契約を保つ。

## 根拠

- 原本DTS `CT3_GPL_LGPL_Src/kernel-4.4/arch/arm64/boot/dts/mediatek/a04br3.dts`：I2C2 0x0b、搭載LC709203F。
- 原本 `drivers/misc/mediatek/power/lc709203f.c:115`：low/high/PECのread、CRCにwrite addr0x16、command、read addr0x17を含む。原本のshort-read判定不足はコピーしない。
- 既存Linux port `port/edk2/a04br3-linux-drivers/power/a04br3-lc709203f.c`：厳密3 byte、PEC、VOLTAGE_NOW=raw*1000µVを公開。XNUで公開済みのendpointではない。
- onsemi LC709203F/D Rev17（2020-11）、pp7..12：Read Word CRC、Cell Voltage=VDD mV、IC Power Mode。メーカーURLはtoolから取得できず、メーカー作成PDFのMouser配布写しを照合した。
  https://www.mouser.com/datasheet/2/308/LC709203F_D-1141798.pdf
- メーカーgolden vector：0x16,09,17,C2,0EのPECは0x86、3778mV。ホスト試験はこの独立定数を使う。

これはLC内蔵ADCの既存数値registerを取得するもので、PMIC ADC換算の新実装ではない。
端末固有の校正値を別端末からコピーせず、量産用packパラメータを推測しない。
新PMIC ADC/current経路を追加する場合は、実搭載MT6392の参照driver、配線/分圧/
current shuntの根拠、当該実機からの動的校正、共有PWRAPの排他が別途必要。
MT6323の換算をMT6392へそのまま適用しない。

## 実測改善の判定限界

LC709203Fはsense resistorなしでRSOCを推定するICであり、数値mAを公開する根拠がない。
原本battery_meter_hw_lc709203f.cのfgauge_read_currentは空実装。
ISENSE名のADC channelも、powerpath構成ではsystem voltageと記され、名前だけで電流と扱えない。
電圧の上昇/低下やBQの設定維持だけから、USB入力電流増加・正味充電電流を断定しない。
同一負荷・開始電圧・温度・経過時間を揃えた比較と、独立した入力/電池電流観測が必要。

## 検証

```
python3 scripts/tests/test_a04br3_gauge.py
python3 scripts/tests/test_a04br3_charger.py
```

実際のPIO/PECヘッダをASan/UBSanで実行。メーカーgolden、mode/CRC/FIFO、busy/NACK/
timeout/range、command whitelist、same bus lock/opt-in接続を確認する。
後者は既存owner試験と、actual board sourceのAArch64宣言stub付きsyntax check。
実機の測定値やSDK build成功を意味しない。
