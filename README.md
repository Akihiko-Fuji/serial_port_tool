# serial_port_tool（`seria.py`）
> **A practical CLI tool for serial
> communication troubleshooting.**  
> Probe multiple ports and baud rates, detect line endings or custom delimiters, inspect text/binary payloads, and save reproducible logs as JSON.  
> Supports Linux, macOS, and Windows.

---

`seria.py` は、シリアル通信の障害切り分け・現地調査向けに作られた確認スクリプトです。  
「どのポートで」「どのボーレートで」「どんな終端（デリミタ）で」「何バイト/何文字のデータが来ているか」を、複数ポート × 複数ボーレートで同時に確認できます。  
1ファイルにまとめているため、利用環境にファイルを置けばすぐに使える設計です。

> **設計方針（トレードオフ）**  
> 本ツールは **現地調査での見逃しを避けることを最優先**にしています（反応性・確実性 > 省資源）。  
> そのため自動検索は取りこぼし防止を優先して対象を広めに取り、短時間実行を前提に設計しています。無通信時は `--timeout` 間隔で読み取りを繰り返すため、CPU 使用率が上がる場合があります。  
> また `--wait` は「概ねの最大待機時間」を与えるもので、実際の終了時刻は `--timeout` の設定や読み取り境界の都合でわずかに超過することがあります。

---
## Quick Start

```bash
pip install pyserial
python seria.py --help
python seria.py
python seria.py COM3 -b 9600,115200 --newline -n 3


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

Windowsの場合、実行ファイルがありますので[こちら](https://github.com/Akihiko-Fuji/serial_port_tool/raw/411d58521309343136531db4bc73f95f22bc4f0f/seria.zip)からダウンロードして下さい。

gitの場合は、このリポジトリを取得し、`seria.py` を実行します。

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

### 5.1 ポート / ボーレート

- `port`（位置引数、複数可）
  - ポート名またはワイルドカード
  - 省略時は自動検索
- `-b`, `--baudrate`
  - 例: `-b 9600,115200`
  - カンマ区切りで複数指定可
  - 既定: `9600`

### 5.2 読み取りモード（排他指定）

次の3つから1つを選択します（既定は改行終端モード）。

1. `--newline`
   - 既定モード（明示指定も可能）
   - `\n` / `\r\n` / `\r` 終端の行単位読み取り
2. `--delimiter HEX`
   - `read_until(expected=...)` を使用
   - 任意バイト列終端（16進数指定）
   - 例: `--delimiter 0D0A`（CRLF）, `--delimiter FF`
3. `--chunk N`
   - `read(N)` を使用
   - 固定長 N バイト読み取り

### 5.3 受信制御

- `-n`, `--lines <数>`
  - 取得するチャンク数（既定: `1`）
- `-w`, `--wait <秒>`
  - 最大待機時間（既定: `10`）
- `-t`, `--timeout <秒>`
  - シリアル `read()` タイムアウト（既定: `0.1`）

### 5.4 シリアルパラメータ

- `--bytesize 5|6|7|8`（既定: `8`）
- `--parity N|E|O|M|S`（既定: `N`）
- `--stopbits 1|1.5|2`（既定: `1`）
- `--rtscts`（RTS/CTS 有効）
- `--xonxoff`（XON/XOFF 有効）
- `--dsrdtr`（DSR/DTR 有効）

### 5.5 デコード

- `--encodings shift_jis,utf-8,cp932,ascii`
  - 受信 payload を順にデコード試行
  - 既定: `shift_jis,utf-8`
  - すべて失敗時は `binary` 扱い

### 5.6 出力

- `--json`
  - 結果 JSON を標準出力へ出力（stdout は JSON のみ）
  - 人間向けサマリは標準エラーへ出力
- `--json-file <path>`
  - 結果 JSON をファイル保存
- `--quiet`
  - 通常出力（active/silent の結果表示）を抑制
  - シリアルエラーが発生した組み合わせだけを標準エラーに表示
  - `--json` / `--json-file` と組み合わせると、監視バッチ向けの静かな実行が可能
- `--no-attr`
  - ポート属性（VID:PID、メーカー等）の表示省略


### 5.7 コンソール表示言語

- 既定は日本語表示です。
- ただし **Linux ローカルのプレーンコンソール（`TERM=linux`）** では英語表示に自動切り替えされます。
  - SSH 接続先ターミナル、X Window 上のターミナル、Windows/macOS は日本語表示のままです。
- 環境変数 `SERIA_FORCE_LANG` で強制指定できます。
  - `SERIA_FORCE_LANG=en` または `SERIA_FORCE_LANG=english` : 英語表示
  - `SERIA_FORCE_LANG=ja` または `SERIA_FORCE_LANG=jp` : 日本語表示

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
  - `port`, `baudrate`
  - `has_data`（1件以上チャンク受信したか）
  - `has_error`（オープン/読み取り/クローズ時に例外があったか）
  - `error`（エラー文字列。エラーなしは `null`）
  - `port_info`
  - `serial_params`
  - `chunk_count`
  - `chunks[]`（`repr`/`hex`/`raw_bytes`/`payload_bytes`/`delim_bytes`/`terminator`/`encoding`/`decoded`/`char_count`/`bytes_per_char`/`frame_complete`/`reason`）

障害解析ログとして、後で実行条件を再現しやすい形を目指しています。

補足:
- `has_data` と `has_error` は直交するフラグです。
  例: 受信自体は成功したが `close()` でエラーになった場合、`has_data=true` かつ `has_error=true` になり得ます。
- newline モードでは CR が末尾で分割到着した場合に CRLF 判定を優先するため、期限付き待機中は次バイトを待ちます。
  ただし `deadline` が無い呼び出しでは無限待機しないよう、空読み時は pending を返す安全動作になっています。

---

## 8. 終了コード

- 現時点の実装は **0 / 1 の2値**です。
- `0`: 1つ以上の組み合わせで受信あり
- `1`: 受信なし（タイムアウト）、利用可能ポートなし、引数/設定エラー、実行時エラー など

CI や監視スクリプトから呼び出す際は終了コード判定を活用できます。

---

## 9. トラブルシューティング

- **ポートが見つからない**
  - ケーブル接続・電源・ドライバ確認
  - Linux は権限確認（`dialout` グループなど）
  - 明示指定で試す（例: `python seria.py /dev/ttyUSB0`）
- **文字化けする**
  - `--encodings` を追加（例: `shift_jis,utf-8,cp932,ascii`）
- **受信できない**
  - ボーレートを複数指定して探索
  - 終端モードを見直し（`--newline` / `--delimiter` / `--chunk`）
  - パリティ/ストップビット/フロー制御を機器仕様に合わせる
- **ポートが多すぎる、接続機器が多くて情報がたくさん出る**
  - ポートを明示指定して対象を絞ってください（例: `python seria.py COM3 COM5`）

---

## 10. このツールが返す答え

本ツールは「受信できた / できない」を **ポート×ボーレートごとに並列で実測**し、結果をログ（コンソール / JSON）として残します。

---

## 11. ライセンス

`LICENSE` を参照してください。
