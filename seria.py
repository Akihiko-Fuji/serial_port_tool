#!/usr/bin/env python3
# =============================================================================
# seria.py  ―  シリアルポート確認ツール（拡張版）
#
# 目的: どのポートからデータが来ているか、デリミタは何か、桁数はどうかを確認する
#       障害発生時など、シリアル通信の状態をすばやく把握するために使う
#
# 動作環境:
#   - Linux  : /dev/rfcomm*, /dev/ttyUSB*, /dev/ttyACM*, /dev/ttyS* など
#   - macOS  : /dev/tty.usbserial*, /dev/tty.usbmodem*, /dev/tty.Bluetooth* など
#   - Windows: COM1〜COM256（pyserial の list_ports 経由で自動検出）
#   - Python : 3.7 以上
#
# 必要ライブラリ（標準ライブラリ以外）:
#   pip install pyserial
#
# =============================================================================
# 使い方
# =============================================================================
#
# [基本] 引数なしで起動 → 接続中のポートを全自動検出して同時監視
#   python seria.py
#
# [ポート指定] 特定ポートだけを見る（ワイルドカード可・複数指定可）
#   python seria.py /dev/rfcomm0
#   python seria.py "/dev/ttyUSB*"
#   python seria.py COM3
#   python seria.py COM3 COM5 /dev/rfcomm0   # 複数ポートを並べて指定
#
# ----------------------------------------------------------------------------
# オプション早見表
# ----------------------------------------------------------------------------
#
# ポート・ボーレート
#   (positional, 複数可)    ポート名またはワイルドカード（省略=自動検索）
#   -b 9600,115200          ボーレートをカンマ区切りで複数指定
#                             複数指定時はどの速度で反応するかを一度に探索する
#                             （デフォルト: 9600）
#
# 読み取りモード（3 種類から 1 つ選ぶ、デフォルトは --newline）
#   --newline               改行（\n / \r\n / \r）を終端として 1 行ずつ読む
#                             例: python seria.py --newline
#   --delimiter "0D0A"      任意バイト列を終端として読む（16進数で指定）
#                             例: python seria.py --delimiter "0D"       # CR のみ
#                             例: python seria.py --delimiter "0D0A"     # CR+LF
#                             例: python seria.py --delimiter "FF"       # 0xFF
#   --chunk 16              固定長バイト数で読む
#                             例: python seria.py --chunk 16
#
# 受信制御
#   -n, --lines <数値>      取得チャンク数（デフォルト: 1）
#                             例: python seria.py -n 5
#   -w, --wait <秒>         最大待機時間（デフォルト: 10 秒）
#                             例: python seria.py -w 30
#   -t, --timeout <秒>      read() のタイムアウト（デフォルト: 0.1 秒、通常変更不要）
#
# シリアルパラメータ（デフォルトは 8N1、フロー制御なし）
#   --bytesize 5|6|7|8      データビット数（デフォルト: 8）
#   --parity N|E|O|M|S      パリティ N=なし E=偶数 O=奇数 M=Mark S=Space
#                             （デフォルト: N）
#   --stopbits 1|1.5|2      ストップビット数（デフォルト: 1）
#   --rtscts                RTS/CTS ハードウェアフロー制御を有効にする
#   --xonxoff               XON/XOFF ソフトウェアフロー制御を有効にする
#   --dsrdtr                DSR/DTR ハードウェアフロー制御を有効にする
#
# デコード
#   --encodings utf-8,cp932,ascii
#                           試みるエンコーディングをカンマ区切りで指定
#                             デフォルト: utf-8,shift_jis
#                             例: python seria.py --encodings utf-8,cp932,ascii
#
# 出力形式
#   --json                  結果を JSON 形式で標準出力に出す（障害記録用）
#   --json-file <path>      JSON をファイルに保存する
#                             例: python seria.py --json-file result.json
#   --quiet                 進捗ログ（チャンク受信通知・起動メッセージ）を抑制する
#                             リダイレクト先のログをきれいに保ちたい場合に使う
#   --no-attr               ポート属性（VID:PID・メーカー等）の表示を省略する
#
# ----------------------------------------------------------------------------
# 組み合わせ例
# ----------------------------------------------------------------------------
#
#   # Bluetooth で 9600/115200 の両方を試して 3 チャンク取得、20 秒待機
#   python seria.py /dev/rfcomm0 -b 9600,115200 -n 3 -w 20
#
#   # 複数ポートを明示指定して同時監視
#   python seria.py COM3 COM5 -b 9600,115200
#
#   # 全ポート自動検索、CR+LF デリミタ、5 チャンク取得、結果を JSON 保存
#   python seria.py --delimiter "0D0A" -n 5 --json-file result.json
#
#   # 固定長 16 バイトで読む、7E1、RTS/CTS あり
#   python seria.py /dev/ttyUSB0 --chunk 16 --bytesize 7 --parity E --stopbits 1 --rtscts
#
#   # 全ポート・全ボーレードで反応するものを探す、属性省略、JSON 保存
#   python seria.py -b 9600,19200,38400,57600,115200 --no-attr --json-file scan.json
#
#   # 障害ログ用：静かに動かして JSON だけ残す
#   python seria.py COM3 --quiet --json-file incident.json
#
# =============================================================================

