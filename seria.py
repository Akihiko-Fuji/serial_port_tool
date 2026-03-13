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
#   --quiet                 通常表示を抑制し、シリアルエラーのみ表示する
#                             JSON 保存や監視バッチ向け（異常時のみ stderr に出力）
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
#   # 全ポート・全ボーレートで反応するものを探す、属性省略、JSON 保存
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
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Literal, Optional, Sequence, TextIO, Tuple

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
DEFAULT_ENCODINGS: Tuple[str, ...] = ('utf-8', 'shift_jis')  # --encodings のデフォルト値
NEWLINE_TERMINATORS: Sequence[bytes] = (b'\r\n', b'\r', b'\n')

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

Language = Literal['ja', 'en']


def tr(ja: str, en: str, lang: Language) -> str:
    """現在の表示言語設定に応じてメッセージを返す。"""
    return ja if lang == 'ja' else en


def should_use_english_console() -> bool:
    """Linux ローカルのプレーンコンソール（TERM=linux）では英語表示に切り替える。"""
    forced_lang = os.environ.get('SERIA_FORCE_LANG', '').strip().lower()
    if forced_lang in {'ja', 'jp'}:
        return False
    if forced_lang in {'en', 'english'}:
        return True

    # 要件:
    # - Windows は日本語
    # - SSH 接続先ターミナルは日本語
    # - X Window 上のターミナルは日本語
    # - Linux ローカルのプレーンコンソールのみ英語
    if not sys.platform.startswith('linux'):
        return False
    has_ssh_session = bool(os.environ.get('SSH_CONNECTION') or os.environ.get('SSH_TTY'))
    if has_ssh_session:
        return False
    return os.environ.get('TERM') == 'linux'


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
    mode: Literal['newline', 'delimiter', 'chunk'] = 'newline'
    delimiter: bytes = b''
    chunk_size: int = 0
    selected_by_user: bool = False


@dataclass(frozen=True)
class SerialConfig:
    bytesize: int
    parity: str
    stopbits: float
    rtscts: bool
    xonxoff: bool
    dsrdtr: bool
    bytesize_label: int
    parity_label: str
    stopbits_label: float


@dataclass(frozen=True)
class ChunkStats:
    raw_bytes: int
    delim_bytes: int
    payload_bytes: int
    encoding: str
    char_count: Optional[int]
    bytes_per_char: Optional[float]
    terminator: str
    decoded: str


@dataclass(frozen=True)
class PortInfo:
    vid_pid: Optional[str] = None
    manufacturer: Optional[str] = None
    product: Optional[str] = None
    serial_number: Optional[str] = None
    description: Optional[str] = None
    interface: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            'vid_pid': self.vid_pid,
            'manufacturer': self.manufacturer,
            'product': self.product,
            'serial_number': self.serial_number,
            'description': self.description,
            'interface': self.interface,
        }

    def is_empty(self) -> bool:
        return not any(self.to_dict().values())

    def __bool__(self) -> bool:
        return not self.is_empty()


@dataclass(frozen=True)
class SerialParams:
    bytesize: int
    parity: str
    stopbits: float
    rtscts: bool
    xonxoff: bool
    dsrdtr: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            'bytesize': self.bytesize,
            'parity': self.parity,
            'stopbits': self.stopbits,
            'rtscts': self.rtscts,
            'xonxoff': self.xonxoff,
            'dsrdtr': self.dsrdtr,
        }


@dataclass(frozen=True)
class AppConfig:
    requested_ports: List[str]
    baudrates: List[int]
    read_mode: ReadMode
    lines: int
    wait_sec: int
    timeout_sec: float
    serial_config: SerialConfig
    encodings: List[str]
    json_stdout: bool
    json_file: Optional[str]
    quiet: bool
    no_attr: bool
    lang: Language


@dataclass(frozen=True)
class ConfigError(Exception):
    """CLI 設定解決中に発生した利用者向けエラー。"""
    ja: str
    en: str


@dataclass
class LineBufferedReader:
    """改行終端モード用の内部バッファ。"""
    pending: bytearray = field(default_factory=bytearray)

    def read_line(self, ser: serial.Serial, deadline: Optional[float] = None) -> bytes:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                if self.pending:
                    return self._consume(len(self.pending))
                return b''

            trailing_cr = False
            for idx, value in enumerate(self.pending):
                if value == 0x0A:  # LF
                    return self._consume(idx + 1)
                if value == 0x0D:  # CR
                    if idx + 1 < len(self.pending) and self.pending[idx + 1] == 0x0A:
                        return self._consume(idx + 2)
                    if idx + 1 < len(self.pending):
                        return self._consume(idx + 1)
                    # CR が末尾で止まっている場合は次バイト到着まで待機し、
                    # 分割到着した CRLF を CR/LF に分離しないようにする。
                    trailing_cr = True
                    break

            in_waiting = getattr(ser, 'in_waiting', 0)
            read_size = in_waiting if in_waiting > 0 else 1
            chunk = ser.read(read_size)
            if not chunk:
                if self.pending:
                    if trailing_cr and deadline is not None and time.monotonic() < deadline:
                        continue
                    # deadline=None のときは待機を続けず、末尾 CR を含む pending を返す。
                    return self._consume(len(self.pending))
                return b''
            self.pending.extend(chunk)

    def _consume(self, size: int) -> bytes:
        data = bytes(self.pending[:size])
        del self.pending[:size]
        return data


