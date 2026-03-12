# serial_port_tool（`seria.py`）

`seria.py` は、**シリアル通信の障害切り分け・現地調査向け**に作られた確認ツールです。  
「どのポートで」「どのボーレートで」「どんな終端（デリミタ）で」「何バイト/何文字のデータが来ているか」を、
**複数ポート × 複数ボーレートで同時に**確認できます。

---

## 1. 主な用途

- 接続機器がどのポートに割り当てられたかを素早く把握する
- 機器の通信速度（baudrate）が不明なときに候補を同時探索する
- 改行終端（LF/CRLF/CR）か、独自デリミタ終端かを判別する
- 受信データの内容（テキスト/バイナリ、HEX、文字数）を確認する
- 障害対応の記録として JSON 形式で証跡を残す

---

## 2. 動作環境

- Python: **3.7 以上**
- OS:
  - Linux: `/dev/rfcomm*`, `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/ttyS*` など
  - macOS: `/dev/tty.usbserial*`, `/dev/tty.usbmodem*`, `/dev/tty.Bluetooth*` など
  - Windows: `COM1`〜`COM256`（`pyserial` の列挙で検出）

### 必須ライブラリ

```bash
pip install pyserial
```

---

## 3. インストール / 配置

このリポジトリを取得し、`seria.py` を実行します。

```bash
git clone https://github.com/Akihiko-Fuji/serial_port_tool/
cd serial_port_tool
python seria.py --help
```

> `python` コマンド名は環境により `python3` の場合があります。

---

## 4. 基本的な使い方

### 4.1 引数なし（自動検索）

```bash
python seria.py
```

- OS に応じた代表的なデバイスパターン + `pyserial` の列挙結果を使い、ポートを自動探索
- 既定では `9600bps` / 改行終端モード / 1チャンク取得 / 最大10秒待機

### 4.2 ポートを明示指定

```bash
python seria.py /dev/rfcomm0
python seria.py "/dev/ttyUSB*"
python seria.py COM3
python seria.py COM3 COM5 /dev/rfcomm0
```

- 明示指定時は **指定したポートだけ**を対象にします（他ポート混入なし）
- Windows の `COMx` は `glob`/`os.path.exists` で判定しづらいため、内部で列挙結果と照合して存在確認します

---

## 5. オプション詳細

## 5.1 ポート / ボーレート

- `port`（位置引数、複数可）
  - ポート名またはワイルドカード
  - 省略時は自動検索
- `-b`, `--baudrate`
  - 例: `-b 9600,115200`
  - カンマ区切りで複数指定可
  - 既定: `9600`

## 5.2 読み取りモード（排他指定）

次の3つから1つを選択します（既定は改行終端モード）。

1. `--newline`
   - `readline()` を使用
   - `\n` / `\r\n` / `\r` 終端の行単位読み取り
2. `--delimiter HEX`
   - `read_until(expected=...)` を使用
   - 任意バイト列終端（16進数指定）
   - 例: `--delimiter 0D0A`（CRLF）, `--delimiter FF`
3. `--chunk N`
   - `read(N)` を使用
   - 固定長 N バイト読み取り

## 5.3 受信制御

- `-n`, `--lines <数>`
  - 取得するチャンク数（既定: `1`）
- `-w`, `--wait <秒>`
  - 最大待機時間（既定: `10`）
- `-t`, `--timeout <秒>`
  - シリアル `read()` タイムアウト（既定: `0.1`）

## 5.4 シリアルパラメータ

- `--bytesize 5|6|7|8`（既定: `8`）
- `--parity N|E|O|M|S`（既定: `N`）
- `--stopbits 1|1.5|2`（既定: `1`）
- `--rtscts`（RTS/CTS 有効）
- `--xonxoff`（XON/XOFF 有効）
- `--dsrdtr`（DSR/DTR 有効）

## 5.5 デコード

- `--encodings utf-8,cp932,ascii`
  - 受信 payload を順にデコード試行
  - 既定: `utf-8,shift_jis`
  - すべて失敗時は `binary` 扱い

## 5.6 出力

- `--json`
  - 結果 JSON を標準出力にも表示
- `--json-file <path>`
  - 結果 JSON をファイル保存
- `--quiet`
  - 起動メッセージ / チャンク受信進捗の抑制
- `--no-attr`
  - ポート属性（VID:PID、メーカー等）の表示省略

---

## 6. 実行例

### 6.1 Bluetoothポートで複数ボーレート探索

```bash
python seria.py /dev/rfcomm0 -b 9600,115200 -n 3 -w 20
```

### 6.2 複数ポートを同時監視

```bash
python seria.py COM3 COM5 -b 9600,115200
```

### 6.3 CR+LF終端で受信し JSON 保存

```bash
python seria.py --delimiter 0D0A -n 5 --json-file result.json
```

### 6.4 固定長 + 7E1 + RTS/CTS

```bash
python seria.py /dev/ttyUSB0 --chunk 16 --bytesize 7 --parity E --stopbits 1 --rtscts
```

### 6.5 調査ログ用途（静かに実行）

```bash
python seria.py COM3 --quiet --json-file incident.json
```

---

## 7. 出力の見方

### 7.1 コンソール出力（人間可読）

アクティブな組み合わせ（データ受信あり）ごとに、以下を表示します。

- ポート / ボーレート
- シリアル設定（例: 8N1、フロー制御 ON/OFF）
- ポート属性（VID:PID、manufacturer、product など）
- チャンクごとの:
  - `repr`（Python表現）
  - `hex`（16進ダンプ）
  - 終端情報
  - 受信バイト数（payload + delimiter）
  - デコード結果 / 文字数 / 平均 bytes/char

受信が無かった組み合わせは「データなし（タイムアウト）」、
ポートオープン失敗は「オープンエラー」で分類表示されます。

### 7.2 JSON 出力構造

主に以下の構造です。

- `metadata`
  - 実行時刻
  - 読み取りモード
  - delimiter / chunk_size
  - encodings
  - 指定ポート / baudrates / wait / timeout / serial設定 など
- `results[]`
  - `port`, `baudrate`, `status(active|silent|error)`
  - `port_info`
  - `serial_params`
  - `chunks[]`（repr/hex/bytes/decoded など）

障害解析ログとして、後で実行条件を再現しやすい形を目指しています。

---

## 8. 終了コード

- `0`: 1つ以上の組み合わせで受信あり
- `1`: 受信なし / ポート未検出 / 引数エラー など

CI や監視スクリプトから呼び出す際は終了コード判定を活用できます。

---

## 9. トラブルシューティング

- **ポートが見つからない**
  - ケーブル接続・電源・ドライバ確認
  - Linux は権限確認（`dialout` グループなど）
  - 明示指定で試す（例: `python seria.py /dev/ttyUSB0`）
- **文字化けする**
  - `--encodings` を追加（例: `utf-8,cp932,shift_jis,ascii`）
- **受信できない**
  - ボーレートを複数指定して探索
  - 終端モードを見直し（`--newline` / `--delimiter` / `--chunk`）
  - パリティ/ストップビット/フロー制御を機器仕様に合わせる

---

## 10. ライセンス

`LICENSE` を参照してください。