import codecs
import serial
import serial.tools.list_ports
import time
import os
import sys
import glob
import json
import argparse
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

# ==============================
# デフォルト設定
# ==============================
# 引数なし起動時にワイルドカード検索するパスパターン（Linux / macOS 用）
# Windows の COM ポートは glob では検出できないため list_ports で別途補完する
DEFAULT_PORT_PATTERNS = [
    '/dev/rfcomm*',         # Linux: Bluetooth SPP
    '/dev/ttyUSB*',         # Linux: USB-シリアル変換（CH340 / FT232 など）
    '/dev/ttyACM*',         # Linux: CDC-ACM（Arduino など）
    '/dev/ttyS*',           # Linux: 物理シリアルポート
    '/dev/tty.usbserial*',  # macOS: USB-シリアル変換
    '/dev/tty.usbmodem*',   # macOS: CDC-ACM
    '/dev/tty.Bluetooth*',  # macOS: Bluetooth SPP
]

DEFAULT_BAUDRATE   = 9600
DEFAULT_TIMEOUT    = 0.1
DEFAULT_WAIT_SEC   = 10
DEFAULT_ENCODINGS  = ['utf-8', 'shift_jis']  # --encodings のデフォルト値
NEWLINE_TERMINATORS: Sequence[Tuple[bytes, str]] = (
    (b'\r\n', "CR+LF (\\r\\n)"),
    (b'\r', "CR のみ (\\r)"),
    (b'\n', "LF のみ (\\n)"),
)

# pyserial の定数マッピング（引数文字列 → serial モジュール定数）
BYTESIZE_MAP = {
    5: serial.FIVEBITS,
    6: serial.SIXBITS,
    7: serial.SEVENBITS,
    8: serial.EIGHTBITS,
}
PARITY_MAP = {
    'N': serial.PARITY_NONE,
    'E': serial.PARITY_EVEN,
    'O': serial.PARITY_ODD,
    'M': serial.PARITY_MARK,
    'S': serial.PARITY_SPACE,
}
STOPBITS_MAP = {
    1:   serial.STOPBITS_ONE,
    1.5: serial.STOPBITS_ONE_POINT_FIVE,
    2:   serial.STOPBITS_TWO,
}
# DSR/DTR フロー制御は bool フラグで serial.Serial に渡すため定数マップは不要


# ==============================
# 読み取りモード設定
# ==============================
@dataclass
class ReadMode:
    """
    読み取りモードを 1 つの dataclass にまとめる。
      mode      : 'newline' | 'delimiter' | 'chunk'
      delimiter : mode='delimiter' のときの終端バイト列
      chunk_size: mode='chunk' のときのバイト数
    """
    mode: str = 'newline'
    delimiter: bytes = b''
    chunk_size: int = 0
    selected_by_user: bool = False


@dataclass
class LineBufferedReader:
    """改行終端モード用の内部バッファ。"""
    pending: bytearray = field(default_factory=bytearray)

    def read_line(self, ser: serial.Serial) -> bytes:
        while True:
            for idx, value in enumerate(self.pending):
                if value == 0x0A:  # LF
                    return self._consume(idx + 1)
                if value == 0x0D:  # CR
                    if idx + 1 < len(self.pending) and self.pending[idx + 1] == 0x0A:
                        return self._consume(idx + 2)
                    return self._consume(idx + 1)

            read_size = max(getattr(ser, 'in_waiting', 0), 64)
            chunk = ser.read(read_size)
            if not chunk:
                if self.pending:
                    return self._consume(len(self.pending))
                return b''
            self.pending.extend(chunk)

    def _consume(self, size: int) -> bytes:
        data = bytes(self.pending[:size])
        del self.pending[:size]
        return data


# ==============================
# ポート探索
# ==============================
def list_port_info_map() -> Dict[str, serial.tools.list_ports_common.ListPortInfo]:
    """OS が認識しているポート情報を device 名をキーにした辞書で返す。"""
    return {info.device: info for info in serial.tools.list_ports.comports()}


def find_ports(
    patterns: Optional[List[str]] = None,
    explicit: bool = False,
    known_ports: Optional[Sequence[str]] = None,
) -> List[str]:
    """
    実在するシリアルポートを返す（重複なし・ソート済み）。

    explicit=False（自動検索モード）:
      1. patterns を glob で展開（Linux / macOS のワイルドカード対応）
      2. serial.tools.list_ports.comports() で OS 認識ポートを補完
         → Windows の COM ポートはこちらでのみ確実に検出できる

    explicit=True（明示指定モード）:
      patterns で指定したポートだけを対象にする。
      comports() による補完は行わない。
      → 「この 1 本だけ見たい」場合に他のポートが混入しない。
      → Windows で COM3 を明示指定した場合は os.path.exists が効かないため、
         comports() で存在確認だけ行い、一致したポートのみ追加する。
    """
    found = set()
    known_port_set = set(known_ports) if known_ports is not None else set(list_port_info_map().keys())

    for pattern in (patterns or []):
        expanded = glob.glob(pattern)
        if expanded:
            # ワイルドカードが展開できた場合（例: /dev/ttyUSB*）
            found.update(expanded)
        elif os.path.exists(pattern):
            # Unix 系の直接指定（例: /dev/rfcomm0）
            found.add(pattern)
        elif explicit:
            # Windows の COM ポートは glob も os.path.exists も効かない。
            # 明示指定時に限り comports() で存在確認して一致分だけ追加する。
            if pattern in known_port_set:
                found.add(pattern)
            else:
                print(f"Warning: 指定ポートが見つかりません: {pattern}", file=sys.stderr)

    if not explicit:
        # 自動検索モードのみ全ポートを comports() で補完
        found.update(known_port_set)

    return sorted(found)


