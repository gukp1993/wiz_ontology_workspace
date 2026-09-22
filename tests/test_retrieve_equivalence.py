# -*- coding: utf-8 -*-
"""retrieve 优化等价金样（需求《retrieve 筛选算法优化》R2/R6）。

基线实现快照：本文件内嵌优化前 retrieval.py 的逐字节副本（base64，取自
worktree/build-governance 分支 3dc51dc 线 = 优化前基线版本）。同一 facts+scope
分别运行基线实现（reference_*，exec 加载）与现行实现（workbench.ontology_build.
retrieval），断言 select_scope 输出逐字节一致：

* relevant/related/excluded 三列表（含元素顺序）；
* reasons 全文逐字（含依赖扩展、重复副本、弱词/hint 理由文本）；
* counts。

S1 语义边界说明：合并正则只做早停探测；reasons 展示词一律按原词表顺序、
原 term-in-text 成员判定复算（findall 非重叠匹配与逐词成员判定在重叠词上不等价）。

夹具：小样手构造（CJK 长句/ASCII/空 data/空 snippet/重复 snippet/依赖扩展/
正则元字符词条/空词表/单词条/超长词/大小写 casefold）+ 确定性合成集（8000 条，
约 4% 含范围关键词）。

运行：.venv/bin/python tests/test_retrieve_equivalence.py
（纯内存计算，无网络、不连真实库、无需数据根。）
"""
import base64
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build import retrieval as current_retrieval  # noqa: E402

