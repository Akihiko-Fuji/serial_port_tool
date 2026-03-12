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


# ==============================
# ポート探索
# ==============================
def find_ports(patterns: Optional[List[str]] = None, explicit: bool = False) -> List[str]:
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
    known_ports = {info.device for info in serial.tools.list_ports.comports()}

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
            if pattern in known_ports:
                found.add(pattern)
            else:
                print(f"Warning: 指定ポートが見つかりません: {pattern}")

    if not explicit:
        # 自動検索モードのみ全ポートを comports() で補完
        found.update(known_ports)

    return sorted(found)


def get_port_info(port: str) -> Dict:
    """
    pyserial の list_ports からポートの属性情報を取得して返す。
    VID:PID、メーカー、製品名、シリアル番号、説明など。
    """
    for info in serial.tools.list_ports.comports():
        if info.device == port:
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
    if data.endswith(b'\r\n'):
        return "CR+LF (\\r\\n)"
    elif data.endswith(b'\r'):
        return "CR のみ (\\r)"
    elif data.endswith(b'\n'):
        return "LF のみ (\\n)"
    else:
        return "改行なし"


def terminator_bytes(data: bytes) -> bytes:
    """末尾の改行バイト列を返す（改行なしは空バイト列）"""
    for term in (b'\r\n', b'\r', b'\n'):
        if data.endswith(term):
            return term
    return b''


def decode_data(data: bytes, encodings: Optional[List[str]] = None) -> Tuple[str, str]:
    """
    (エンコーディング名, デコード文字列) を返す。失敗時は ('binary', '')。
    encodings には試みるエンコーディングをリストで渡す。
    省略時は DEFAULT_ENCODINGS を使う。
    """
    for enc in (encodings or DEFAULT_ENCODINGS):
        try:
            return enc, data.decode(enc).rstrip('\r\n')
        except (UnicodeDecodeError, LookupError):
            # LookupError: 不正なエンコーディング名が渡された場合
            pass
    return 'binary', ''


def parse_baudrates(raw: str) -> List[int]:
    """--baudrate の入力文字列を整数リストに変換する。"""
    return [int(value.strip()) for value in raw.split(',')]


def resolve_read_mode(args: argparse.Namespace) -> ReadMode:
    """引数から読み取りモードを決定する。"""
    if args.delimiter is not None:
        delimiter_hex = args.delimiter.replace(' ', '')
        if not delimiter_hex:
            raise ValueError('empty delimiter')
        delimiter = bytes.fromhex(delimiter_hex)
        return ReadMode(mode='delimiter', delimiter=delimiter)

    if args.chunk is not None:
        if args.chunk <= 0:
            raise ValueError('invalid chunk size')
        return ReadMode(mode='chunk', chunk_size=args.chunk)

    return ReadMode(mode='newline')


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


def print_startup_summary(
    ports: Sequence[str],
    baudrates: Sequence[int],
    read_mode: ReadMode,
    encodings: Sequence[str],
    no_attr: bool,
) -> None:
    """監視開始前の設定と検出ポート情報を表示する。"""
    print(f"検出されたポート  ({len(ports)} 件): {', '.join(ports)}")
    print(f"ボーレート候補    ({len(baudrates)} 件): {', '.join(str(b) for b in baudrates)}")
    print(
        f"読み取りモード   : {read_mode.mode}"
        + (f" / デリミタ: {read_mode.delimiter.hex(' ').upper()}" if read_mode.mode == 'delimiter' else '')
        + (f" / {read_mode.chunk_size} bytes" if read_mode.mode == 'chunk' else '')
    )
    print(f"エンコーディング : {', '.join(encodings)}")

    if no_attr:
        return

    print("\n--- 検出ポート属性 ---")
    for port in ports:
        info = get_port_info(port)
        print(f"  {port}")
        print_port_info(info)
        if not info:
            print("    （属性情報なし）")


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
    enc, decoded = decode_data(payload, encodings)
    char_count = len(decoded) if enc != 'binary' else None
    bpc = (len(payload) / char_count) if char_count else None

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
    chunks: List[bytes] = field(default_factory=list)
    error: str = ''
    serial_params: Dict = field(default_factory=dict)