def get_port_info(port: str, port_info_map: Optional[Dict[str, serial.tools.list_ports_common.ListPortInfo]] = None) -> Dict:
    """
    pyserial の list_ports からポートの属性情報を取得して返す。
    VID:PID、メーカー、製品名、シリアル番号、説明など。
    """
    info_map = port_info_map or list_port_info_map()
    info = info_map.get(port)
    if info:
        vid = info.vid
        pid = info.pid
        return {
            'vid_pid'     : f"{vid:04X}:{pid:04X}" if vid is not None and pid is not None else None,
            'manufacturer': info.manufacturer,
            'product'     : info.product,
            'serial_number': info.serial_number,
            'description' : info.description,
            'interface'   : info.interface,
        }
    return {}


# ==============================
# データ解析ユーティリティ
# ==============================
def check_terminator(data: bytes) -> str:
    """末尾の改行種別を人間が読める文字列で返す"""
    for terminator, label in NEWLINE_TERMINATORS:
        if data.endswith(terminator):
            return label
    return "改行なし"


def terminator_bytes(data: bytes) -> bytes:
    """末尾の改行バイト列を返す（改行なしは空バイト列）"""
    for terminator, _ in NEWLINE_TERMINATORS:
        if data.endswith(terminator):
            return terminator
    return b''


def decode_data(
    data: bytes,
    encodings: Optional[List[str]] = None,
    strip_newline: bool = True,
) -> Tuple[str, str]:
    """
    (エンコーディング名, デコード文字列) を返す。失敗時は ('binary', '')。
    encodings には試みるエンコーディングをリストで渡す。
    省略時は DEFAULT_ENCODINGS を使う。
    strip_newline=True の場合、デコード後文字列の末尾 CR/LF を除去する。
    """
    if b'\x00' in data:
        return 'binary', ''

    for enc in (encodings or DEFAULT_ENCODINGS):
        try:
            decoded = data.decode(enc)
            if not looks_like_text(decoded):
                continue
            if strip_newline:
                decoded = decoded.rstrip('\r\n')
            return enc, decoded
        except UnicodeDecodeError:
            # このエンコーディングではデコードできないため次候補へ。
            continue
        except LookupError:
            # 不正なエンコーディング名はこの候補のみスキップする。
            continue
    return 'binary', ''


def looks_like_text(decoded: str) -> bool:
    """バイナリ混入を避けるための簡易テキスト判定。"""
    if not decoded:
        return True

    allowed_controls = {'\n', '\r', '\t'}
    control_count = 0
    printable_count = 0
    for ch in decoded:
        if ch.isprintable() or ch in allowed_controls:
            printable_count += 1
        elif ord(ch) < 32 or ord(ch) == 127:
            control_count += 1

    total = len(decoded)
    printable_ratio = printable_count / total
    control_ratio = control_count / total
    return printable_ratio >= 0.7 and control_ratio <= 0.1

def parse_baudrates(raw: str) -> List[int]:
    """--baudrate の入力文字列を整数リストに変換する。"""
    segments = [value.strip() for value in raw.split(',')]
    if not segments or any(not value for value in segments):
        raise ValueError('invalid baudrate')

    try:
        baudrates = [int(value) for value in segments]
    except ValueError as exc:
        raise ValueError('invalid baudrate') from exc

    if not baudrates or any(rate <= 0 for rate in baudrates):
        raise ValueError('invalid baudrate')
    return baudrates


def resolve_read_mode(args: argparse.Namespace) -> ReadMode:
    """引数から読み取りモードを決定する。"""
    if args.delimiter is not None:
        delimiter_hex = args.delimiter.replace(' ', '')
        if not delimiter_hex:
            raise ValueError('empty delimiter')

        try:
            delimiter = bytes.fromhex(delimiter_hex)
        except ValueError as exc:
            raise ValueError('invalid delimiter') from exc

        if not delimiter:
            raise ValueError('empty delimiter')

        return ReadMode(mode='delimiter', delimiter=delimiter, selected_by_user=True)

    if args.chunk is not None:
        if args.chunk <= 0:
            raise ValueError('invalid chunk size')
        return ReadMode(mode='chunk', chunk_size=args.chunk, selected_by_user=True)
    return ReadMode(mode='newline', selected_by_user=args.newline)