_REFERENCE_SOURCE_B64 = (
    'IiIi5LuO54mp5paZ5p6E5bu65pys5L2T77ya5pys5Zyw5qOA57Si5LiO6IyD5Zu0562b6YCJ77yI56Gu5a6a5oCn44CB57qv'
    '5qCH5YeG5bqT44CBKirkuI3osIPnlKjmqKHlnosqKu+8ieOAggoK6IGM6LSj6L6555WM77yI5o6l5Y+j5paH5qGjIDA4IMKn'
    'MS4zL8KnMS4244CB5byA5Y+R6K6h5YiSIMKnNS4y77yJ77yaCiog5Y+q5a+55bey6Kej5p6Q55qEICoq5LqL5a6e77yIRmFj'
    'dO+8iSoqIOWBmuWFs+mUruivjeWRveS4reS4juaciemZkOS+nei1luaJqeWxle+8jOS6p+WHuuOAjOWTquS6m+S6i+WunuWx'
    'nuS6juacrOasoQogIOiMg+WbtOOAjeeahOijgeWGs+S4jueQhueUse+8m+S4jeWBmuivreS5ieeQhuino+OAgeS4jeWBmuWA'
    'memAieaKveixoeOAgeS4jeeMnOWNleS9jS/lhbPns7vjgIIKKiDnu53kuI3lm6Dmlofku7blkI3miJbmianlsZXlkI3ov4fm'
    'u6TmnZDmlpnvvJrlkb3kuK3kuI3liLDlhbPplK7or43nmoTkuovlrp7ku43kv53lrojkv53nlZnlnKggYHJlbGF0ZWRgCiAg'
    '77yI5LiN5Lii5p2Q5paZ77yM5Lqk55Sx5qih5Z6L5LiO5Lq65bel5Yik5pat77yJ44CCCiog5ZCM5LiAIHNuaXBwZXQg5ZOI'
    '5biM5Ye6546w5aSa5qyh5Y+q566X5LiA5p2h6K+B5o2u77yI6YeN5aSN5Ymv5pys5LiN5p6E5oiQ54us56uL5L2Q6K+B77yJ'
    '77yb6YeN5aSN6aG55LuN5L+d55WZ5ZyoCiAgYHJlbGF0ZWRgIOW5tuWcqCByZWFzb25zIOmHjOivtOaYjuS4juWTquadoeS6'
    'i+WunumHjeWkjeOAggoqIOWujOWFqOehruWumuaAp++8muWQjOS4gOi+k+WFpeawuOi/nOWQjOS4gOi+k+WHuu+8iOS4jeS+'
    'nei1luaXtumXtOOAgemaj+acuuaVsOOAgeWtl+WFuOW6j+S7peWklueahOmhuuW6j++8ieOAggoK6IyD5Zu06K+N5YiG57qn'
    '77yI5L+d5a6I5Y+j5b6E77yJ77yaCiog5by66K+NID0g6IyD5Zu05paH5pys6YeM5pW05q616Iux5paHL+aVsOWtl+agh+iv'
    'huaIluaVtOauteS4reaWh++8iDLigJMxMiDlrZfvvInihpIg5ZG95LitIGBpbmNsdWRlYCDliKQgcmVsZXZhbnTjgIIKKiDl'
    'vLHor40gPSDotoXplb/kuK3mlofkuLLliIflh7rnmoQgMuKAkzMg5a2X54mH5q61IOKGkiDlj6rnlKjkuo7miorkuovlrp7m'
    'oIfkuLogcmVsYXRlZCDlubbnu5nlh7rmm7TlhbfkvZPnmoTnkIbnlLHvvIwKICDnu53kuI3nlKjlvLHor43liKQgcmVsZXZh'
    'bnTvvIzkuZ/kuI3nlKjlvLHor43liKQgZXhjbHVkZWTvvIjpgb/lhY3or6/mjpLpmaTvvInjgIIKKiBgZ29hbGAgLyBgcmVs'
    'YXRpb25zYCDlj6rkvZzkuLogcmVsYXRlZCDnmoTnkIbnlLHmnaXmupDvvIjkuI3mlLnlj5ggaW5jbHVkZS9leGNsdWRlIOij'
    'geWGs++8ieOAggoiIiIKCmltcG9ydCBoYXNobGliCmltcG9ydCByZQoKIyDkuK3mlofvvIjlkKvmianlsZXljLrvvInkuI7o'
    'i7Hmlocv5pWw5a2X5qCH6K+G55qE57KX5YiH5YiG77ya5LiN5YGa5YiG6K+N77yM5L+d5oyB56Gu5a6a5oCnCl9URVJNX1JF'
    'ID0gcmUuY29tcGlsZShyJ1tBLVphLXowLTlfXSt8W1x1MzQwMC1cdTRkYmZcdTRlMDAtXHU5ZmZmXHVmOTAwLVx1ZmFmZl0r'
    'JykKX0FTQ0lJX09OTFkgPSByZS5jb21waWxlKHInXltBLVphLXowLTlfXSskJykKCk1JTl9BU0NJSV9URVJNID0gMwpNSU5f'
    'Q0pLX1RFUk0gPSAyCk1BWF9URVJNX0xFTiA9IDEyCldFQUtfR1JBTV9NSU4gPSAyCldFQUtfR1JBTV9NQVggPSAzCkRFUEVO'
    'REVOVF9QRVJfSElUID0gNSAgICAgICAgICAjIOavj+S4qiByZWxldmFudCDmnIDlpJrluKblh7rnmoTlkIzmqKHlnZcv5ZCM'
    '5paH5Lu25LqL5a6e5pWwCl9NQVhfRklFTERfQ0hBUlMgPSA0MDAKX01BWF9EQVRBX0lURU1TID0gNDAKCgpkZWYgX2lkX29m'
    'KGZhY3QpOgogICAgcmV0dXJuIHN0cigoZmFjdCBvciB7fSkuZ2V0KCdpZCcpIG9yICcnKQoKCmRlZiBfc25pcHBldF9oYXNo'
    'KHRleHQpOgogICAgcmV0dXJuIGhhc2hsaWIuc2hhMShzdHIodGV4dCBvciAnJykuZW5jb2RlKCd1dGYtOCcpKS5oZXhkaWdl'
    'c3QoKQoKCmRlZiBfZmxhdHRlbih2YWx1ZSwgZGVwdGg9MCwgb3V0PU5vbmUpOgogICAgIiIiZGF0YSDlgLwg4oaSIOWPguS4'
    'juajgOe0oueahOWtl+espuS4suWIl+ihqO+8iOmZkOa3semZkOmHj++8jOmYsuatoui2heWkp+W1jOWll+aLluaFouajgOe0'
    'ou+8ieOAgiIiIgogICAgb3V0ID0gW10gaWYgb3V0IGlzIE5vbmUgZWxzZSBvdXQKICAgIGlmIGxlbihvdXQpID49IF9NQVhf'
    'REFUQV9JVEVNUyBvciBkZXB0aCA+IDI6CiAgICAgICAgcmV0dXJuIG91dAogICAgaWYgaXNpbnN0YW5jZSh2YWx1ZSwgZGlj'
    'dCk6CiAgICAgICAgZm9yIGtleSwgaXRlbSBpbiB2YWx1ZS5pdGVtcygpOgogICAgICAgICAgICBpZiBsZW4ob3V0KSA+PSBf'
    'TUFYX0RBVEFfSVRFTVM6CiAgICAgICAgICAgICAgICBicmVhawogICAgICAgICAgICBvdXQuYXBwZW5kKHN0cihrZXkpKQog'
    'ICAgICAgICAgICBfZmxhdHRlbihpdGVtLCBkZXB0aCArIDEsIG91dCkKICAgIGVsaWYgaXNpbnN0YW5jZSh2YWx1ZSwgKGxp'
    'c3QsIHR1cGxlKSk6CiAgICAgICAgZm9yIGl0ZW0gaW4gdmFsdWU6CiAgICAgICAgICAgIGlmIGxlbihvdXQpID49IF9NQVhf'
    'REFUQV9JVEVNUzoKICAgICAgICAgICAgICAgIGJyZWFrCiAgICAgICAgICAgIF9mbGF0dGVuKGl0ZW0sIGRlcHRoICsgMSwg'
    'b3V0KQogICAgZWxpZiBpc2luc3RhbmNlKHZhbHVlLCAoc3RyLCBpbnQsIGZsb2F0KSkgYW5kIG5vdCBpc2luc3RhbmNlKHZh'
    'bHVlLCBib29sKToKICAgICAgICB0ZXh0ID0gc3RyKHZhbHVlKQogICAgICAgIGlmIHRleHQ6CiAgICAgICAgICAgIG91dC5h'
    'cHBlbmQodGV4dFs6X01BWF9GSUVMRF9DSEFSU10pCiAgICByZXR1cm4gb3V0CgoKZGVmIHNlYXJjaGFibGVfdGV4dChmYWN0'
    'KToKICAgICIiIuS6i+WunueahOWPr+ajgOe0ouaWh+acrO+8iHNuaXBwZXQgKyDlrprkvY0gKyDnu5PmnoTljJblrZfmrrXv'
    'vIznu5/kuIAgY2FzZWZvbGTvvInjgIIiIiIKICAgIGZhY3QgPSBmYWN0IGlmIGlzaW5zdGFuY2UoZmFjdCwgZGljdCkgZWxz'
    'ZSB7fQogICAgcGFydHMgPSBbc3RyKGZhY3QuZ2V0KCdzbmlwcGV0Jykgb3IgJycpLCBzdHIoZmFjdC5nZXQoJ2tpbmQnKSBv'
    'ciAnJyksCiAgICAgICAgICAgICBzdHIoZmFjdC5nZXQoJ21vZHVsZScpIG9yICcnKV0KICAgIGxvY2F0b3IgPSBmYWN0Lmdl'
    'dCgnbG9jYXRvcicpIGlmIGlzaW5zdGFuY2UoZmFjdC5nZXQoJ2xvY2F0b3InKSwgZGljdCkgZWxzZSB7fQogICAgZm9yIHZh'
    'bHVlIGluIGxvY2F0b3IudmFsdWVzKCk6CiAgICAgICAgaWYgaXNpbnN0YW5jZSh2YWx1ZSwgKHN0ciwgaW50LCBmbG9hdCkp'
    'IGFuZCBub3QgaXNpbnN0YW5jZSh2YWx1ZSwgYm9vbCk6CiAgICAgICAgICAgIHBhcnRzLmFwcGVuZChzdHIodmFsdWUpKQog'
    'ICAgcGFydHMuZXh0ZW5kKF9mbGF0dGVuKGZhY3QuZ2V0KCdkYXRhJykpKQogICAgcmV0dXJuICcgJy5qb2luKHBhcnQgZm9y'
    'IHBhcnQgaW4gcGFydHMgaWYgcGFydCkuY2FzZWZvbGQoKQoKCmRlZiBpbmRleF90b2tlbnModGV4dCk6CiAgICAiIiLmlofm'
    'nKwg4oaSIOe0ouW8lSB0b2tlbu+8iOiLseaWhy/mlbDlrZfmoIfor4bljp/moLflsI/lhpnvvJvkuK3mlofmlbTmrrUgMuKA'
    'kzEyIOWtl++8ieOAgiIiIgogICAgdG9rZW5zID0gW10KICAgIHNlZW4gPSBzZXQoKQogICAgZm9yIGNodW5rIGluIF9URVJN'
    'X1JFLmZpbmRhbGwoc3RyKHRleHQgb3IgJycpKToKICAgICAgICB0b2tlbiA9IGNodW5rLmNhc2Vmb2xkKCkKICAgICAgICBp'
    'ZiBfQVNDSUlfT05MWS5tYXRjaChjaHVuayk6CiAgICAgICAgICAgIGlmIGxlbihjaHVuaykgPCAyOgogICAgICAgICAgICAg'
    'ICAgY29udGludWUKICAgICAgICBlbGlmIG5vdCAoTUlOX0NKS19URVJNIDw9IGxlbihjaHVuaykgPD0gTUFYX1RFUk1fTEVO'
    'KToKICAgICAgICAgICAgY29udGludWUKICAgICAgICBpZiB0b2tlbiBpbiBzZWVuOgogICAgICAgICAgICBjb250aW51ZQog'
    'ICAgICAgIHNlZW4uYWRkKHRva2VuKQogICAgICAgIHRva2Vucy5hcHBlbmQodG9rZW4pCiAgICByZXR1cm4gdG9rZW5zCgoK'
    'ZGVmIGJ1aWxkX2luZGV4KGZhY3RzKToKICAgICIiIuS6i+WunuWIl+ihqCDihpIg5pys5Zyw5qOA57Si57Si5byV44CCCgog'
    'ICAg6L+U5ZueIHsnbW9kdWxlcyc6IFsuLi5dLCAndG9rZW5zJzoge3Rva2VuOiBbZmFjdElkXX0sICdieUtpbmQnOiB7a2lu'
    'ZDogW2ZhY3RJZF19LCAuLi5977ybCiAgICDpop3lpJbplK7vvIhieU1vZHVsZS9ieUZpbGUvdGV4dC9zbmlwcGV0SGFzaC9t'
    'YXRlcmlhbC9vcmRlcu+8ieS+myBzZWxlY3Rfc2NvcGUg5LiO5aSN5qC45L2/55So77yMCiAgICDlhajpg6jmjInovpPlhaXp'
    'obrluo/nqLPlrprnlJ/miJDjgIIKICAgICIiIgogICAgbW9kdWxlcyA9IFtdCiAgICB0b2tlbnMgPSB7fQogICAgYnlfa2lu'
    'ZCA9IHt9CiAgICBieV9tb2R1bGUgPSB7fQogICAgYnlfZmlsZSA9IHt9CiAgICB0ZXh0cyA9IHt9CiAgICBoYXNoZXMgPSB7'
    'fQogICAgbWF0ZXJpYWxzID0ge30KICAgIG9yZGVyID0gW10KICAgIGZvciBmYWN0IGluIGZhY3RzIG9yIFtdOgogICAgICAg'
    'IGlmIG5vdCBpc2luc3RhbmNlKGZhY3QsIGRpY3QpOgogICAgICAgICAgICBjb250aW51ZQogICAgICAgIGZhY3RfaWQgPSBf'
    'aWRfb2YoZmFjdCkKICAgICAgICBpZiBub3QgZmFjdF9pZDoKICAgICAgICAgICAgY29udGludWUKICAgICAgICBvcmRlci5h'
    'cHBlbmQoZmFjdF9pZCkKICAgICAgICBtb2R1bGUgPSBzdHIoZmFjdC5nZXQoJ21vZHVsZScpIG9yICcnKQogICAgICAgIGlm'
    'IG1vZHVsZSBhbmQgbW9kdWxlIG5vdCBpbiBtb2R1bGVzOgogICAgICAgICAgICBtb2R1bGVzLmFwcGVuZChtb2R1bGUpCiAg'
    'ICAgICAga2luZCA9IHN0cihmYWN0LmdldCgna2luZCcpIG9yICcnKQogICAgICAgIGJ5X2tpbmQuc2V0ZGVmYXVsdChraW5k'
    'LCBbXSkuYXBwZW5kKGZhY3RfaWQpCiAgICAgICAgYnlfbW9kdWxlLnNldGRlZmF1bHQobW9kdWxlLCBbXSkuYXBwZW5kKGZh'
    'Y3RfaWQpCiAgICAgICAgbG9jYXRvciA9IGZhY3QuZ2V0KCdsb2NhdG9yJykgaWYgaXNpbnN0YW5jZShmYWN0LmdldCgnbG9j'
    'YXRvcicpLCBkaWN0KSBlbHNlIHt9CiAgICAgICAgZmlsZV9uYW1lID0gc3RyKGxvY2F0b3IuZ2V0KCdmaWxlJykgb3IgJycp'
    'CiAgICAgICAgYnlfZmlsZS5zZXRkZWZhdWx0KGZpbGVfbmFtZSwgW10pLmFwcGVuZChmYWN0X2lkKQogICAgICAgIHRleHQg'
    'PSBzZWFyY2hhYmxlX3RleHQoZmFjdCkKICAgICAgICB0ZXh0c1tmYWN0X2lkXSA9IHRleHQKICAgICAgICBoYXNoZXNbZmFj'
    'dF9pZF0gPSBfc25pcHBldF9oYXNoKGZhY3QuZ2V0KCdzbmlwcGV0JykpCiAgICAgICAgbWF0ZXJpYWxzW2ZhY3RfaWRdID0g'
    'c3RyKGZhY3QuZ2V0KCdtYXRlcmlhbElkJykgb3IgJycpCiAgICAgICAgZm9yIHRva2VuIGluIGluZGV4X3Rva2VucygnICcu'
    'am9pbihbdGV4dCwgbW9kdWxlLCBraW5kLCBmaWxlX25hbWVdKSk6CiAgICAgICAgICAgIGJ1Y2tldCA9IHRva2Vucy5zZXRk'
    'ZWZhdWx0KHRva2VuLCBbXSkKICAgICAgICAgICAgaWYgZmFjdF9pZCBub3QgaW4gYnVja2V0OgogICAgICAgICAgICAgICAg'
    'YnVja2V0LmFwcGVuZChmYWN0X2lkKQogICAgcmV0dXJuIHsnbW9kdWxlcyc6IG1vZHVsZXMsICd0b2tlbnMnOiB0b2tlbnMs'
    'ICdieUtpbmQnOiBieV9raW5kLCAnYnlNb2R1bGUnOiBieV9tb2R1bGUsCiAgICAgICAgICAgICdieUZpbGUnOiBieV9maWxl'
    'LCAndGV4dCc6IHRleHRzLCAnc25pcHBldEhhc2gnOiBoYXNoZXMsICdtYXRlcmlhbCc6IG1hdGVyaWFscywKICAgICAgICAg'
    'ICAgJ29yZGVyJzogb3JkZXJ9CgoKZGVmIHNjb3BlX3Rlcm1zKHRleHQpOgogICAgIiIi6IyD5Zu05paH5pysIOKGkiAo5by6'
    '6K+NLCDlvLHor40p77yM5Z2H5Li65Y676YeN5ZCO55qE5pyJ5bqP5YiX6KGo44CCIiIiCiAgICBzdHJvbmcsIHdlYWsgPSBb'
    'XSwgW10KICAgIHN0cm9uZ19zZWVuLCB3ZWFrX3NlZW4gPSBzZXQoKSwgc2V0KCkKICAgIGZvciBjaHVuayBpbiBfVEVSTV9S'
    'RS5maW5kYWxsKHN0cih0ZXh0IG9yICcnKSk6CiAgICAgICAgZm9sZGVkID0gY2h1bmsuY2FzZWZvbGQoKQogICAgICAgIGlm'
    'IF9BU0NJSV9PTkxZLm1hdGNoKGNodW5rKToKICAgICAgICAgICAgaWYgbGVuKGNodW5rKSA8IE1JTl9BU0NJSV9URVJNOgog'
    'ICAgICAgICAgICAgICAgY29udGludWUKICAgICAgICAgICAgaWYgZm9sZGVkIG5vdCBpbiBzdHJvbmdfc2VlbjoKICAgICAg'
    'ICAgICAgICAgIHN0cm9uZ19zZWVuLmFkZChmb2xkZWQpCiAgICAgICAgICAgICAgICBzdHJvbmcuYXBwZW5kKGZvbGRlZCkK'
    'ICAgICAgICAgICAgY29udGludWUKICAgICAgICBpZiBNSU5fQ0pLX1RFUk0gPD0gbGVuKGNodW5rKSA8PSBNQVhfVEVSTV9M'
    'RU46CiAgICAgICAgICAgIGlmIGZvbGRlZCBub3QgaW4gc3Ryb25nX3NlZW46CiAgICAgICAgICAgICAgICBzdHJvbmdfc2Vl'
    'bi5hZGQoZm9sZGVkKQogICAgICAgICAgICAgICAgc3Ryb25nLmFwcGVuZChmb2xkZWQpCiAgICAgICAgICAgIGNvbnRpbnVl'
    'CiAgICAgICAgIyDotoXplb/kuK3mlofkuLLvvJrmlbTmrrXku43mmK/lvLror43vvIjplb/lkb3kuK3mm7Tnsr7noa7vvInv'
    'vIzlkIzml7bliIflh7ogMuKAkzMg5a2X5byx54mH5q61CiAgICAgICAgaWYgbGVuKGNodW5rKSA+IE1BWF9URVJNX0xFTiBh'
    'bmQgZm9sZGVkIG5vdCBpbiBzdHJvbmdfc2VlbjoKICAgICAgICAgICAgc3Ryb25nX3NlZW4uYWRkKGZvbGRlZCkKICAgICAg'
    'ICAgICAgc3Ryb25nLmFwcGVuZChmb2xkZWQpCiAgICAgICAgZm9yIHNpemUgaW4gcmFuZ2UoV0VBS19HUkFNX01JTiwgV0VB'
    'S19HUkFNX01BWCArIDEpOgogICAgICAgICAgICBmb3Igc3RhcnQgaW4gcmFuZ2UoMCwgbWF4KDAsIGxlbihjaHVuaykgLSBz'
    'aXplICsgMSkpOgogICAgICAgICAgICAgICAgZ3JhbSA9IGNodW5rW3N0YXJ0OnN0YXJ0ICsgc2l6ZV0uY2FzZWZvbGQoKQog'
    'ICAgICAgICAgICAgICAgaWYgZ3JhbSBub3QgaW4gd2Vha19zZWVuIGFuZCBncmFtIG5vdCBpbiBzdHJvbmdfc2VlbjoKICAg'
    'ICAgICAgICAgICAgICAgICB3ZWFrX3NlZW4uYWRkKGdyYW0pCiAgICAgICAgICAgICAgICAgICAgd2Vhay5hcHBlbmQoZ3Jh'
    'bSkKICAgIHJldHVybiBzdHJvbmcsIHdlYWsKCgpkZWYgX2hpdHModGVybXMsIHRleHQpOgogICAgcmV0dXJuIFt0ZXJtIGZv'
    'ciB0ZXJtIGluIHRlcm1zIGlmIHRlcm0gYW5kIHRlcm0gaW4gdGV4dF0KCgpkZWYgc2VsZWN0X3Njb3BlKGZhY3RzLCBzY29w'
    'ZSwgaW5kZXg9Tm9uZSk6CiAgICAiIiLmjInojIPlm7TmlofmnKzoo4HlhrPkuovlrp7lvZLlsZ7jgIIKCiAgICDov5Tlm54g'
    'eydyZWxldmFudCc6IFtmYWN0SWRdLCAncmVsYXRlZCc6IFtmYWN0SWRdLCAnZXhjbHVkZWQnOiBbZmFjdElkXSwKICAgICAg'
    'ICAgICdyZWFzb25zJzoge2ZhY3RJZDog5Lit5paH6K+05piOfSwgJ2NvdW50cyc6IHsuLi59fQogICAg6KeE5YiZ77yI6aG6'
    '5bqP5Y2z5LyY5YWI57qn77yJ77yaCiAgICAgIDEuIOWRveS4rSBpbmNsdWRlIOW8uuivjSDihpIgcmVsZXZhbnTvvJvlkIwg'
    'c25pcHBldCDlk4jluIzlt7LlnKjliY3pnaLkvZzkuLror4Hmja7lh7rnjrAg4oaSIHJlbGF0ZWTvvIjph43lpI3lia/mnKzv'
    'vInjgIIKICAgICAgMi4g5ZCm5YiZ5ZG95LitIGV4Y2x1ZGUg5by66K+NIOKGkiBleGNsdWRlZO+8iOiusOW9leWOn+WboO+8'
    'ieOAggogICAgICAzLiDlhbbkvZkg4oaSIOWFiOWBmuS+nei1luaJqeWxle+8iOS4jiByZWxldmFudCDlkIwgbW9kdWxlIOaI'
    'luWQjCBmaWxl77yM5q+P5LiqIHJlbGV2YW50IOacgOWkmiA1IOadoe+8ieKGkiByZWxhdGVk44CCCiAgICAgIDQuIOS7jeac'
    'quW9kuexu+eahCDihpIgcmVsYXRlZO+8iOS/neWuiOS/neeVme+8jOS4jeS4ouadkOaWme+8ieOAggogICAg5o6S6Zmk6aG5'
    '57ud5LiN5Zug5L6d6LWW5omp5bGV5aSN5rS777yb5paH5Lu25ZCNL+aJqeWxleWQjeS4jeWPguS4jui/h+a7pOOAggogICAg'
    'IiIiCiAgICBzY29wZSA9IHNjb3BlIGlmIGlzaW5zdGFuY2Uoc2NvcGUsIGRpY3QpIGVsc2Uge30KICAgIGZhY3RzID0gW2Zh'
    'Y3QgZm9yIGZhY3QgaW4gKGZhY3RzIG9yIFtdKSBpZiBpc2luc3RhbmNlKGZhY3QsIGRpY3QpIGFuZCBfaWRfb2YoZmFjdCld'
    'CiAgICBpbmRleCA9IGluZGV4IG9yIGJ1aWxkX2luZGV4KGZhY3RzKQogICAgaW5jbHVkZV9zdHJvbmcsIGluY2x1ZGVfd2Vh'
    'ayA9IHNjb3BlX3Rlcm1zKHNjb3BlLmdldCgnaW5jbHVkZScpKQogICAgZXhjbHVkZV9zdHJvbmcsIF9leGNsdWRlX3dlYWsg'
    'PSBzY29wZV90ZXJtcyhzY29wZS5nZXQoJ2V4Y2x1ZGUnKSkKICAgIGhpbnRfc3Ryb25nLCBfaGludF93ZWFrID0gc2NvcGVf'
    'dGVybXMoc2NvcGUuZ2V0KCdnb2FsJykpCiAgICByZWxhdGlvbl9zdHJvbmcsIF9yZWxhdGlvbl93ZWFrID0gc2NvcGVfdGVy'
    'bXMoc2NvcGUuZ2V0KCdyZWxhdGlvbnMnKSkKICAgIGhpbnRzID0gbGlzdChoaW50X3N0cm9uZykgKyBsaXN0KHJlbGF0aW9u'
    'X3N0cm9uZykKCiAgICBjb250ZXh0cyA9IHtfaWRfb2YoZmFjdCk6IGZhY3QgZm9yIGZhY3QgaW4gZmFjdHN9CiAgICB0ZXh0'
    'cyA9IGluZGV4LmdldCgndGV4dCcpIG9yIHt9CiAgICBoYXNoZXMgPSBpbmRleC5nZXQoJ3NuaXBwZXRIYXNoJykgb3Ige30K'
    'ICAgIGJ5X21vZHVsZSA9IGluZGV4LmdldCgnYnlNb2R1bGUnKSBvciB7fQogICAgYnlfZmlsZSA9IGluZGV4LmdldCgnYnlG'
    'aWxlJykgb3Ige30KICAgIG9yZGVyID0gW2ZhY3RfaWQgZm9yIGZhY3RfaWQgaW4gKGluZGV4LmdldCgnb3JkZXInKSBvciBb'
    'XSkgaWYgZmFjdF9pZCBpbiBjb250ZXh0c10gb3IgbGlzdChjb250ZXh0cykKCiAgICByZWxldmFudCwgcmVsYXRlZCwgZXhj'
    'bHVkZWQgPSBbXSwgW10sIFtdCiAgICByZWFzb25zID0ge30KICAgIGNsYWltZWQgPSB7fQogICAgdXNlZF9oYXNoID0ge30K'
    'ICAgIGZvciBmYWN0X2lkIGluIG9yZGVyOgogICAgICAgIHRleHQgPSB0ZXh0cy5nZXQoZmFjdF9pZCwgJycpCiAgICAgICAg'
    'aGl0X2luY2x1ZGUgPSBfaGl0cyhpbmNsdWRlX3N0cm9uZywgdGV4dCkKICAgICAgICBoaXRfZXhjbHVkZSA9IF9oaXRzKGV4'
    'Y2x1ZGVfc3Ryb25nLCB0ZXh0KQogICAgICAgIGlmIGhpdF9pbmNsdWRlOgogICAgICAgICAgICBkaWdlc3QgPSBoYXNoZXMu'
    'Z2V0KGZhY3RfaWQsICcnKQogICAgICAgICAgICBpZiBkaWdlc3QgYW5kIGRpZ2VzdCBpbiB1c2VkX2hhc2g6CiAgICAgICAg'
    'ICAgICAgICByZWxhdGVkLmFwcGVuZChmYWN0X2lkKQogICAgICAgICAgICAgICAgcmVhc29uc1tmYWN0X2lkXSA9ICgn5LiO'
    'ICVzIOWGheWuuemHjeWkje+8iOWQjOS4gOeJh+auteWJr+acrO+8jOS4jeS9nOS4uueLrOeri+S9kOivge+8iScKICAgICAg'
    'ICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgJSB1c2VkX2hhc2hbZGlnZXN0XSkKICAgICAgICAgICAgICAgIGNsYWlt'
    'ZWRbZmFjdF9pZF0gPSAncmVsYXRlZCcKICAgICAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgICAgIGlmIGRpZ2VzdDoK'
    'ICAgICAgICAgICAgICAgIHVzZWRfaGFzaFtkaWdlc3RdID0gZmFjdF9pZAogICAgICAgICAgICBub3RlID0gJ+WRveS4ree6'
    's+WFpeiMg+WbtO+8miVzJyAlICfjgIEnLmpvaW4oaGl0X2luY2x1ZGVbOjVdKQogICAgICAgICAgICBpZiBoaXRfZXhjbHVk'
    'ZToKICAgICAgICAgICAgICAgIG5vdGUgKz0gJ++8m+WQjOaXtuWRveS4reaOkumZpOiMg+WbtOivje+8iOe6s+WFpeS8mOWF'
    'iO+8jOivt+S6uuW3peehruiupO+8ie+8miVzJyAlICfjgIEnLmpvaW4oaGl0X2V4Y2x1ZGVbOjVdKQogICAgICAgICAgICBy'
    'ZWxldmFudC5hcHBlbmQoZmFjdF9pZCkKICAgICAgICAgICAgY2xhaW1lZFtmYWN0X2lkXSA9ICdyZWxldmFudCcKICAgICAg'
    'ICAgICAgcmVhc29uc1tmYWN0X2lkXSA9IG5vdGUKICAgICAgICBlbGlmIGhpdF9leGNsdWRlOgogICAgICAgICAgICBleGNs'
    'dWRlZC5hcHBlbmQoZmFjdF9pZCkKICAgICAgICAgICAgY2xhaW1lZFtmYWN0X2lkXSA9ICdleGNsdWRlZCcKICAgICAgICAg'
    'ICAgcmVhc29uc1tmYWN0X2lkXSA9ICflkb3kuK3mjpLpmaTojIPlm7TvvJolcycgJSAn44CBJy5qb2luKGhpdF9leGNsdWRl'
    'Wzo1XSkKCiAgICAjIOS+nei1luaciemZkOaJqeWxle+8muS4jiByZWxldmFudCDlkIzmqKHlnZfmiJblkIzmlofku7bvvIzm'
    'r4/kuKogcmVsZXZhbnQg5pyA5aSaIERFUEVOREVOVF9QRVJfSElUIOadoQogICAgZm9yIGZhY3RfaWQgaW4gcmVsZXZhbnQ6'
    'CiAgICAgICAgbW9kdWxlID0gc3RyKGNvbnRleHRzW2ZhY3RfaWRdLmdldCgnbW9kdWxlJykgb3IgJycpCiAgICAgICAgbG9j'
    'YXRvciA9IGNvbnRleHRzW2ZhY3RfaWRdLmdldCgnbG9jYXRvcicpCiAgICAgICAgZmlsZV9uYW1lID0gc3RyKChsb2NhdG9y'
    'IG9yIHt9KS5nZXQoJ2ZpbGUnKSBvciAnJykgaWYgaXNpbnN0YW5jZShsb2NhdG9yLCBkaWN0KSBlbHNlICcnCiAgICAgICAg'
    'bmVpZ2hib3Vycywgc2VlbiA9IFtdLCBzZXQoKQogICAgICAgIGZvciBuZWlnaGJvdXIgaW4gKGJ5X21vZHVsZS5nZXQobW9k'
    'dWxlKSBvciBbXSkgKyAoYnlfZmlsZS5nZXQoZmlsZV9uYW1lKSBvciBbXSk6CiAgICAgICAgICAgIGlmIG5laWdoYm91ciA9'
    'PSBmYWN0X2lkIG9yIG5laWdoYm91ciBpbiBzZWVuOgogICAgICAgICAgICAgICAgY29udGludWUKICAgICAgICAgICAgc2Vl'
    'bi5hZGQobmVpZ2hib3VyKQogICAgICAgICAgICBpZiBjbGFpbWVkLmdldChuZWlnaGJvdXIpID09ICdleGNsdWRlZCc6CiAg'
    'ICAgICAgICAgICAgICBjb250aW51ZSAgIyDmjpLpmaTpobnnu53kuI3lm6Dkvp3otZbmianlsZXlpI3mtLsKICAgICAgICAg'
    'ICAgbmVpZ2hib3Vycy5hcHBlbmQobmVpZ2hib3VyKQogICAgICAgIHRha2VuID0gMAogICAgICAgIGZvciBuZWlnaGJvdXIg'
    'aW4gbmVpZ2hib3VyczoKICAgICAgICAgICAgaWYgdGFrZW4gPj0gREVQRU5ERU5UX1BFUl9ISVQ6CiAgICAgICAgICAgICAg'
    'ICBicmVhawogICAgICAgICAgICBpZiBuZWlnaGJvdXIgaW4gY2xhaW1lZDoKICAgICAgICAgICAgICAgIGNvbnRpbnVlCiAg'
    'ICAgICAgICAgIHJlbGF0ZWQuYXBwZW5kKG5laWdoYm91cikKICAgICAgICAgICAgY2xhaW1lZFtuZWlnaGJvdXJdID0gJ3Jl'
    'bGF0ZWQnCiAgICAgICAgICAgIHNhbWVfZmlsZSA9IGJvb2woZmlsZV9uYW1lKSBhbmQgbmVpZ2hib3VyIGluIChieV9maWxl'
    'LmdldChmaWxlX25hbWUpIG9yIFtdKQogICAgICAgICAgICByZWFzb25zW25laWdoYm91cl0gPSAoJ+S4jue6s+WFpeS6i+Wu'
    'niAlcyDlkIzkuIAlc++8iOS+nei1luaJqeWxle+8jOavj+S4que6s+WFpemhueacgOWkmiAlZCDmnaHvvIknCiAgICAgICAg'
    'ICAgICAgICAgICAgICAgICAgICAgICAgICAlIChmYWN0X2lkLCAn5qih5Z2XJyBpZiBub3Qgc2FtZV9maWxlIGVsc2UgJ+aW'
    'h+S7ticsCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICBERVBFTkRFTlRfUEVSX0hJVCkpCiAgICAgICAg'
    'ICAgIHRha2VuICs9IDEKCiAgICAjIOWFtuS9meS6i+Wunu+8muS/neWuiOS/neeVme+8iOS4jeS4ouadkOaWme+8ie+8jOeQ'
    'hueUseWMuuWIhuaYr+WQpuS4juebruaghy/lhbPogZTmj4/ov7DlvLHnm7jlhbMKICAgIGZvciBmYWN0X2lkIGluIG9yZGVy'
    'OgogICAgICAgIGlmIGZhY3RfaWQgaW4gY2xhaW1lZDoKICAgICAgICAgICAgY29udGludWUKICAgICAgICB0ZXh0ID0gdGV4'
    'dHMuZ2V0KGZhY3RfaWQsICcnKQogICAgICAgIHdlYWtfaGl0cyA9IF9oaXRzKGluY2x1ZGVfd2VhaywgdGV4dCkKICAgICAg'
    'ICBoaW50X2hpdHMgPSBfaGl0cyhoaW50cywgdGV4dCkKICAgICAgICBpZiB3ZWFrX2hpdHM6CiAgICAgICAgICAgIHJlYXNv'
    'bnNbZmFjdF9pZF0gPSAoJ+iMg+WbtOaPj+i/sOeahOeJh+auteWRveS4re+8iOS/neWuiOS/neeVme+8jOW+heS6uuW3peeh'
    'ruiupO+8ie+8miVzJwogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICUgJ+OAgScuam9pbih3ZWFrX2hpdHNbOjVd'
    'KSkKICAgICAgICBlbGlmIGhpbnRfaGl0czoKICAgICAgICAgICAgcmVhc29uc1tmYWN0X2lkXSA9ICgn5ZG95Lit5bu65qih'
    '55uu5qCHL+WFs+iBlOivtOaYjuWFs+mUruivje+8iOS/neWuiOS/neeVme+8ie+8miVzJwogICAgICAgICAgICAgICAgICAg'
    'ICAgICAgICAgICAgICUgJ+OAgScuam9pbihoaW50X2hpdHNbOjVdKSkKICAgICAgICBlbHNlOgogICAgICAgICAgICByZWFz'
    'b25zW2ZhY3RfaWRdID0gJ+acquWRveS4reiMg+WbtOWFs+mUruivje+8jOS/neWuiOS/neeVme+8iOS4jeS4ouadkOaWme+8'
    'iScKICAgICAgICByZWxhdGVkLmFwcGVuZChmYWN0X2lkKQogICAgICAgIGNsYWltZWRbZmFjdF9pZF0gPSAncmVsYXRlZCcK'
    'CiAgICByZXR1cm4geydyZWxldmFudCc6IHJlbGV2YW50LCAncmVsYXRlZCc6IHJlbGF0ZWQsICdleGNsdWRlZCc6IGV4Y2x1'
    'ZGVkLCAncmVhc29ucyc6IHJlYXNvbnMsCiAgICAgICAgICAgICdjb3VudHMnOiB7J3JlbGV2YW50JzogbGVuKHJlbGV2YW50'
    'KSwgJ3JlbGF0ZWQnOiBsZW4ocmVsYXRlZCksCiAgICAgICAgICAgICAgICAgICAgICAgJ2V4Y2x1ZGVkJzogbGVuKGV4Y2x1'
    'ZGVkKSwgJ3RvdGFsJzogbGVuKG9yZGVyKX19CgoKZGVmIHNlbGVjdF9zY29wZV9pZHMoc2VsZWN0aW9uLCBidWNrZXQ9J3Jl'
    'bGV2YW50Jyk6CiAgICAiIiLkvr/mjbfor7vlj5bvvIjkvpvnrqHnur/mjInluo/lj5bkuovlrp4gSUTvvJvnvLrnnIHov5Tl'
    'm57nqbrliJfooajvvInjgIIiIiIKICAgIGlmIG5vdCBpc2luc3RhbmNlKHNlbGVjdGlvbiwgZGljdCk6CiAgICAgICAgcmV0'
    'dXJuIFtdCiAgICB2YWx1ZSA9IHNlbGVjdGlvbi5nZXQoYnVja2V0KQogICAgcmV0dXJuIFtzdHIoaXRlbSkgZm9yIGl0ZW0g'
    'aW4gdmFsdWVdIGlmIGlzaW5zdGFuY2UodmFsdWUsIGxpc3QpIGVsc2UgW10KCgpkZWYgc25pcHBldF9kaWdlc3QoZmFjdCk6'
    'CiAgICAiIiLkvpsgYWxpZ25tZW50IC8g5aSN5qC45YWx55So55qE54mH5q615ZOI5biM77yI5ZCM5LiA5a6e546w55qE5ZSv'
    '5LiA5YWl5Y+j77yJ44CCIiIiCiAgICByZXR1cm4gX3NuaXBwZXRfaGFzaCgoZmFjdCBvciB7fSkuZ2V0KCdzbmlwcGV0Jykp'
    'Cg=='
)

