"""OOXML 容器（DOCX/XLSX）部件读取的解压字节守卫（解析侧，D12）。

为什么需要本模块
----------------
`zipfile.ZipFile.read(name)` 会一次性把整个部件解压进内存，长度只由压缩流实际产出决定，
**不受任何解析器侧上限约束**。材料层的两道检查都覆盖不到这一步：
`materials.zip_bomb_guard` 只看 ZIP 目录里的**声明值**（条目数、声明展开总量、单条目压缩比），
声明值可被打包方伪造；上传压缩包展开时的实际字节预算在 `materials.expand_zip/_write_member`，
但 DOCX/XLSX 本身就是一个 ZIP，解析器按部件直接读取时不经过那条路径。因此 OOXML 部件读取
必须在本层按**实际解压出来的字节数**封顶。

语义（与 `protocol.ZIP_*` 对齐）
------------------------------
* 单部件上限 `DEFAULT_MEMBER_LIMIT`（默认 64 MiB）：既查 header 声明值（声明即超限直接拒，
  连一个字节都不解压），也按流式实际读取计数；超过即抛 `ZipBombDetected`。
* 整包累计上限 `DEFAULT_TOTAL_LIMIT` = `protocol.ZIP_EXPANDED_BYTES`：一个压缩包内被本解析器
  真正解压出来的总字节数不得超过它（多个部件分摊也不放行）。
* 计数以 `READ_CHUNK_BYTES` 为粒度，峰值内存约为「上限 + 2 个分块」，与部件真实大小无关。
* 只做只读计数与拒绝：不解压到磁盘、不落临时文件、不执行任何部件内容。

失败一律抛 `ZipBombDetected`（带中文原因），由调用方转成显式 `failure(...)` 或
`failedSegments` 条目——绝不静默截断后当成完整文档继续解析。
"""

import zipfile

from workbench.ontology_build import protocol

READ_CHUNK_BYTES = 256 * 1024            # 流式读取分块（同时决定封顶粒度）
DEFAULT_MEMBER_LIMIT = 64 * 1024 * 1024  # 单部件实际解压字节上限（MiB）
DEFAULT_TOTAL_LIMIT = protocol.ZIP_EXPANDED_BYTES   # 整包累计实际解压字节上限


class ZipBombDetected(Exception):
    """解压字节超过守卫上限（声明值或实际值）。消息可直接作为材料失败原因展示。"""

    def __init__(self, message, member='', read_bytes=0, limit=0):
        super().__init__(message)
        self.member = str(member or '')
        self.read_bytes = int(read_bytes or 0)
        self.limit = int(limit or 0)


class CappedZipFile:
    """`zipfile.ZipFile` 的只读包装：所有 `read()` 走按实际字节计数的封顶流式读取。

    只暴露 OOXML 解析需要的目录/读取方法（namelist / infolist / getinfo / read），不提供写
    操作，也不解压到文件系统。刻意不代理 `ZipFile.testzip()`：它会把每个条目完整解压一遍
    做 CRC 校验，正好绕过本类的字节封顶。
    """

    def __init__(self, archive, member_limit=None, total_limit=None):
        self._archive = archive
        self._member_limit = _positive(member_limit) or DEFAULT_MEMBER_LIMIT
        # 累计上限默认取整包展开上限；显式传更小的成员上限时不得低于单部件上限，
        # 否则第一个部件也读不完（那属于配置错误，这里按“至少放行一个部件”处理）。
        self._total_limit = _positive(total_limit) or max(DEFAULT_TOTAL_LIMIT, self._member_limit)
        self._read_total = 0

    # --- 目录（只读，不产生解压） ------------------------------------------------
    def namelist(self):
        return self._archive.namelist()

    def infolist(self):
        return self._archive.infolist()

    def getinfo(self, name):
        return self._archive.getinfo(name)

    @property
    def read_bytes(self):
        """本实例已累计实际解压的字节数（测试与 notes 用）。"""
        return self._read_total

    @property
    def member_limit(self):
        return self._member_limit

    @property
    def total_limit(self):
        return self._total_limit

    # --- 生命周期 ----------------------------------------------------------------
    def close(self):
        self._archive.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    # --- 有界读取 ----------------------------------------------------------------
    def read(self, name):
        """按实际解压字节计数读取部件；超单部件或累计上限即抛 `ZipBombDetected`。"""
        member = str(name or '')
        info = self._archive.getinfo(member)      # KeyError 交由调用方按缺部件处理
        declared = max(0, int(getattr(info, 'file_size', 0) or 0))
        if declared > self._member_limit:
            raise ZipBombDetected(
                '部件 %s 声明解压后 %d 字节，超过单部件上限 %d 字节：未读取任何内容。'
                % (member, declared, self._member_limit), member, 0, self._member_limit)
        if self._read_total >= self._total_limit:
            raise ZipBombDetected(
                '整包已实际解压 %d 字节，达到累计上限 %d 字节：部件 %s 未读取。'
                % (self._read_total, self._total_limit, member), member,
                self._read_total, self._total_limit)

        chunks = []
        total = 0
        try:
            stream = self._archive.open(member)
        except zipfile.BadZipFile as exc:
            raise ZipBombDetected('部件 %s 无法安全打开（压缩包结构异常）：%s' % (member, exc),
                                  member, 0, self._member_limit)
        with stream:
            while True:
                part = stream.read(READ_CHUNK_BYTES)
                if not part:
                    break
                total += len(part)
                if total > self._member_limit:
                    raise ZipBombDetected(
                        '部件 %s 实际解压已达 %d 字节，超过单部件上限 %d 字节：读取中止'
                        '（声明值 %d 字节不可信）。' % (member, total, self._member_limit, declared),
                        member, total, self._member_limit)
                if self._read_total + total > self._total_limit:
                    raise ZipBombDetected(
                        '整包实际解压累计将超过上限 %d 字节（已计 %d 字节 + 本部件 %d 字节）：'
                        '读取中止。' % (self._total_limit, self._read_total, total),
                        member, total, self._total_limit)
                chunks.append(part)
        self._read_total += total
        return b''.join(chunks)


def open_capped(path, member_limit=None, total_limit=None):
    """打开 ZIP 容器并返回 `CappedZipFile`（调用方 `with` 使用）。

    打开阶段的 `zipfile.BadZipFile` / `OSError` 原样抛出，由解析器按「不是有效 ZIP 包 /
    读取失败」报告；`read()` 里打不开某个条目（`archive.open()` 抛 `BadZipFile`）归入
    `ZipBombDetected`，而解压过程中的 CRC/压缩流错误保持原异常向上抛出，由解析器显式报告。
    """
    archive = zipfile.ZipFile(str(path))
    return CappedZipFile(archive, member_limit=member_limit, total_limit=total_limit)


def limit_note():
    """coverage.notes 用的限额说明（本模块默认上限，即解析器实际生效值；不夸大能力）。

    测试用 `CappedZipFile(..., member_limit=…)` 显式覆盖时，此处文本描述的是默认上限，
    生产路径（docx/xlsx）始终用默认值。
    """
    return ('OOXML 部件按实际解压字节读取：单部件上限 %d 字节、整包累计上限 %d 字节'
            '（超限即显式失败，不按 header 声明值放行）。'
            % (DEFAULT_MEMBER_LIMIT, DEFAULT_TOTAL_LIMIT))


def _positive(value):
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0