def parse_encodings(raw: Optional[str]) -> List[str]:
    """--encodings の入力文字列を正規化して返す。"""
    if raw is None:
        return DEFAULT_ENCODINGS

    encodings = [encoding.strip() for encoding in raw.split(',') if encoding.strip()]
    if not encodings:
        raise ValueError('empty encodings')

    for encoding in encodings:
        try:
            codecs.lookup(encoding)
        except LookupError as exc:
            raise ValueError(f'invalid encoding: {encoding}') from exc

    return encodings


def build_serial_config(args: argparse.Namespace) -> Dict:
    """pyserial のオープン設定と表示用ラベルを構築する。"""
    return {
        'bytesize': BYTESIZE_MAP[args.bytesize],
        'parity': PARITY_MAP[args.parity],
        'stopbits': STOPBITS_MAP[args.stopbits],
        'rtscts': args.rtscts,
        'xonxoff': args.xonxoff,
        'dsrdtr': args.dsrdtr,
        # JSON / 表示用のラベル
        'bytesize_label': args.bytesize,
        'parity_label': args.parity,
        'stopbits_label': args.stopbits,
    }


def build_parser() -> argparse.ArgumentParser:
    """CLI 引数パーサを構築して返す。"""
    parser = argparse.ArgumentParser(
        description='複数シリアルポートを同時監視して、受信データの改行/デリミタ/桁数/文字化けを確認するツール'
    )

    # --- ポート指定（複数可・ワイルドカード可） ---
    parser.add_argument(
        'port', nargs='*', default=[],
        help="ポート名またはワイルドカード（複数可、省略=自動検索）\n"
             "例: /dev/rfcomm0  /dev/ttyUSB*  COM3 COM5"
    )

    # --- ボーレート（複数可） ---
    parser.add_argument(
        '-b', '--baudrate', default=str(DEFAULT_BAUDRATE),
        help="ボーレートをカンマ区切りで指定 (例: 9600,115200)  デフォルト: 9600"
    )

    # --- 読み取りモード（排他） ---
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        '--newline', action='store_true',
        help="改行終端モード（デフォルト）: \\n / \\r\\n / \\r を終端として読む"
    )
    mode_group.add_argument(
        '--delimiter',
        metavar='HEX',
        help="任意デリミタモード: 終端バイト列を 16 進数で指定 (例: 0D0A = CR+LF, FF)"
    )
    mode_group.add_argument(
        '--chunk', type=int,
        metavar='N',
        help="固定長モード: N バイトずつ読む (例: --chunk 16)"
    )

    # --- 受信制御 ---
    parser.add_argument(
        '-n', '--lines', type=int, default=1,
        help="取得チャンク数（デフォルト: 1）"
    )
    parser.add_argument(
        '-w', '--wait', type=int, default=DEFAULT_WAIT_SEC,
        help=f"最大待機秒（デフォルト: {DEFAULT_WAIT_SEC}）"
    )
    parser.add_argument(
        '-t', '--timeout', type=float, default=DEFAULT_TIMEOUT,
        help=f"read() タイムアウト秒（デフォルト: {DEFAULT_TIMEOUT}、通常変更不要）"
    )

    # --- シリアルパラメータ ---
    parser.add_argument(
        '--bytesize', type=int, default=8, choices=[5, 6, 7, 8],
        help="データビット数（デフォルト: 8）"
    )
    parser.add_argument(
        '--parity', default='N', choices=['N', 'E', 'O', 'M', 'S'],
        help="パリティ N=なし E=偶数 O=奇数 M=Mark S=Space（デフォルト: N）"
    )
    parser.add_argument(
        '--stopbits', type=float, default=1.0, choices=[1, 1.5, 2],
        help="ストップビット数（デフォルト: 1）"
    )
    parser.add_argument(
        '--rtscts', action='store_true',
        help="RTS/CTS ハードウェアフロー制御を有効にする"
    )
    parser.add_argument(
        '--xonxoff', action='store_true',
        help="XON/XOFF ソフトウェアフロー制御を有効にする"
    )
    parser.add_argument(
        '--dsrdtr', action='store_true',
        help="DSR/DTR ハードウェアフロー制御を有効にする"
    )

    # --- デコード ---
    parser.add_argument(
        '--encodings', default=None,
        metavar='ENC,...',
        help="試みるエンコーディングをカンマ区切りで指定\n"
             f"デフォルト: {','.join(DEFAULT_ENCODINGS)}\n"
             "例: --encodings utf-8,cp932,ascii"
    )

    # --- 出力形式 ---
    parser.add_argument(
        '--json', action='store_true',
        help="結果を JSON 形式で標準出力に出す（stdout は JSON のみ）"
    )
    parser.add_argument(
        '--json-file', metavar='PATH',
        help="結果を JSON ファイルに保存する (例: --json-file result.json)"
    )
    parser.add_argument(
        '--quiet', action='store_true',
        help="標準出力/標準エラーの通常表示を抑制する（エラーのみ出力）"
    )
    parser.add_argument(
        '--no-attr', action='store_true',
        help="ポート属性（VID:PID・メーカー等）の表示を省略する"
    )
    return parser