_reference_ns = {'__name__': 'reference_retrieval'}
exec(compile(base64.b64decode(_REFERENCE_SOURCE_B64).decode('utf-8'),
             'reference_retrieval_snapshot', 'exec'), _reference_ns)

reference_build_index = _reference_ns['build_index']
reference_select_scope = _reference_ns['select_scope']

PASSED = []
FAILED = []
SEQ = [0]


def check(cond, message, actual=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    if actual is not None:
        print('  实际:', actual)
    return False


def _bytes(obj):
    """selection 的逐字节序列化（键序归一，内容级等价的忠实比较）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def assert_equivalent(facts, scope, label):
    old_index = reference_build_index([dict(f) for f in facts])
    new_index = current_retrieval.build_index([dict(f) for f in facts])
    old_sel = reference_select_scope([dict(f) for f in facts], dict(scope), old_index)
    new_sel = current_retrieval.select_scope([dict(f) for f in facts], dict(scope), new_index)
    old_text, new_text = _bytes(old_sel), _bytes(new_sel)
    if old_text == new_text:
        check(True, '等价金样 %s：selection 逐字节一致（relevant=%d related=%d excluded=%d）'
              % (label, old_sel['counts']['relevant'], old_sel['counts']['related'],
                 old_sel['counts']['excluded']))
    else:
        # 定位第一个分歧（仅诊断用）
        old_obj, new_obj = json.loads(old_text), json.loads(new_text)
        diff_key = None
        for key in ('relevant', 'related', 'excluded', 'counts', 'reasons'):
            if _bytes(old_obj.get(key)) != _bytes(new_obj.get(key)):
                diff_key = key
                break
        check(False, '等价金样 %s：selection 逐字节一致' % label,
              actual={'diffKey': diff_key,
                      'old': old_text[:300], 'new': new_text[:300]})


# --- 手构造小样 ---------------------------------------------------------------------

def handcrafted_facts():
    facts = []
    counter = [0]

    def add(snippet, module='device', file='src/device/T1.md', data=None, kind='seg'):
        counter[0] += 1
        facts.append({
            'id': 'bf-t%04d' % counter[0],
            'taskId': 'task-eq',
            'materialId': 'bm-t%03d' % (counter[0] // 5),
            'module': module,
            'locator': {'kind': 'md', 'file': file, 'section': 'S%d' % counter[0],
                        'line': counter[0]},
            'snippet': snippet,
            'kind': kind,
            'data': data if data is not None else {},
            'quality': ('high', 'medium', 'low')[counter[0] % 3],
        })

    add('储能电站设备台账：额定容量 500kWh，含电池簇从属关系与告警处置流程。')
    add('收益结算与财务分成口径说明：本期不做，仅保留只读接口。')
    add('储能电站告警处置流程：收益结算字段在报表中只读展示。')
    add('泵站机组的振动数据按巡检周期归档，与储能业务无关。')
    add('储能系统的监测数据按簇归档。')
    add('计量与结算的数据边界说明，园区与电表只保留必要关联。')
    add('capacity of the station is 500kWh, soc sampled every 5s.')
    add('pcs 模块的功率变换逻辑（小写测试 casefold）。')
    add('')
    add('   ')
    add('重复片段：设备台账字段说明。', module='meter', file='src/meter/T9.md')
    add('重复片段：设备台账字段说明。', module='meter', file='src/meter/T9.md')
    add('预算(2024)年 C++ 与 a.b 的 100% 数据。')
    add('正则元字符词条命中：预算(2024)年 的专项说明。')
    add('C++ 接口与 a.b 配置样例。')
    add('园区级储能巡检说明。', module='cluster', file='src/cluster/C1.md')
    add('簇内电芯温度说明。', module='cluster', file='src/cluster/C1.md')
    add('孤立事实：与任何词条无关的说明文本。', module='orphan', file='src/orphan/O1.md')
    return facts


SCOPES = {
    'S1 全量范围': {
        'goal': '梳理储能电站运行管理的核心业务模型，覆盖设备台账与监测数据，理解计量与结算的数据边界',
        'include': '储能电站、电池簇、PCS 功率变换、BMS 电池管理、告警处置、设备台账',
        'exclude': '收益结算、财务分成',
        'relations': '园区与电表只保留必要关联，聚焦设备与簇的组成关系',
    },
    'S2 仅 include': {'goal': '', 'include': '储能电站、告警处置', 'exclude': '', 'relations': ''},
    'S3 仅 exclude': {'goal': '', 'include': '', 'exclude': '收益结算', 'relations': ''},
    'S4 仅 goal/relations（弱词与 hints）': {
        'goal': '理解计量与结算的数据边界', 'include': '', 'exclude': '',
        'relations': '园区与电表只保留必要关联'},
    'S5 全空': {'goal': '', 'include': '', 'exclude': '', 'relations': ''},
    'S6 正则元字符词条': {
        'goal': '', 'include': '预算(2024)年、C++、a.b', 'exclude': '100%', 'relations': ''},
    'S7 单词条与超长词': {
        'goal': '', 'include': '储能电站',
        'exclude': '收益结算与财务分成规则说明这条超长词整段保留用于验证超过十二字的整段仍参与强词匹配',
        'relations': ''},
    'S8 重复词条与大小写': {
        'goal': '储能储能', 'include': '储能电站、储能电站、PCS', 'exclude': 'pcs',
        'relations': ''},
}


def _generic_sentences():
    return (
        '泵站机组的振动数据按巡检周期归档，口径以现场作业指导书为准，跨班组交接存在分钟级延迟，',
        '风机叶片在低风速工况下的效率曲线出现抖动，需要结合历史工单记录复核参数设定的合理性，',
        '光伏组件的清扫周期与辐照采样窗口并不严格对齐，平台侧的存储批次按站点分别维护，',
        '供热管网的补水流程分为申请、审批与执行三个阶段，执行依赖现场回单的结构化录入，',
        '冷链仓储的温区划分在月度盘点与日常巡检之间存在差异，历史版本的台账仍在部分仓库运行，',
        '交通信号的配时方案经由区域控制器下发，链路抖动会造成短暂的状态缺口，重传策略按路口配置，',
        '水务调度的调度令与执行回执在多个子系统间各自留存，统一目录的改造尚在规划阶段，',
        '票据流转的审核规则由财务共享中心独立维护，本期数据接口只做只读对接，',
    )


def _keyword_sentences():
    return (
        '电池簇 {n} 的额定容量与实时 SOC 采样写入监测表，簇内电芯温度越限触发告警处置流程，',
        'PCS 功率变换系统 {n} 的有功功率设定与运行模式由能量管理平台下发，告警联动需要台账关联，',
        '储能电站 {n} 的设备台账记录电池簇与 PCS 的从属关系，监测数据按设备维度归档，',
        'BMS 电池管理 {n} 的告警处置记录包含确认人与复归时间，台账字段与运行报表保持一致，',
    )


def synthetic_facts(count=8000, seed=20260922):
    rng = random.Random(seed)
    generic = _generic_sentences()
    keywords = _keyword_sentences()
    modules = ('device', 'cluster', 'pcs', 'bms', 'alarm', 'meter', 'order', 'auth')
    kinds = ('code', 'ddl', 'docx', 'pdf', 'xlsx', 'md', 'image')
    tokens = ('capacity', 'soc', 'voltage', 'temperature', 'alarmCode', 'stationId')
    facts = []
    for index in range(count):
        parts, total = [], 0
        target = rng.randint(200, 500)
        keyword = None
        roll = rng.random()
        if roll < 0.02:
            keyword = keywords[0]
        elif roll < 0.04:
            keyword = keywords[1 + index % 3]
        if keyword is not None:
            parts.append(keyword.format(n=index % 97))
            total += len(parts[0])
        while total < target:
            if rng.random() < 0.18:
                piece = '%s=%d；' % (rng.choice(tokens), rng.randint(0, 99999))
            else:
                piece = rng.choice(generic)
            parts.append(piece)
            total += len(piece)
        facts.append({
            'id': 'bf-s%08d' % index,
            'taskId': 'task-eq-syn',
            'materialId': 'bm-s%06d' % (index // 150),
            'module': modules[index % len(modules)],
            'locator': {'kind': kinds[index % len(kinds)],
                        'file': 'src/pkg%02d/Module%03d.%s' % (index % 40, index % 250,
                                                               kinds[index % len(kinds)]),
                        'line': index % 4000 + 1,
                        'symbol': 'sym_%d' % (index % 1000)},
            'snippet': ''.join(parts),
            'kind': 'segment%d' % (index % 6),
            'data': ({'page': index % 900, 'keywords': [rng.choice(tokens)]}
                     if index % 7 else {}),
            'quality': ('high', 'medium', 'low')[index % 3],
        })
    return facts


def main():
    facts = handcrafted_facts()
    for label, scope in SCOPES.items():
        assert_equivalent(facts, scope, label)

    # O0：默认不构建 tokens 桶（死工作消除，R4）；显式 with_tokens=True 仍可构建
    sample = facts[:6]
    new_index = current_retrieval.build_index([dict(f) for f in sample])
    check(new_index.get('tokens') == {},
          'O0：默认（with_tokens=False）不再构建 tokens 倒排桶（R4）',
          actual=(new_index.get('tokens'),))
    keyed = current_retrieval.build_index([dict(f) for f in sample], with_tokens=True)
    check(bool(keyed.get('tokens')),
          'O0：显式 with_tokens=True 仍构建 tokens 桶（参数化保留）',
          actual=bool(keyed.get('tokens')))
    ref_index = reference_build_index([dict(f) for f in sample])
    shared_keys = ('byModule', 'byFile', 'byKind', 'order', 'snippetHash', 'material')
    mismatch = [key for key in shared_keys
                if list(new_index.get(key) or []) != list(ref_index.get(key) or [])]
    check(not mismatch, '桶结构（byModule/byFile/byKind/order/snippetHash/material）与基线一致',
          actual=mismatch)
    text_mismatch = [f['id'] for f in sample
                     if new_index['text'][f['id']] != ref_index['text'][f['id']]]
    check(not text_mismatch,
          'searchable_text 文本与基线逐字一致（texts 由 build_index/select_scope 共享，无重复构建）',
          actual=text_mismatch)

    synthetic = synthetic_facts(8000)
    for label in ('S1 全量范围', 'S4 仅 goal/relations（弱词与 hints）', 'S5 全空'):
        assert_equivalent(synthetic, SCOPES[label], '合成集 ' + label)

    print('')
    print('========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), len(PASSED) + len(FAILED)))
    for name in FAILED:
        print('  失败: ' + name)
    return 0 if not FAILED else 1


if __name__ == '__main__':
    sys.exit(main())
