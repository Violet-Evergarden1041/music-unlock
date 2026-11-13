"""QQ 音乐解密器（QMC 系列）。

文件尾部藏着密钥，套路有三种（详见 QmcDecoder._search_key）：
* 尾部 4 字节是 ``STag``     → 密钥没写进文件，必须靠客户端（Frida 后端）才能解；
* 尾部 4 字节是 ``QTag``     → 大端 keySize + 密钥 + ",songID"；
* 否则读小端 keySize         → 小于 0x400 走 v2，否则整个文件走 v1 固定表。
"""

from __future__ import annotations

from pathlib import Path

from . import qmc_algo
from .base import Decoded, DecryptError, NeedsClientError, register

#: 扩展名 → 解密出来的默认容器
EXT_MAP: dict[str, str] = {
    # QMCv2（PC 端 VIP）
    "mflac": "flac",
    "mflac0": "flac",
    "mgg": "ogg",
    "mgg0": "ogg",
    "mgg1": "ogg",
    "mggl": "ogg",
    "mmp4": "m4a",
    # 旧后缀，可能混用 v1/v2
    "qmcflac": "flac",
    "qmcogg": "ogg",
    "qmc0": "mp3",
    "qmc2": "ogg",
    "qmc3": "mp3",
    "qmc4": "ogg",
    "qmc6": "ogg",
    "qmc8": "ogg",
    # Moo 音乐 / Tm 等同一套算法
    "bkcmp3": "mp3",
    "bkcflac": "flac",
    "bkcogg": "ogg",
    "bkcwav": "wav",
    "bkcape": "ape",
    "bkcm4a": "m4a",
    "bkcwma": "wma",
    "tkm": "m4a",
}


def tail_marker(data: bytes) -> str:
    """读出文件尾部的方案标记，用于给出人话错误提示。"""
    if data.endswith(b"musicex\0"):
        return "musicex"
    tail4 = data[-4:]
    if tail4 in (b"STag", b"QTag"):
        return tail4.decode("ascii")
    return "未知"


class QmcDecoder:
    """按 QMC 文件布局找出密钥并解密。"""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.size = len(data)
        self.audio_size = 0
        self.cipher: qmc_algo.StreamCipher | None = None
        self.song_id: int | None = None
        self._search_key()

    # -- 找密钥 ---------------------------------------------------------- #

    def _search_key(self) -> None:
        if self.size < 8:
            raise DecryptError("文件太小，不是有效的 QMC 文件")

        tail4 = self.data[-4:]

        if tail4 == b"STag":
            raise NeedsClientError(
                "这个文件没有写入解密密钥（尾部是 STag），只能借助 QQ 音乐客户端解密。"
                "请关闭「无客户端模式」后重试。"
            )

        if tail4 == b"QTag":
            key_size = int.from_bytes(self.data[-8:-4], "big")
            self.audio_size = self.size - key_size - 8
            if self.audio_size <= 0:
                raise DecryptError("QTag 长度字段异常，文件可能被截断")
            raw_key = self.data[self.audio_size:self.size - 8]
            key_end = raw_key.find(b",")
            if key_end < 0:
                raise DecryptError("invalid key: 找不到密钥分隔符")
            self._set_cipher(raw_key[:key_end])
            id_buf = raw_key[key_end + 1:]
            id_end = id_buf.find(b",")
            if id_end >= 0:
                try:
                    self.song_id = int(id_buf[:id_end])
                except ValueError:
                    pass
            return

        key_size = int.from_bytes(tail4, "little")
        if key_size < 0x400:
            self.audio_size = self.size - key_size - 4
            if self.audio_size <= 0:
                raise DecryptError("密钥长度字段异常，文件可能损坏")
            self._set_cipher(self.data[self.audio_size:self.size - 4])
        else:
            # QMCv1：整个文件一个固定表
            self.audio_size = self.size
            self.cipher = qmc_algo.QmcStaticCipher()

    def _set_cipher(self, raw_key: bytes) -> None:
        try:
            key = qmc_algo.derive_key(raw_key)
        except ValueError as exc:
            raise DecryptError(f"密钥派生失败：{exc}") from exc
        self.cipher = (
            qmc_algo.QmcRC4Cipher(key) if len(key) > 300 else qmc_algo.QmcMapCipher(key)
        )

    # -- 解密 ------------------------------------------------------------ #

    def decrypt(self) -> bytes:
        if self.cipher is None:
            raise DecryptError("没有可用的解密器")
        if self.audio_size <= 0:
            raise DecryptError("音频长度非法")
        buf = bytearray(self.data[:self.audio_size])
        self.cipher.decrypt(buf, 0)
        return bytes(buf)