def print_startup_summary(
    ports: Sequence[str],
    baudrates: Sequence[int],
    read_mode: ReadMode,
    encodings: Sequence[str],
    no_attr: bool,
    port_info_map: Optional[Dict[str, serial.tools.list_ports_common.ListPortInfo]] = None,
) -> None:
    """監視開始前の設定と検出ポート情報を表示する。"""
    print(f"検出されたポート  ({len(ports)} 件): {', '.join(ports)}", file=sys.stderr)
    print(f"ボーレート候補    ({len(baudrates)} 件): {', '.join(str(b) for b in baudrates)}", file=sys.stderr)
    print(
        f"読み取りモード   : {read_mode.mode}"
        + (f" / デリミタ: {read_mode.delimiter.hex(' ').upper()}" if read_mode.mode == 'delimiter' else '')
        + (f" / {read_mode.chunk_size} bytes" if read_mode.mode == 'chunk' else '')
    , file=sys.stderr)
    print(f"エンコーディング : {', '.join(encodings)}", file=sys.stderr)

    if no_attr:
        return

    print("\n--- 検出ポート属性 ---", file=sys.stderr)
    for port in ports:
        info = get_port_info(port, port_info_map=port_info_map)
        print(f"  {port}", file=sys.stderr)
        print_port_info(info, stream=sys.stderr)
        if not info:
            print("    （属性情報なし）", file=sys.stderr)


def chunk_stats(data: bytes, read_mode: ReadMode,
                encodings: Optional[List[str]] = None) -> Dict:
    """
    1 チャンク分のバイト数・文字数統計を返す。
      raw_bytes     : 受信バイト数（デリミタ込み）
      delim_bytes   : 末尾デリミタのバイト数（固定長モードは 0）
      payload_bytes : データ本体のバイト数（デリミタ除く）
      encoding      : デコードに使ったエンコーディング
      char_count    : デコード後の文字数（バイナリは None）
      bytes_per_char: 平均バイト/文字（バイナリは None）
      terminator    : 末尾の改行種別文字列（newline モード以外は種別説明）
    encodings には試みるエンコーディングをリストで渡す（省略時は DEFAULT_ENCODINGS）。
    """
    if read_mode.mode == 'newline':
        term_b = terminator_bytes(data)
        delim_len = len(term_b)
        terminator_label = check_terminator(data)
    elif read_mode.mode == 'delimiter':
        delim_len = len(read_mode.delimiter) if data.endswith(read_mode.delimiter) else 0
        terminator_label = f"delimiter ({read_mode.delimiter.hex(' ').upper()})"
    else:  # chunk
        delim_len = 0
        terminator_label = f"固定長 {read_mode.chunk_size} bytes"

    payload = data[: len(data) - delim_len]
    # デコードは終端を除いた payload に対して行う。
    # data 全体に対してデコードすると、終端バイトが CR/LF 以外の任意バイト列の場合に
    # decoded 文字列に終端が混入したり、デコード失敗する可能性がある。
    enc, decoded = decode_data(payload, encodings, strip_newline=False)
    char_count = len(decoded) if enc != 'binary' else None
    bpc = (len(payload) / char_count) if char_count is not None and char_count > 0 else None

    return {
        'raw_bytes'     : len(data),
        'delim_bytes'   : delim_len,
        'payload_bytes' : len(payload),
        'encoding'      : enc,
        'char_count'    : char_count,
        'bytes_per_char': round(bpc, 3) if bpc is not None else None,
        'terminator'    : terminator_label,
        'decoded'       : decoded,
    }


# ==============================
# 単一ポート監視（スレッド用）
# ==============================
@dataclass
class PortResult:
    port: str
    baudrate: int
    port_info: Dict = field(default_factory=dict)
    chunks: List[Dict] = field(default_factory=list)
    error: str = ''
    serial_params: Dict = field(default_factory=dict)


def read_one_chunk(ser: serial.Serial, read_mode: ReadMode, line_reader: Optional[LineBufferedReader] = None) -> bytes:
    """
    読み取りモードに応じて 1 チャンク分のデータを読んで返す。
      newline  : \r\n / \r / \n を終端として読む（自前実装）
      delimiter: read_until() ― 任意バイト列を終端として読む
      chunk    : read(n)    ― 固定長バイト数を読む
    """
    if read_mode.mode == 'newline':
        reader = line_reader or LineBufferedReader()
        return reader.read_line(ser)
    elif read_mode.mode == 'delimiter':
        return ser.read_until(expected=read_mode.delimiter)
    else:
        return ser.read(read_mode.chunk_size)


def classify_chunk(data: bytes, read_mode: ReadMode) -> Tuple[bool, str]:
    """受信チャンクが終端まで到達した完全フレームかどうかを返す。"""
    if read_mode.mode == 'newline':
        complete = any(data.endswith(t) for t, _ in NEWLINE_TERMINATORS)
        return complete, ('newline_terminator_found' if complete else 'timeout_partial')

    if read_mode.mode == 'delimiter':
        complete = data.endswith(read_mode.delimiter)
        return complete, ('delimiter_found' if complete else 'timeout_partial')

    complete = len(data) == read_mode.chunk_size
    return complete, ('fixed_size_complete' if complete else 'timeout_partial')