@dataclass(frozen=True)
class PortDiscoveryResult:
    ports: List[str]
    warnings: List[str] = field(default_factory=list)


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
    lang: Language = 'ja',
) -> PortDiscoveryResult:
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
    warnings: List[str] = []
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
                warnings.append(tr(f"Warning: 指定ポートが見つかりません: {pattern}", f"Warning: specified port not found: {pattern}", lang=lang))

    if not explicit:
        # 自動検索モードのみ全ポートを comports() で補完
        found.update(known_port_set)

    return PortDiscoveryResult(ports=sorted(found), warnings=warnings)


def get_port_info(port: str, port_info_map: Optional[Dict[str, serial.tools.list_ports_common.ListPortInfo]] = None) -> PortInfo:
    """
    pyserial の list_ports からポートの属性情報を取得して返す。
    VID:PID、メーカー、製品名、シリアル番号、説明など。
    """
    info_map = port_info_map or list_port_info_map()
    info = info_map.get(port)
    if info:
        vid = info.vid
        pid = info.pid
        return PortInfo(
            vid_pid=f"{vid:04X}:{pid:04X}" if vid is not None and pid is not None else None,
            manufacturer=info.manufacturer,
            product=info.product,
            serial_number=info.serial_number,
            description=info.description,
            interface=info.interface,
        )
    return PortInfo()


# ==============================
# データ解析ユーティリティ
# ==============================
def check_terminator(data: bytes, lang: Language = 'ja') -> str:
    """末尾の改行種別を人間が読める文字列で返す"""
    term = terminator_bytes(data)
    if term == b'\r\n':
        return tr("CR+LF (\\r\\n)", "CR+LF (\\r\\n)", lang=lang)
    if term == b'\r':
        return tr("CR のみ (\\r)", "CR only (\\r)", lang=lang)
    if term == b'\n':
        return tr("LF のみ (\\n)", "LF only (\\n)", lang=lang)
    return tr("改行なし", "No newline", lang=lang)


def terminator_bytes(data: bytes) -> bytes:
    """末尾の改行バイト列を返す（改行なしは空バイト列）"""
    for terminator in NEWLINE_TERMINATORS:
        if data.endswith(terminator):
            return terminator
    return b''