class QQMusicDecoder:
    name = "qqmusic"
    extensions = tuple(EXT_MAP)

    def matches(self, path: Path) -> bool:
        return path.suffix.lower().lstrip(".") in EXT_MAP

    def guess_container(self, path: Path) -> str | None:
        """按后缀猜明文容器，让界面能在转码前就提示「有损转无损」没意义。"""
        return EXT_MAP.get(path.suffix.lower().lstrip("."))

    def decode(self, path: Path) -> Decoded:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise DecryptError(f"读不了文件：{exc}") from exc

        notes = ["QQ 音乐加密文件不含内嵌标签，歌名/歌手将从文件名推断"]

        marker = tail_marker(data)
        if marker == "musicex":
            plain = self._decode_musicex(path, data, notes)
        elif marker == "STag":
            raise DecryptError(
                "这个文件尾部是 STag：密钥完全没有写进文件，只能由客户端在运行时提供。\n"
                "本项目不支持这种文件（实测客户端的读取接口拿不到数据，且这类文件已经很少见）。\n"
                "建议：在 QQ 音乐里重新下载，新版本客户端产出的 musicex 格式是支持的。"
            )
        else:
            plain = QmcDecoder(data).decrypt()

        # 关键校验：解出来必须是真正的音频。
        # 少了这一步，遇到未知格式会静默产出几十 MB 垃圾文件。
        container = qmc_algo.sniff_container_strict(plain)
        if container is None:
            raise DecryptError(
                f"解出来的数据不是任何已知音频格式（尾部标记：{marker}）。\n"
                "通常是遇到了新的加密方案，或者文件被改动过。已中止，未写出文件。"
            )
        return Decoded(data=plain, container=container, notes=notes)

    @staticmethod
    def _decode_musicex(path: Path, data: bytes, notes: list[str]) -> bytes:
        """musicex：密码学没变，只是 ekey 不在文件里，需要先从客户端取一次。

        拿到 ekey 之后就是纯离线解密（QMCv2 的 RC4），速度很快，
        而且能解整首歌——不像播放那样只能截到已缓冲的部分。
        """
        from . import keystore, musicex, qqmusic_frida

        try:
            info = musicex.parse(data)
        except ValueError as exc:
            raise DecryptError(f"musicex 结构解析失败：{exc}") from exc

        ekey = keystore.get(info.file_id)
        if ekey:
            notes.append(f"使用已缓存的密钥（{info.file_id}）")
        else:
            if not qqmusic_frida.available():
                raise DecryptError(
                    "这是 QQ 音乐 2026 版的新格式（musicex），密钥不在文件里，"
                    "需要先从客户端取一次。\n"
                    f"当前状态：{qqmusic_frida.describe()}\n"
                    "处理办法：启动并登录 QQ 音乐，再 pip install frida。"
                )
            notes.append("正在通过 QQ 音乐客户端取一次密钥（客户端会被打开）")
            ekey = qqmusic_frida.capture_ekey(path)
            if not ekey:
                raise DecryptError(
                    "没能从客户端拿到密钥。\n"
                    "请确认 QQ 音乐已登录、这首歌能正常播放，然后重试。"
                )
            keystore.put(info.file_id, ekey)
            notes.append(f"已取得密钥并缓存（{info.file_id}）")

        try:
            key = qmc_algo.derive_key(ekey.encode("utf-8"))
        except ValueError as exc:
            raise DecryptError(f"密钥派生失败：{exc}") from exc

        audio = bytearray(data[:info.audio_size])
        cipher = (
            qmc_algo.QmcRC4Cipher(key) if len(key) > 300
            else qmc_algo.QmcMapCipher(key)
        )
        cipher.decrypt(audio, 0)
        return bytes(audio)


register(QQMusicDecoder())