def monitor_port(
    port: str,
    baudrate: int,
    timeout: float,
    wait_sec: int,
    num_chunks: int,
    read_mode: ReadMode,
    serial_cfg: Dict,
    result: PortResult,
    lock: threading.Lock,
    quiet: bool = False,
) -> None:
    """スレッド内で 1 ポートを監視し、結果を result に格納する"""
    try:
        ser = serial.Serial(
            port,
            baudrate,
            timeout         = timeout,
            bytesize        = serial_cfg.get('bytesize', serial.EIGHTBITS),
            parity          = serial_cfg.get('parity',   serial.PARITY_NONE),
            stopbits        = serial_cfg.get('stopbits', serial.STOPBITS_ONE),
            rtscts          = serial_cfg.get('rtscts',   False),
            xonxoff         = serial_cfg.get('xonxoff',  False),
            dsrdtr          = serial_cfg.get('dsrdtr',   False),
        )
    except serial.SerialException as e:
        result.error = str(e)
        return

    collected: List[Dict] = []
    line_reader = LineBufferedReader()
    deadline = time.monotonic() + wait_sec
    try:
        while time.monotonic() < deadline:
            try:
                chunk = read_one_chunk(ser, read_mode, line_reader=line_reader)
            except ReferenceError as e:
                # pyserial の内部オブジェクトが GC された場合など、
                # まれに weakref 由来の ReferenceError が伝播することがある。
                result.error = f"ReferenceError while reading serial port: {e}"
                break
            except (serial.SerialException, OSError) as e:
                result.error = f"I/O error while reading serial port: {e}"
                break
            if chunk:
                frame_complete, reason = classify_chunk(chunk, read_mode)
                collected.append({
                    'data': chunk,
                    'frame_complete': frame_complete,
                    'reason': reason,
                })
                if not quiet:
                    with lock:
                        print(f"  [{port}@{baudrate}] チャンク {len(collected)}: {repr(chunk)}", file=sys.stderr)
                if len(collected) >= num_chunks:
                    break
    finally:
        try:
            ser.close()
        except (ReferenceError, serial.SerialException, OSError):
            # クローズ時に weakref 由来エラーが発生しても、取得済みデータは保持する。
            if not result.error:
                result.error = "ReferenceError while closing serial port"

    result.chunks = collected


# ==============================
# ポート単位監視（同一ポート内のボーレート探索は直列）
# ==============================
def monitor_port_all_baudrates(
    port: str,
    baudrates: Sequence[int],
    timeout: float,
    wait_sec: int,
    num_chunks: int,
    read_mode: ReadMode,
    serial_cfg: Dict,
    results: List[PortResult],
    lock: threading.Lock,
    quiet: bool = False,
    port_info_map: Optional[Dict[str, serial.tools.list_ports_common.ListPortInfo]] = None,
) -> None:
    """1ポートを担当し、指定されたボーレート候補を順に試す。"""
    for baud in baudrates:
        r = PortResult(
            port=port,
            baudrate=baud,
            port_info=get_port_info(port, port_info_map=port_info_map),
            serial_params={
                'bytesize': serial_cfg.get('bytesize_label', 8),
                'parity'  : serial_cfg.get('parity_label',   'N'),
                'stopbits': serial_cfg.get('stopbits_label',  1),
                'rtscts'  : serial_cfg.get('rtscts',          False),
                'xonxoff' : serial_cfg.get('xonxoff',         False),
                'dsrdtr'  : serial_cfg.get('dsrdtr',          False),
            },
        )
        monitor_port(
            port, baud, timeout, wait_sec, num_chunks, read_mode,
            serial_cfg, r, lock, quiet
        )
        with lock:
            results.append(r)