def decode_data(
    data: bytes,
    encodings: Sequence[str] = DEFAULT_ENCODINGS,
    strip_newline: bool = True,
) -> Tuple[str, str]:
    """
    (エンコーディング名, デコード文字列) を返す。失敗時は ('binary', '')。
    encodings には試みるエンコーディングをシーケンスで渡す。
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
    # 可視文字 95% 以上かつ制御文字 5% 以下をテキストとみなす経験則。
    return printable_ratio >= 0.95 and control_ratio <= 0.05

def parse_baudrates(raw: str) -> List[int]:
    """--baudrate の入力文字列を整数リストに変換する。"""
    segments = [value.strip() for value in raw.split(',')]
    if not segments or any(not value for value in segments):
        raise ValueError('invalid baudrate')

    try:
        baudrates = [int(value) for value in segments]
    except ValueError as exc:
        raise ValueError('invalid baudrate') from exc

    if any(rate <= 0 for rate in baudrates):
        raise ValueError('invalid baudrate')
    return baudrates


def resolve_read_mode(delimiter_arg: Optional[str], chunk_arg: Optional[int], newline_arg: bool) -> ReadMode:
    """引数から読み取りモードを決定する。"""
    if delimiter_arg is not None:
        delimiter_hex = delimiter_arg.replace(' ', '')
        if not delimiter_hex:
            raise ValueError('empty delimiter')

        try:
            delimiter = bytes.fromhex(delimiter_hex)
        except ValueError as exc:
            raise ValueError('invalid delimiter') from exc

        if not delimiter:
            raise ValueError('empty delimiter')

        return ReadMode(mode='delimiter', delimiter=delimiter, selected_by_user=True)

    if chunk_arg is not None:
        if chunk_arg <= 0:
            raise ValueError('invalid chunk size')
        return ReadMode(mode='chunk', chunk_size=chunk_arg, selected_by_user=True)
    return ReadMode(mode='newline', selected_by_user=newline_arg)


def parse_encodings(raw: Optional[str]) -> List[str]:
    """--encodings の入力文字列を正規化して返す。"""
    if raw is None:
        return list(DEFAULT_ENCODINGS)

    encodings = [encoding.strip() for encoding in raw.split(',') if encoding.strip()]
    if not encodings:
        raise ValueError('empty encodings')

    for encoding in encodings:
        try:
            codecs.lookup(encoding)
        except LookupError as exc:
            raise ValueError(f'invalid encoding: {encoding}') from exc

    return encodings


def build_serial_config(bytesize: int, parity: str, stopbits: float, rtscts: bool, xonxoff: bool, dsrdtr: bool) -> SerialConfig:
    """pyserial のオープン設定と表示用ラベルを構築する。"""
    return SerialConfig(
        bytesize=BYTESIZE_MAP[bytesize],
        parity=PARITY_MAP[parity],
        stopbits=STOPBITS_MAP[stopbits],
        rtscts=rtscts,
        xonxoff=xonxoff,
        dsrdtr=dsrdtr,
        bytesize_label=bytesize,
        parity_label=parity,
        stopbits_label=stopbits,
    )


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
        help="通常表示を抑制し、シリアルエラーのみ標準エラーに出力する"
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
    lang: Language = 'ja',
) -> None:
    """監視開始前の設定と検出ポート情報を表示する。"""
    print(tr(f"検出されたポート  ({len(ports)} 件): {', '.join(ports)}", f"Detected ports ({len(ports)}): {', '.join(ports)}", lang=lang), file=sys.stderr)
    print(tr(f"ボーレート候補    ({len(baudrates)} 件): {', '.join(str(b) for b in baudrates)}", f"Baudrate candidates ({len(baudrates)}): {', '.join(str(b) for b in baudrates)}", lang=lang), file=sys.stderr)
    print(
        tr(
            f"読み取りモード   : {read_mode.mode}"
            + (f" / デリミタ: {read_mode.delimiter.hex(' ').upper()}" if read_mode.mode == 'delimiter' else '')
            + (f" / {read_mode.chunk_size} bytes" if read_mode.mode == 'chunk' else ''),
            f"Read mode: {read_mode.mode}"
            + (f" / delimiter: {read_mode.delimiter.hex(' ').upper()}" if read_mode.mode == 'delimiter' else '')
            + (f" / {read_mode.chunk_size} bytes" if read_mode.mode == 'chunk' else '')
        , lang=lang),
        file=sys.stderr
    )
    print(tr(f"エンコーディング : {', '.join(encodings)}", f"Encodings: {', '.join(encodings)}", lang=lang), file=sys.stderr)

    if no_attr:
        return

    print(tr("\n--- 検出ポート属性 ---", "\n--- Detected port attributes ---", lang=lang), file=sys.stderr)
    for port in ports:
        info = get_port_info(port, port_info_map=port_info_map)
        print(f"  {port}", file=sys.stderr)
        print_port_info(info, stream=sys.stderr, lang=lang)
        if not info:
            print(tr("    （属性情報なし）", "    (No attribute info)", lang=lang), file=sys.stderr)


def chunk_stats(
    data: bytes,
    read_mode: ReadMode,
    encodings: Sequence[str] = DEFAULT_ENCODINGS,
    lang: Language = 'ja',
) -> ChunkStats:
    """
    1 チャンク分のバイト数・文字数統計を返す。
      raw_bytes     : 受信バイト数（デリミタ込み）
      delim_bytes   : 末尾デリミタのバイト数（固定長モードは 0）
      payload_bytes : データ本体のバイト数（デリミタ除く）
      encoding      : デコードに使ったエンコーディング
      char_count    : デコード後の文字数（バイナリは None）
      bytes_per_char: 平均バイト/文字（バイナリは None）
      terminator    : 末尾の改行種別文字列（newline モード以外は種別説明）
    encodings には試みるエンコーディングをシーケンスで渡す。
    """
    if read_mode.mode == 'newline':
        term_b = terminator_bytes(data)
        delim_len = len(term_b)
        terminator_label = check_terminator(data, lang=lang)
    elif read_mode.mode == 'delimiter':
        delim_len = len(read_mode.delimiter) if data.endswith(read_mode.delimiter) else 0
        delim_hex = read_mode.delimiter.hex(' ').upper()
        terminator_label = tr(f"デリミタ ({delim_hex})", f"Delimiter ({delim_hex})", lang=lang)
    else:  # chunk
        delim_len = 0
        terminator_label = tr(f"固定長 {read_mode.chunk_size} bytes", f"fixed length {read_mode.chunk_size} bytes", lang=lang)

    payload = data[: len(data) - delim_len]
    # デコードは終端を除いた payload に対して行う。
    # data 全体に対してデコードすると、終端バイトが CR/LF 以外の任意バイト列の場合に
    # decoded 文字列に終端が混入したり、デコード失敗する可能性がある。
    enc, decoded = decode_data(payload, encodings, strip_newline=False)
    char_count = len(decoded) if enc != 'binary' else None
    bpc = (len(payload) / char_count) if char_count is not None and char_count > 0 else None

    return ChunkStats(
        raw_bytes=len(data),
        delim_bytes=delim_len,
        payload_bytes=len(payload),
        encoding=enc,
        char_count=char_count,
        bytes_per_char=round(bpc, 3) if bpc is not None else None,
        terminator=terminator_label,
        decoded=decoded,
    )


# ==============================
# 単一ポート監視（スレッド用）
# ==============================
class FrameReason(str, Enum):
    NEWLINE_FOUND = 'newline_terminator_found'
    DELIMITER_FOUND = 'delimiter_found'
    FIXED_SIZE_COMPLETE = 'fixed_size_complete'
    TIMEOUT_PARTIAL = 'timeout_partial'


@dataclass(frozen=True)
class ChunkRecord:
    data: bytes
    frame_complete: bool
    reason: FrameReason


@dataclass
class PortResult:
    port: str
    baudrate: int
    serial_params: SerialParams
    port_info: PortInfo = field(default_factory=PortInfo)
    chunks: List[ChunkRecord] = field(default_factory=list)
    chunk_stats_list: List[ChunkStats] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def has_data(self) -> bool:
        return bool(self.chunks)

    @property
    def has_error(self) -> bool:
        return bool(self.error)

    def attach_stats(self, read_mode: ReadMode, encodings: Sequence[str], lang: Language) -> None:
        self.chunk_stats_list = [
            chunk_stats(chunk.data, read_mode, encodings, lang=lang)
            for chunk in self.chunks
        ]

    def to_json_dict(self) -> Dict[str, object]:
        chunks_data = []
        for chunk_index, chunk in enumerate(self.chunks):
            stats = self.chunk_stats_list[chunk_index]
            data = chunk.data
            chunks_data.append({
                'repr': repr(data),
                'hex': data.hex(' '),
                'raw_bytes': stats.raw_bytes,
                'payload_bytes': stats.payload_bytes,
                'delim_bytes': stats.delim_bytes,
                'terminator': stats.terminator,
                'encoding': stats.encoding,
                'decoded': stats.decoded,
                'char_count': stats.char_count,
                'bytes_per_char': stats.bytes_per_char,
                'frame_complete': chunk.frame_complete,
                'reason': chunk.reason.value,
            })

        return {
            'port': self.port,
            'baudrate': self.baudrate,
            'serial_params': self.serial_params.to_dict(),
            'port_info': self.port_info.to_dict(),
            'has_data': self.has_data,
            'has_error': self.has_error,
            'error': self.error,
            'chunk_count': len(self.chunks),
            'chunks': chunks_data,
        }


def read_one_chunk(
    ser: serial.Serial,
    read_mode: ReadMode,
    line_reader: LineBufferedReader,
    deadline: Optional[float] = None,
) -> bytes:
    """
    読み取りモードに応じて 1 チャンク分のデータを読んで返す。
      newline  : \r\n / \r / \n を終端として読む（自前実装）
      delimiter: read_until() ― 任意バイト列を終端として読む
      chunk    : read(n)    ― 固定長バイト数を読む
    """
    if read_mode.mode == 'newline':
        return line_reader.read_line(ser, deadline=deadline)
    elif read_mode.mode == 'delimiter':
        # read_until() は serial timeout 単位で復帰し、monitor_port() の
        # ループ先頭 deadline 判定で停止する（最大 timeout 秒の超過）。
        return ser.read_until(expected=read_mode.delimiter)
    else:
        # 固定長 read() も同様に timeout 単位で復帰する設計。
        return ser.read(read_mode.chunk_size)


def classify_chunk(data: bytes, read_mode: ReadMode) -> Tuple[bool, FrameReason]:
    """受信チャンクが終端まで到達した完全フレームかどうかを返す。"""
    if read_mode.mode == 'newline':
        complete = any(data.endswith(t) for t in NEWLINE_TERMINATORS)
        return complete, (FrameReason.NEWLINE_FOUND if complete else FrameReason.TIMEOUT_PARTIAL)

    if read_mode.mode == 'delimiter':
        complete = data.endswith(read_mode.delimiter)
        return complete, (FrameReason.DELIMITER_FOUND if complete else FrameReason.TIMEOUT_PARTIAL)

    complete = len(data) == read_mode.chunk_size
    return complete, (FrameReason.FIXED_SIZE_COMPLETE if complete else FrameReason.TIMEOUT_PARTIAL)


def monitor_port(
    port: str,
    baudrate: int,
    timeout: float,
    wait_sec: int,
    num_chunks: int,
    read_mode: ReadMode,
    serial_cfg: SerialConfig,
    result: PortResult,
    log_lock: threading.Lock,
    quiet: bool = False,
    lang: Language = 'ja',
) -> None:
    """スレッド内で 1 ポートを監視し、結果を result に格納する"""
    try:
        ser = serial.Serial(
            port,
            baudrate,
            timeout         = timeout,
            bytesize        = serial_cfg.bytesize,
            parity          = serial_cfg.parity,
            stopbits        = serial_cfg.stopbits,
            rtscts          = serial_cfg.rtscts,
            xonxoff         = serial_cfg.xonxoff,
            dsrdtr          = serial_cfg.dsrdtr,
        )
    except serial.SerialException as e:
        result.error = str(e)
        return

    collected: List[ChunkRecord] = []
    line_reader = LineBufferedReader()
    deadline = time.monotonic() + wait_sec
    try:
        while time.monotonic() < deadline:
            try:
                chunk = read_one_chunk(ser, read_mode, line_reader=line_reader, deadline=deadline)
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
                collected.append(ChunkRecord(data=chunk, frame_complete=frame_complete, reason=reason))
                if not quiet:
                    with log_lock:
                        print(tr(f"  [{port}@{baudrate}] チャンク {len(collected)}: {repr(chunk)}", f"  [{port}@{baudrate}] chunk {len(collected)}: {repr(chunk)}", lang=lang), file=sys.stderr)
                if len(collected) >= num_chunks:
                    break
    finally:
        try:
            ser.close()
        except (ReferenceError, serial.SerialException, OSError) as e:
            # クローズ時に weakref 由来エラーが発生しても、取得済みデータは保持する。
            if not result.error:
                result.error = f"{type(e).__name__} while closing serial port: {e}"

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
    serial_cfg: SerialConfig,
    log_lock: threading.Lock,
    quiet: bool = False,
    port_info_map: Optional[Dict[str, serial.tools.list_ports_common.ListPortInfo]] = None,
    lang: Language = 'ja',
) -> List[PortResult]:
    """1ポートを担当し、指定されたボーレート候補を順に試す。

    いずれかのボーレートで受信できても探索は止めず、候補を全件試し切る。
    """
    per_port_results: List[PortResult] = []
    for baud in baudrates:
        r = PortResult(
            port=port,
            baudrate=baud,
            port_info=get_port_info(port, port_info_map=port_info_map),
            serial_params=SerialParams(
                bytesize=serial_cfg.bytesize_label,
                parity=serial_cfg.parity_label,
                stopbits=serial_cfg.stopbits_label,
                rtscts=serial_cfg.rtscts,
                xonxoff=serial_cfg.xonxoff,
                dsrdtr=serial_cfg.dsrdtr,
            ),
        )
        monitor_port(
            port, baud, timeout, wait_sec, num_chunks, read_mode,
            serial_cfg, r, log_lock, quiet, lang=lang
        )
        per_port_results.append(r)
    return per_port_results


def finalize_result(result: PortResult, read_mode: ReadMode, encodings: Sequence[str], lang: Language) -> PortResult:
    """PortResult にチャンク統計を付与し、表示/JSON 出力可能な完成状態にする。"""
    result.attach_stats(read_mode, encodings, lang=lang)
    return result


def monitor_all(
    ports: List[str],
    baudrates: List[int],
    timeout: float,
    wait_sec: int,
    num_chunks: int,
    read_mode: ReadMode,
    serial_cfg: SerialConfig,
    encodings: Sequence[str],
    quiet: bool = False,
    port_info_map: Optional[Dict[str, serial.tools.list_ports_common.ListPortInfo]] = None,
    lang: Language = 'ja',
) -> List[PortResult]:
    """
    ポートごとにスレッドを 1 本ずつ起動し、
    同一ポート内のボーレート探索は直列で実行する。
    quiet=True のときは進捗ログを出さない。
    """
    results: List[PortResult] = []

    combos = [(p, b) for p in ports for b in baudrates]
    if not quiet:
        print(tr(
            f"\n{len(combos)} 組合せ（{len(ports)} ポート × {len(baudrates)} ボーレート）を監視開始（ポート間は並列、同一ポート内は直列 / 最大 {wait_sec} 秒）…",
            f"\nStarting monitoring for {len(combos)} combinations ({len(ports)} ports × {len(baudrates)} baudrates) (parallel across ports, sequential per port / max {wait_sec} sec)..."
        , lang=lang), file=sys.stderr)

    log_lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max(len(ports), 1)) as executor:
        futures = [
            executor.submit(
                monitor_port_all_baudrates,
                port, baudrates, timeout, wait_sec, num_chunks, read_mode,
                serial_cfg, log_lock, quiet, port_info_map, lang
            )
            for port in ports
        ]
        for future in futures:
            results.extend(future.result())

    finalized = [finalize_result(r, read_mode, encodings, lang=lang) for r in results]
    return sorted(finalized, key=lambda r: (r.port, r.baudrate))


# ==============================
# 表示
# ==============================
def print_port_info(info: PortInfo, stream: Optional[TextIO] = None, lang: Language = 'ja') -> None:
    """ポート属性情報を表示する"""
    stream = stream or sys.stderr
    labels = [
        ('VID:PID', 'VID:PID', 'vid_pid'),
        ('メーカー', 'Manufacturer', 'manufacturer'),
        ('製品名', 'Product', 'product'),
        ('シリアル番号', 'Serial number', 'serial_number'),
        ('説明', 'Description', 'description'),
        ('インターフェース', 'Interface', 'interface'),
    ]
    for label_ja, label_en, key in labels:
        val = getattr(info, key)
        if val:
            print(f"    {tr(label_ja, label_en, lang=lang):16}: {val}", file=stream)


def msg_serial_errors(lang: Language) -> str:
    return tr("\n【シリアルエラー（オープン/読み取り/クローズ）】", "\n[Serial errors (open/read/close)]", lang=lang)


def frame_reason_label(reason: FrameReason, lang: Language) -> str:
    labels = {
        FrameReason.NEWLINE_FOUND: tr("改行終端を検出", "newline terminator found", lang=lang),
        FrameReason.DELIMITER_FOUND: tr("デリミタを検出", "delimiter found", lang=lang),
        FrameReason.FIXED_SIZE_COMPLETE: tr("固定長を満たした", "fixed size complete", lang=lang),
        FrameReason.TIMEOUT_PARTIAL: tr("タイムアウトで部分受信", "timeout partial", lang=lang),
    }
    return labels[reason]


def print_results(
    results: List[PortResult],
    no_attr: bool = False,
    quiet: bool = False,
    lang: Language = 'ja',
) -> None:
    """
    人間可読な形式でサマリーを表示する。
    no_attr=True のときはポート属性（VID:PID・メーカー等）を省略する。
    quiet=True のときは通常結果を抑制し、シリアルエラーのみを表示する。
    """
    active = [r for r in results if r.has_data]
    silent = [r for r in results if not r.has_data and not r.has_error]
    errors = [r for r in results if r.has_error]

    if quiet:
        if errors:
            print(msg_serial_errors(lang), file=sys.stderr)
            for r in errors:
                print(f"  {r.port} @ {r.baudrate} bps : {r.error}", file=sys.stderr)
        return

    print("\n" + "=" * 56, file=sys.stderr)
    print(tr("  監視結果サマリー", "  Monitoring summary", lang=lang), file=sys.stderr)
    print("=" * 56, file=sys.stderr)

    for r in active:
        print(tr(f"\n  ポート    : {r.port}  @  {r.baudrate} bps", f"\n  Port      : {r.port}  @  {r.baudrate} bps", lang=lang), file=sys.stderr)
        params = r.serial_params
        serial_ja = (
            f"  シリアル  : {params.bytesize}{params.parity}{params.stopbits}"
            f"  RTS/CTS={'ON' if params.rtscts else 'OFF'}"
            f"  XON/XOFF={'ON' if params.xonxoff else 'OFF'}"
            f"  DSR/DTR={'ON' if params.dsrdtr else 'OFF'}"
        )
        serial_en = (
            f"  Serial    : {params.bytesize}{params.parity}{params.stopbits}"
            f"  RTS/CTS={'ON' if params.rtscts else 'OFF'}"
            f"  XON/XOFF={'ON' if params.xonxoff else 'OFF'}"
            f"  DSR/DTR={'ON' if params.dsrdtr else 'OFF'}"
        )
        print(tr(serial_ja, serial_en, lang=lang), file=sys.stderr)
        if not no_attr:
            print_port_info(r.port_info, stream=sys.stderr, lang=lang)
        print(tr(f"  受信チャンク数: {len(r.chunks)}", f"  Received chunks: {len(r.chunks)}", lang=lang), file=sys.stderr)
        print(f"  {'-'*50}", file=sys.stderr)

        for i, chunk_item in enumerate(r.chunks, start=1):
            stats = r.chunk_stats_list[i - 1]
            data = chunk_item.data
            print(tr(f"  --- チャンク {i} ---", f"  --- Chunk {i} ---", lang=lang), file=sys.stderr)
            print(f"    repr         : {repr(data)}", file=sys.stderr)
            print(f"    hex          : {data.hex(' ')}", file=sys.stderr)
            print(tr(f"    終端         : {stats.terminator}", f"    Terminator   : {stats.terminator}", lang=lang), file=sys.stderr)
            frame_complete_label = tr('はい' if chunk_item.frame_complete else 'いいえ', 'Yes' if chunk_item.frame_complete else 'No', lang=lang)
            reason_label = frame_reason_label(chunk_item.reason, lang=lang)
            print(tr(
                f"    完全フレーム : {frame_complete_label}  ({reason_label})",
                f"    Full frame   : {frame_complete_label}  ({reason_label})",
                lang=lang
            ), file=sys.stderr)
            print(tr(
                f"    受信バイト数 : {stats.raw_bytes} bytes  （データ {stats.payload_bytes} + 終端 {stats.delim_bytes}）",
                f"    Received     : {stats.raw_bytes} bytes  (payload {stats.payload_bytes} + terminator {stats.delim_bytes})",
                lang=lang
            ), file=sys.stderr)
            if stats.encoding == 'binary':
                print(tr("    デコード     : 失敗（バイナリデータ）", "    Decode       : failed (binary data)", lang=lang), file=sys.stderr)
            else:
                print(f"    {stats.encoding.upper():9}    : {stats.decoded}", file=sys.stderr)
                bpc_display = f"{stats.bytes_per_char:.2f}" if stats.bytes_per_char is not None else "-"
                print(tr(
                    f"    文字数       : {stats.char_count} 文字  （平均 {bpc_display} bytes/char）",
                    f"    Characters   : {stats.char_count}  (avg {bpc_display} bytes/char)",
                    lang=lang
                ), file=sys.stderr)

        # 複数チャンクの統計
        if len(r.chunks) > 1:
            chunk_stats_list = r.chunk_stats_list
            payloads = [s.payload_bytes for s in chunk_stats_list]
            chars    = [s.char_count for s in chunk_stats_list if s.char_count is not None]
            print(tr(f"\n  --- 統計サマリー ({len(r.chunks)} チャンク) ---", f"\n  --- Statistics ({len(r.chunks)} chunks) ---", lang=lang), file=sys.stderr)
            print(tr(
                f"    データバイト数: min={min(payloads)}  max={max(payloads)}  avg={sum(payloads)/len(payloads):.1f}",
                f"    Payload bytes: min={min(payloads)}  max={max(payloads)}  avg={sum(payloads)/len(payloads):.1f}",
                lang=lang
            ), file=sys.stderr)
            if chars:
                print(tr(
                    f"    文字数        : min={min(chars)}  max={max(chars)}  avg={sum(chars)/len(chars):.1f}",
                    f"    Characters   : min={min(chars)}  max={max(chars)}  avg={sum(chars)/len(chars):.1f}",
                    lang=lang
                ), file=sys.stderr)

    if silent:
        print(tr("\n【データなし（タイムアウト）】", "\n[No data (timeout)]", lang=lang), file=sys.stderr)
        for r in silent:
            print(f"  {r.port} @ {r.baudrate} bps", end='', file=sys.stderr)
            if not no_attr and r.port_info.description:
                print(f"  ({r.port_info.description})", end='', file=sys.stderr)
            print(file=sys.stderr)

    if errors:
        print(msg_serial_errors(lang), file=sys.stderr)
        for r in errors:
            print(f"  {r.port} @ {r.baudrate} bps : {r.error}", file=sys.stderr)

    print("=" * 56, file=sys.stderr)


# ==============================
# JSON 出力
# ==============================
def build_json(
    results: List[PortResult],
    read_mode: ReadMode,
    encodings: Sequence[str],
    meta: Optional[Dict] = None,
) -> Dict:
    """
    結果を JSON シリアライズ可能な dict に変換する。
    meta には調査パラメータ（wait / timeout / delimiter / requested_ports / baudrates 等）を渡す。
    """
    entries = [r.to_json_dict() for r in results]

    # metadata: 調査パラメータをまとめて調査ログとして完結させる
    metadata = {
        'timestamp'      : datetime.now().astimezone().isoformat(),
        'read_mode'      : read_mode.mode,
        'delimiter_hex'  : read_mode.delimiter.hex(' ').upper() if read_mode.delimiter else None,
        'chunk_size'     : read_mode.chunk_size if read_mode.mode == 'chunk' else None,
        'mode_selected_by_user': read_mode.selected_by_user,
        'encodings'      : list(encodings),
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
def build_app_config(args: argparse.Namespace, lang: Language) -> AppConfig:
    """CLI 引数を内部設定オブジェクトに変換する。"""
    try:
        baudrates = parse_baudrates(args.baudrate)
    except ValueError as exc:
        raise ConfigError(
            ja="Error: --baudrate にはカンマ区切りの正の整数を指定してください (例: 9600,115200)",
            en="Error: --baudrate must be comma-separated positive integers (e.g. 9600,115200)",
        ) from exc

    try:
        read_mode = resolve_read_mode(args.delimiter, args.chunk, args.newline)
    except ValueError as exc:
        raise ConfigError(
            ja="Error: --delimiter は非空の16進数、--chunk は1以上の整数を指定してください",
            en="Error: --delimiter must be non-empty hexadecimal and --chunk must be an integer >= 1",
        ) from exc

    if args.lines <= 0:
        raise ConfigError(
            ja="Error: --lines は 1 以上の整数を指定してください",
            en="Error: --lines must be an integer >= 1",
        )
    if args.wait < 0:
        raise ConfigError(
            ja="Error: --wait は 0 以上の整数を指定してください",
            en="Error: --wait must be an integer >= 0",
        )
    if args.timeout < 0:
        raise ConfigError(
            ja="Error: --timeout は 0 以上の数値を指定してください",
            en="Error: --timeout must be a number >= 0",
        )

    serial_cfg = build_serial_config(
        args.bytesize, args.parity, args.stopbits,
        args.rtscts, args.xonxoff, args.dsrdtr,
    )
    try:
        encodings = parse_encodings(args.encodings)
    except ValueError as exc:
        raise ConfigError(
            ja="Error: --encodings に有効なエンコーディングを指定してください",
            en="Error: --encodings must contain valid encodings",
        ) from exc

    return AppConfig(
        requested_ports=list(args.port),
        baudrates=baudrates,
        read_mode=read_mode,
        lines=args.lines,
        wait_sec=args.wait,
        timeout_sec=args.timeout,
        serial_config=serial_cfg,
        encodings=encodings,
        json_stdout=args.json,
        json_file=args.json_file,
        quiet=args.quiet,
        no_attr=args.no_attr,
        lang=lang,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    lang: Language = 'en' if should_use_english_console() else 'ja'

    # --- CLI -> AppConfig 変換 ---
    try:
        app_cfg = build_app_config(args, lang)
    except ConfigError as exc:
        print(tr(exc.ja, exc.en, lang=lang), file=sys.stderr)
        sys.exit(1)

    # --- ポート解決 ---
    # 明示指定あり（nargs='*' で 1 つ以上渡ってきた場合）
    #   → explicit=True でそのポートだけに絞り込む（他ポートの混入なし）
    # 引数なし（空リスト）
    #   → explicit=False で DEFAULT_PORT_PATTERNS + list_ports による自動検索
    port_info_map = list_port_info_map()
    known_ports = list(port_info_map.keys())

    if app_cfg.requested_ports:
        discovery = find_ports(app_cfg.requested_ports, explicit=True, known_ports=known_ports, lang=app_cfg.lang)
    else:
        discovery = find_ports(DEFAULT_PORT_PATTERNS, explicit=False, known_ports=known_ports, lang=app_cfg.lang)
    ports = discovery.ports

    for warning in discovery.warnings:
        print(warning, file=sys.stderr)

    if not ports:
        print(tr("Error: 利用可能なシリアルポートが見つかりませんでした。", "Error: no available serial ports were found.", lang=app_cfg.lang), file=sys.stderr)
        print(tr("  接続を確認するか、ポートを引数で直接指定してください。", "  Check the connection or specify ports explicitly as arguments.", lang=app_cfg.lang), file=sys.stderr)
        sys.exit(1)

    if not app_cfg.quiet:
        print_startup_summary(
            ports,
            app_cfg.baudrates,
            app_cfg.read_mode,
            app_cfg.encodings,
            no_attr=app_cfg.no_attr,
            port_info_map=port_info_map,
            lang=app_cfg.lang,
        )

    # --- 全ポート×全ボーレート同時監視 ---
    results = monitor_all(
        ports, app_cfg.baudrates, app_cfg.timeout_sec, app_cfg.wait_sec,
        app_cfg.lines, app_cfg.read_mode, app_cfg.serial_config, app_cfg.encodings,
        quiet=app_cfg.quiet,
        port_info_map=port_info_map,
        lang=app_cfg.lang,
    )

    # --- 人間可読な表示 ---
    print_results(
        results,
        no_attr=app_cfg.no_attr,
        quiet=app_cfg.quiet,
        lang=app_cfg.lang,
    )

    # --- JSON 出力 ---
    if app_cfg.json_stdout or app_cfg.json_file:
        # 調査パラメータを metadata として JSON に含める
        json_meta = {
            'requested_ports'  : app_cfg.requested_ports if app_cfg.requested_ports else ['(auto)'],
            'baudrates'        : app_cfg.baudrates,
            'wait_sec'         : app_cfg.wait_sec,
            'timeout_sec'      : app_cfg.timeout_sec,
            'num_chunks'       : app_cfg.lines,
            'serial_bytesize'  : app_cfg.serial_config.bytesize_label,
            'serial_parity'    : app_cfg.serial_config.parity_label,
            'serial_stopbits'  : app_cfg.serial_config.stopbits_label,
            'serial_rtscts'    : app_cfg.serial_config.rtscts,
            'serial_xonxoff'   : app_cfg.serial_config.xonxoff,
            'serial_dsrdtr'    : app_cfg.serial_config.dsrdtr,
        }
        payload = build_json(results, app_cfg.read_mode, encodings=app_cfg.encodings, meta=json_meta)
        if app_cfg.json_stdout:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        if app_cfg.json_file:
            with open(app_cfg.json_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            if not app_cfg.quiet:
                print(tr(f"\nJSON を保存しました: {app_cfg.json_file}", f"\nSaved JSON: {app_cfg.json_file}", lang=app_cfg.lang), file=sys.stderr)

    # アクティブなポートがなければ終了コード 1
    if not any(r.has_data for r in results):
        if not app_cfg.quiet:
            print(tr(f"\n{app_cfg.wait_sec} 秒待機しましたが、どの組み合わせでもデータを受信できませんでした。", f"\nWaited {app_cfg.wait_sec} seconds, but no data was received for any combination.", lang=app_cfg.lang), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