def read_one_chunk(ser: serial.Serial, read_mode: ReadMode) -> bytes:
    """
    読み取りモードに応じて 1 チャンク分のデータを読んで返す。
      newline  : readline() ― \n を終端として読む
      delimiter: read_until() ― 任意バイト列を終端として読む
      chunk    : read(n)    ― 固定長バイト数を読む
    """
    if read_mode.mode == 'newline':
        return ser.readline()
    elif read_mode.mode == 'delimiter':
        return ser.read_until(expected=read_mode.delimiter)
    else:
        return ser.read(read_mode.chunk_size)


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

    collected: List[bytes] = []
    start = time.time()
    try:
        while time.time() - start < wait_sec:
            chunk = read_one_chunk(ser, read_mode)
            if chunk:
                collected.append(chunk)
                if not quiet:
                    with lock:
                        print(f"  [{port}@{baudrate}] チャンク {len(collected)}: {repr(chunk)}")
                if len(collected) >= num_chunks:
                    break
    finally:
        ser.close()

    result.chunks = collected


# ==============================
# 全ポート×全ボーレート同時監視
# ==============================
def monitor_all(
    ports: List[str],
    baudrates: List[int],
    timeout: float,
    wait_sec: int,
    num_chunks: int,
    read_mode: ReadMode,
    serial_cfg: Dict,
    quiet: bool = False,
) -> List[PortResult]:
    """
    全ポート × 全ボーレートの組み合わせをスレッドで同時監視する。
    例: 3 ポート × 4 ボーレート = 12 スレッド同時起動
    quiet=True のときは進捗ログを出さない。
    """
    results: List[PortResult] = []
    lock = threading.Lock()
    threads = []

    combos = [(p, b) for p in ports for b in baudrates]
    if not quiet:
        print(f"\n{len(combos)} 組合せ（{len(ports)} ポート × {len(baudrates)} ボーレート）を同時監視開始"
              f"（最大 {wait_sec} 秒）…")

    for port, baud in combos:
        r = PortResult(
            port=port,
            baudrate=baud,
            port_info=get_port_info(port),
            serial_params={
                'bytesize': serial_cfg.get('bytesize_label', 8),
                'parity'  : serial_cfg.get('parity_label',   'N'),
                'stopbits': serial_cfg.get('stopbits_label',  1),
                'rtscts'  : serial_cfg.get('rtscts',          False),
                'xonxoff' : serial_cfg.get('xonxoff',         False),
                'dsrdtr'  : serial_cfg.get('dsrdtr',          False),
            },
        )
        results.append(r)
        t = threading.Thread(
            target=monitor_port,
            args=(port, baud, timeout, wait_sec, num_chunks, read_mode, serial_cfg, r, lock, quiet),
            daemon=True,
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    return results


# ==============================
# 表示
# ==============================
def print_port_info(info: Dict) -> None:
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
            print(f"    {label:16}: {val}")


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

    print("\n" + "=" * 56)
    print("  監視結果サマリー")
    print("=" * 56)

    for r in active:
        print(f"\n  ポート    : {r.port}  @  {r.baudrate} bps")
        params = r.serial_params
        print(f"  シリアル  : {params['bytesize']}{params['parity']}{params['stopbits']}"
              f"  RTS/CTS={'ON' if params['rtscts'] else 'OFF'}"
              f"  XON/XOFF={'ON' if params['xonxoff'] else 'OFF'}"
              f"  DSR/DTR={'ON' if params.get('dsrdtr') else 'OFF'}")
        if not no_attr:
            print_port_info(r.port_info)
        print(f"  受信チャンク数: {len(r.chunks)}")
        print(f"  {'-'*50}")

        for i, data in enumerate(r.chunks, start=1):
            stats = chunk_stats(data, read_mode, encodings)
            print(f"  --- チャンク {i} ---")
            print(f"    repr         : {repr(data)}")
            print(f"    hex          : {data.hex(' ')}")
            print(f"    終端         : {stats['terminator']}")
            print(f"    受信バイト数 : {stats['raw_bytes']} bytes"
                  f"  （データ {stats['payload_bytes']} + 終端 {stats['delim_bytes']}）")
            if stats['encoding'] == 'binary':
                print(f"    デコード     : 失敗（バイナリデータ）")
            else:
                print(f"    {stats['encoding'].upper():9}    : {stats['decoded']}")
                print(f"    文字数       : {stats['char_count']} 文字"
                      f"  （平均 {stats['bytes_per_char']:.2f} bytes/char）")

        # 複数チャンクの統計
        if len(r.chunks) > 1:
            all_stats = [chunk_stats(d, read_mode, encodings) for d in r.chunks]
            payloads = [s['payload_bytes'] for s in all_stats]
            chars    = [s['char_count'] for s in all_stats if s['char_count'] is not None]
            print(f"\n  --- 統計サマリー ({len(r.chunks)} チャンク) ---")
            print(f"    データバイト数: min={min(payloads)}  max={max(payloads)}"
                  f"  avg={sum(payloads)/len(payloads):.1f}")
            if chars:
                print(f"    文字数        : min={min(chars)}  max={max(chars)}"
                      f"  avg={sum(chars)/len(chars):.1f}")

    if silent:
        print("\n【データなし（タイムアウト）】")
        for r in silent:
            print(f"  {r.port} @ {r.baudrate} bps", end='')
            if not no_attr and r.port_info.get('description'):
                print(f"  ({r.port_info['description']})", end='')
            print()

    if errors:
        print("\n【オープンエラー】")
        for r in errors:
            print(f"  {r.port} @ {r.baudrate} bps : {r.error}")

    print("=" * 56)


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
        for data in r.chunks:
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
        'timestamp'      : datetime.now().isoformat(),
        'read_mode'      : read_mode.mode,
        'delimiter_hex'  : read_mode.delimiter.hex(' ').upper() if read_mode.delimiter else None,
        'chunk_size'     : read_mode.chunk_size if read_mode.mode == 'chunk' else None,
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
    parser = argparse.ArgumentParser(
        description=(
            "シリアルポート確認ツール\n"
            "どのポートからデータが来ているか、デリミタは何か、桁数はどうかを確認する\n"
            "複数ポート・複数ボーレートを同時監視できる"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # --- ポート（複数指定可・ワイルドカード可） ---
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
        help="結果を JSON 形式で標準出力にも出す"
    )
    parser.add_argument(
        '--json-file', metavar='PATH',
        help="結果を JSON ファイルに保存する (例: --json-file result.json)"
    )
    parser.add_argument(
        '--quiet', action='store_true',
        help="進捗ログ（チャンク受信通知・起動メッセージ）を抑制する"
    )
    parser.add_argument(
        '--no-attr', action='store_true',
        help="ポート属性（VID:PID・メーカー等）の表示を省略する"
    )

    args = parser.parse_args()

    # --- ボーレートのパース ---
    try:
        baudrates = parse_baudrates(args.baudrate)
    except ValueError:
        print("Error: --baudrate にはカンマ区切りの整数を指定してください (例: 9600,115200)")
        sys.exit(1)

    # --- 読み取りモードの決定 ---
    try:
        read_mode = resolve_read_mode(args)
    except ValueError:
        print("Error: --delimiter は空でない 16 進数、--chunk は 1 以上の整数を指定してください")
        sys.exit(1)

    # --- シリアル設定の組み立て ---
    serial_cfg = build_serial_config(args)

    # --- エンコーディングのパース ---
    try:
        encodings = parse_encodings(args.encodings)
    except ValueError:
        print("Error: --encodings に有効なエンコーディングを指定してください")
        sys.exit(1)

    # --- ポート解決 ---
    # 明示指定あり（nargs='*' で 1 つ以上渡ってきた場合）
    #   → explicit=True でそのポートだけに絞り込む（他ポートの混入なし）
    # 引数なし（空リスト）
    #   → explicit=False で DEFAULT_PORT_PATTERNS + list_ports による自動検索
    if args.port:
        ports = find_ports(args.port, explicit=True)
    else:
        ports = find_ports(DEFAULT_PORT_PATTERNS, explicit=False)

    if not ports:
        print("Error: 利用可能なシリアルポートが見つかりませんでした。")
        print("  接続を確認するか、ポートを引数で直接指定してください。")
        sys.exit(1)

    if not args.quiet:
        print_startup_summary(ports, baudrates, read_mode, encodings, no_attr=args.no_attr)

    # --- 全ポート×全ボーレート同時監視 ---
    results = monitor_all(
        ports, baudrates, args.timeout, args.wait,
        args.lines, read_mode, serial_cfg,
        quiet=args.quiet,
    )

    # --- 人間可読な表示 ---
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
            print("\n--- JSON ---")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.json_file:
            with open(args.json_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"\nJSON を保存しました: {args.json_file}")

    # アクティブなポートがなければ終了コード 1
    if not any(r.chunks for r in results):
        if not args.quiet:
            print(f"\n{args.wait} 秒待機しましたが、どの組み合わせでもデータを受信できませんでした。")
        sys.exit(1)


if __name__ == '__main__':
    main()