def monitor_all(
    ports: List[str],
    baudrates: List[int],
    timeout: float,
    wait_sec: int,
    num_chunks: int,
    read_mode: ReadMode,
    serial_cfg: Dict,
    quiet: bool = False,
    port_info_map: Optional[Dict[str, serial.tools.list_ports_common.ListPortInfo]] = None,
) -> List[PortResult]:
    """
    ポートごとにスレッドを 1 本ずつ起動し、
    同一ポート内のボーレート探索は直列で実行する。
    quiet=True のときは進捗ログを出さない。
    """
    results: List[PortResult] = []
    lock = threading.Lock()
    threads = []

    combos = [(p, b) for p in ports for b in baudrates]
    if not quiet:
        print(f"\n{len(combos)} 組合せ（{len(ports)} ポート × {len(baudrates)} ボーレート）を監視開始"
              f"（ポート間は並列、同一ポート内は直列 / 最大 {wait_sec} 秒）…", file=sys.stderr)

    for port in ports:
        t = threading.Thread(
            target=monitor_port_all_baudrates,
            args=(port, baudrates, timeout, wait_sec, num_chunks, read_mode, serial_cfg,
                  results, lock, quiet, port_info_map),
            daemon=True,
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    return sorted(results, key=lambda r: (r.port, r.baudrate))


# ==============================
# 表示
# ==============================
def print_port_info(info: Dict, stream=sys.stderr) -> None:
    """ポート属性情報を表示する"""
    if not info:
        return
    labels = [
        ('VID:PID'     , 'vid_pid'),
        ('メーカー'     , 'manufacturer'),
        ('製品名'       , 'product'),
        ('シリアル番号'  , 'serial_number'),
        ('説明'         , 'description'),
        ('インターフェース', 'interface'),
    ]
    for label, key in labels:
        val = info.get(key)
        if val:
            print(f"    {label:16}: {val}", file=stream)


def print_results(
    results: List[PortResult],
    read_mode: ReadMode,
    encodings: Optional[List[str]] = None,
    no_attr: bool = False,
) -> None:
    """
    人間可読な形式でサマリーを表示する。
    no_attr=True のときはポート属性（VID:PID・メーカー等）を省略する。
    encodings はデコード候補リスト（省略時は DEFAULT_ENCODINGS）。
    """
    active = [r for r in results if r.chunks]
    silent = [r for r in results if not r.chunks and not r.error]
    errors = [r for r in results if r.error]

    print("\n" + "=" * 56, file=sys.stderr)
    print("  監視結果サマリー", file=sys.stderr)
    print("=" * 56, file=sys.stderr)

    for r in active:
        print(f"\n  ポート    : {r.port}  @  {r.baudrate} bps", file=sys.stderr)
        params = r.serial_params
        print(f"  シリアル  : {params['bytesize']}{params['parity']}{params['stopbits']}"
              f"  RTS/CTS={'ON' if params['rtscts'] else 'OFF'}"
              f"  XON/XOFF={'ON' if params['xonxoff'] else 'OFF'}"
              f"  DSR/DTR={'ON' if params.get('dsrdtr') else 'OFF'}", file=sys.stderr)
        if not no_attr:
            print_port_info(r.port_info, stream=sys.stderr)
        print(f"  受信チャンク数: {len(r.chunks)}", file=sys.stderr)
        print(f"  {'-'*50}", file=sys.stderr)

        per_chunk_stats = [chunk_stats(chunk['data'], read_mode, encodings) for chunk in r.chunks]
        for i, (chunk_item, stats) in enumerate(zip(r.chunks, per_chunk_stats), start=1):
            data = chunk_item['data']
            print(f"  --- チャンク {i} ---", file=sys.stderr)
            print(f"    repr         : {repr(data)}", file=sys.stderr)
            print(f"    hex          : {data.hex(' ')}", file=sys.stderr)
            print(f"    終端         : {stats['terminator']}", file=sys.stderr)
            print(f"    完全フレーム : {'Yes' if chunk_item['frame_complete'] else 'No'}"
                  f"  ({chunk_item['reason']})", file=sys.stderr)
            print(f"    受信バイト数 : {stats['raw_bytes']} bytes"
                  f"  （データ {stats['payload_bytes']} + 終端 {stats['delim_bytes']}）", file=sys.stderr)
            if stats['encoding'] == 'binary':
                print(f"    デコード     : 失敗（バイナリデータ）", file=sys.stderr)
            else:
                print(f"    {stats['encoding'].upper():9}    : {stats['decoded']}", file=sys.stderr)
                bpc_display = f"{stats['bytes_per_char']:.2f}" if stats['bytes_per_char'] is not None else "-"
                print(f"    文字数       : {stats['char_count']} 文字"
                      f"  （平均 {bpc_display} bytes/char）", file=sys.stderr)

        # 複数チャンクの統計
        if len(r.chunks) > 1:
            payloads = [s['payload_bytes'] for s in per_chunk_stats]
            chars    = [s['char_count'] for s in per_chunk_stats if s['char_count'] is not None]
            print(f"\n  --- 統計サマリー ({len(r.chunks)} チャンク) ---", file=sys.stderr)
            print(f"    データバイト数: min={min(payloads)}  max={max(payloads)}"
                  f"  avg={sum(payloads)/len(payloads):.1f}", file=sys.stderr)
            if chars:
                print(f"    文字数        : min={min(chars)}  max={max(chars)}"
                      f"  avg={sum(chars)/len(chars):.1f}", file=sys.stderr)

    if silent:
        print("\n【データなし（タイムアウト）】", file=sys.stderr)
        for r in silent:
            print(f"  {r.port} @ {r.baudrate} bps", end='', file=sys.stderr)
            if not no_attr and r.port_info.get('description'):
                print(f"  ({r.port_info['description']})", end='', file=sys.stderr)
            print(file=sys.stderr)

    if errors:
        print("\n【オープンエラー】", file=sys.stderr)
        for r in errors:
            print(f"  {r.port} @ {r.baudrate} bps : {r.error}", file=sys.stderr)

    print("=" * 56, file=sys.stderr)


# ==============================
# JSON 出力
# ==============================
def build_json(
    results: List[PortResult],
    read_mode: ReadMode,
    encodings: Optional[List[str]] = None,
    meta: Optional[Dict] = None,
) -> Dict:
    """
    結果を JSON シリアライズ可能な dict に変換する。
    meta には調査パラメータ（wait / timeout / delimiter / requested_ports / baudrates 等）を渡す。
    """
    entries = []
    for r in results:
        chunks_data = []
        for chunk_item in r.chunks:
            data = chunk_item['data']
            stats = chunk_stats(data, read_mode, encodings)
            chunks_data.append({
                'repr'          : repr(data),
                'hex'           : data.hex(' '),
                'raw_bytes'     : stats['raw_bytes'],
                'payload_bytes' : stats['payload_bytes'],
                'delim_bytes'   : stats['delim_bytes'],
                'terminator'    : stats['terminator'],
                'encoding'      : stats['encoding'],
                'decoded'       : stats['decoded'],
                'char_count'    : stats['char_count'],
                'bytes_per_char': stats['bytes_per_char'],
                'frame_complete': chunk_item['frame_complete'],
                'reason'        : chunk_item['reason'],
            })
        entries.append({
            'port'         : r.port,
            'baudrate'     : r.baudrate,
            'serial_params': r.serial_params,
            'port_info'    : r.port_info,
            'status'       : 'active' if r.chunks else ('error' if r.error else 'silent'),
            'error'        : r.error or None,
            'chunk_count'  : len(r.chunks),
            'chunks'       : chunks_data,
        })

    # metadata: 調査パラメータをまとめて調査ログとして完結させる
    metadata = {
        'timestamp'      : datetime.now().astimezone().isoformat(),
        'read_mode'      : read_mode.mode,
        'delimiter_hex'  : read_mode.delimiter.hex(' ').upper() if read_mode.delimiter else None,
        'chunk_size'     : read_mode.chunk_size if read_mode.mode == 'chunk' else None,
        'mode_selected_by_user': read_mode.selected_by_user,
        'encodings'      : encodings or DEFAULT_ENCODINGS,
    }
    if meta:
        metadata.update(meta)

    return {
        'metadata': metadata,
        'results' : entries,
    }


# ==============================
# メイン
# ==============================
def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # --- ボーレートのパース ---
    try:
        baudrates = parse_baudrates(args.baudrate)
    except ValueError:
        print("Error: --baudrate にはカンマ区切りの整数を指定してください (例: 9600,115200)", file=sys.stderr)
        sys.exit(1)

    # --- 読み取りモードの決定 ---
    try:
        read_mode = resolve_read_mode(args)
    except ValueError:
        print("Error: --delimiter は空でない 16 進数、--chunk は 1 以上の整数を指定してください", file=sys.stderr)
        sys.exit(1)

    if args.lines <= 0:
        print("Error: --lines は 1 以上の整数を指定してください", file=sys.stderr)
        sys.exit(1)

    # --- シリアル設定の組み立て ---
    serial_cfg = build_serial_config(args)

    # --- エンコーディングのパース ---
    try:
        encodings = parse_encodings(args.encodings)
    except ValueError:
        print("Error: --encodings に有効なエンコーディングを指定してください", file=sys.stderr)
        sys.exit(1)

    # --- ポート解決 ---
    # 明示指定あり（nargs='*' で 1 つ以上渡ってきた場合）
    #   → explicit=True でそのポートだけに絞り込む（他ポートの混入なし）
    # 引数なし（空リスト）
    #   → explicit=False で DEFAULT_PORT_PATTERNS + list_ports による自動検索
    port_info_map = list_port_info_map()
    known_ports = list(port_info_map.keys())

    if args.port:
        ports = find_ports(args.port, explicit=True, known_ports=known_ports)
    else:
        ports = find_ports(DEFAULT_PORT_PATTERNS, explicit=False, known_ports=known_ports)

    if not ports:
        print("Error: 利用可能なシリアルポートが見つかりませんでした。", file=sys.stderr)
        print("  接続を確認するか、ポートを引数で直接指定してください。", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        print_startup_summary(
            ports,
            baudrates,
            read_mode,
            encodings,
            no_attr=args.no_attr,
            port_info_map=port_info_map,
        )

    # --- 全ポート×全ボーレート同時監視 ---
    results = monitor_all(
        ports, baudrates, args.timeout, args.wait,
        args.lines, read_mode, serial_cfg,
        quiet=args.quiet,
        port_info_map=port_info_map,
    )

    # --- 人間可読な表示 ---
    if not args.quiet:
        print_results(results, read_mode, encodings=encodings, no_attr=args.no_attr)

    # --- JSON 出力 ---
    if args.json or args.json_file:
        # 調査パラメータを metadata として JSON に含める
        json_meta = {
            'requested_ports'  : args.port if args.port else ['(auto)'],
            'baudrates'        : baudrates,
            'wait_sec'         : args.wait,
            'timeout_sec'      : args.timeout,
            'num_chunks'       : args.lines,
            'encodings'        : encodings,
            'serial_bytesize'  : args.bytesize,
            'serial_parity'    : args.parity,
            'serial_stopbits'  : args.stopbits,
            'serial_rtscts'    : args.rtscts,
            'serial_xonxoff'   : args.xonxoff,
            'serial_dsrdtr'    : args.dsrdtr,
        }
        payload = build_json(results, read_mode, encodings=encodings, meta=json_meta)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.json_file:
            with open(args.json_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            if not args.quiet:
                print(f"\nJSON を保存しました: {args.json_file}", file=sys.stderr)

    # アクティブなポートがなければ終了コード 1
    if not any(r.chunks for r in results):
        if not args.quiet:
            print(f"\n{args.wait} 秒待機しましたが、どの組み合わせでもデータを受信できませんでした。", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
