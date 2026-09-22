"""上传选择器 accept 前后端一致性回归（2026-09-22，用户实测 .jsonId 无法选取触发的修复）。

背景：`BuildMaterialsPage.vue` 的 `<input type="file" accept="...">` 是浏览器文件对话框的
显示白名单——不在其中的后缀在 macOS 上会被灰显、无法选取（拖拽与「选择文件夹」不受限）。
后端 `protocol.detect_kind` 早已支持 `.jsonid`（JSON-LD 内容的非标准扩展名），但前端 accept
手工维护且漏登记，导致用户真实文件 `储能_V20260814_0002.jsonId` 无法通过「选择文件」选取。

冻结口径：accept 必须与 `protocol.upload_accept_exts()`（后端"会尝试解析"的后缀单一来源）
逐项一致；本测试读 vue 源码比对，防止两侧再漂移。

运行：python3 tests/run.py --test tests/test_upload_accept_parity.py
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build import protocol  # noqa: E402
from workbench.ontology_build import blacklist as blacklist_domain  # noqa: E402

PASSED = []
FAILED = []
VUE = REPO / 'frontend' / 'src' / 'ontology' / 'build' / 'BuildMaterialsPage.vue'


def check(name, ok, actual=None, expected=None):
    if ok:
        PASSED.append(name)
    else:
        FAILED.append(name)
        print('[失败] %s\n  实际=%s\n  期望=%s' % (name, str(actual)[:500], str(expected)[:500]))


def _accept_values():
    """提取 vue 里所有 accept 属性的值（当前应恰有一个 file input 带 accept）。"""
    text = VUE.read_text(encoding='utf-8')
    return re.findall(r'accept="([^"]*)"', text)


def main():
    if not VUE.is_file():
        print('[错误] 找不到 %s' % VUE)
        return 2
    accepts = _accept_values()
    check('vue 中存在恰一个带 accept 的 file input', len(accepts) == 1, len(accepts), 1)
    if len(accepts) != 1:
        return 1
    front = [item.strip().lower() for item in accepts[0].split(',') if item.strip()]
    backend = protocol.upload_accept_exts()

    check('accept 与后端单一来源逐项一致（含顺序）', front == backend,
          '前端独有=%s 后端独有=%s' % (sorted(set(front) - set(backend)),
                                       sorted(set(backend) - set(front))),
          '完全一致')
    check('accept 覆盖用户案例 .jsonid', '.jsonid' in front, front, '.jsonid in accept')
    check('accept 覆盖结构化/文档/代码/图片/zip 代表项',
          all(item in front for item in ('.json', '.jsonld', '.yaml', '.csv', '.toml', '.docx',
                                         '.pdf', '.xlsx', '.md', '.py', '.sql', '.vue', '.jpg',
                                         '.svg', '.zip')),
          [item for item in ('.json', '.yaml', '.csv', '.toml', '.docx', '.pdf', '.md', '.py',
                             '.sql', '.jpg', '.svg', '.zip') if item not in front],
          '全覆盖')
    soft = {'.' + item for item in blacklist_domain.effective_soft_exts(None)}
    hard = {'.' + item for item in blacklist_domain.HARD_EXTS}
    check('accept 不包含软黑名单后缀（音视频/归档/数据库等不该出现在选择器）',
          not (set(front) & soft), sorted(set(front) & soft), '无交集')
    check('accept 不包含硬黑名单后缀（安全边界）',
          not (set(front) & hard), sorted(set(front) & hard), '无交集')
    # 归一性：全小写、含点、无重复
    check('accept 归一（全小写含点、无重复）',
          all(item.startswith('.') and item == item.lower() for item in front)
          and len(front) == len(set(front)),
          front, 'normalized')

    print('========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), len(PASSED) + len(FAILED)))
    for name in FAILED:
        print('  失败: ' + name)
    return 0 if not FAILED else 1


if __name__ == '__main__':
    sys.exit(main())
