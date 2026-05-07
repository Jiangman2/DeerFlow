import subprocess
from pathlib import Path
from datetime import datetime
import textwrap

venv_py = '/app/backend/.venv/bin/python'
scripts = '/app/skills/public/ppt-master/scripts'
layout_dir = Path('/app/skills/public/ppt-master/templates/layouts/haiyou_pure_svg')
project_name = f"data_lake_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def run(cmd):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.stdout:
        print(p.stdout)
    if p.stderr:
        print(p.stderr)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


cmd = [venv_py, f"{scripts}/project_manager.py", "init", project_name, "--format", "ppt169", "--dir", "/app/projects"]
p = subprocess.run(cmd, text=True, capture_output=True)
print(p.stdout)
if p.stderr:
    print(p.stderr)
if p.returncode != 0:
    raise SystemExit(p.returncode)

project_dir = None
for line in p.stdout.splitlines():
    if line.startswith("Project created: "):
        project_dir = line.split("Project created: ", 1)[1].strip()
        break
if not project_dir:
    raise RuntimeError("Cannot parse project path")

project = Path(project_dir)
svg_output = project / 'svg_output'
notes_dir = project / 'notes'

assets = [
    'cover_bg.png',
    'cnooc_logo_white.png',
    'chapter_bg_user.png',
    'chapter_bg_155057_clean.png',
    'content_bg_user.png',
    'ending_bg_user.png',
]
for a in assets:
    src = layout_dir / a
    dst = svg_output / a
    if src.exists():
        dst.write_bytes(src.read_bytes())


def esc(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def bullets(x, y, lines, line_h=40, fs=22, color="#1D4E79"):
    out = []
    yy = y
    for ln in lines:
        out.append(f'<circle cx="{x}" cy="{yy-8}" r="5" fill="#0074CC"/>')
        out.append(f'<text x="{x+18}" y="{yy}" fill="{color}" font-size="{fs}" font-family="Microsoft YaHei">{esc(ln)}</text>')
        yy += line_h
    return "\n  ".join(out)

pages = []

# 1 封面
pages.append(f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="cover_bg.png" xlink:href="cover_bg.png"/>
  <image x="52" y="26" width="146" height="44" href="cnooc_logo_white.png" xlink:href="cnooc_logo_white.png"/>
  <rect x="62" y="312" width="1148" height="275" rx="16" fill="#003E73" fill-opacity="0.38"/>
  <text x="86" y="386" fill="#FFFFFF" font-size="56" font-family="Microsoft YaHei" font-weight="700">数据湖建设进展与数智化提升汇报</text>
  <text x="86" y="442" fill="#EAF2F9" font-size="34" font-family="Microsoft YaHei" font-weight="700">围绕汇、通、管、服、智推进数据价值释放</text>
  <text x="86" y="500" fill="#DDEAF6" font-size="28" font-family="Microsoft YaHei">汇报单位：中海油研究总院数智研究院</text>
  <text x="86" y="540" fill="#DDEAF6" font-size="28" font-family="Microsoft YaHei">汇报日期：{datetime.now().strftime('%Y-%m-%d')}</text>
</svg>''')

# 2 目录
pages.append('''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="chapter_bg_user.png" xlink:href="chapter_bg_user.png"/>
  <rect x="70" y="80" width="1140" height="560" rx="18" fill="#FFFFFF" fill-opacity="0.86"/>
  <text x="120" y="160" fill="#004C8B" font-size="56" font-family="Microsoft YaHei" font-weight="700">目录</text>
  <line x1="120" y1="178" x2="1160" y2="178" stroke="#2C7BC0" stroke-width="3"/>
  <text x="130" y="250" fill="#0F3E68" font-size="34" font-family="Microsoft YaHei" font-weight="700">01 基本情况</text>
  <text x="130" y="315" fill="#0F3E68" font-size="34" font-family="Microsoft YaHei" font-weight="700">02 进展和成果（技术创新、数据实施、管理提升）</text>
  <text x="130" y="380" fill="#0F3E68" font-size="34" font-family="Microsoft YaHei" font-weight="700">03 下一步工作计划</text>
  <text x="130" y="445" fill="#0F3E68" font-size="34" font-family="Microsoft YaHei" font-weight="700">04 思考和建议</text>
  <rect x="130" y="500" width="1030" height="95" rx="12" fill="#EAF4FD"/>
  <text x="155" y="548" fill="#1D4E79" font-size="24" font-family="Microsoft YaHei">核心主线：问题诊断 -> 体系建设 -> 成果验证 -> 行动计划 -> 数智愿景</text>
</svg>''')

# 3 章节1
pages.append('''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="chapter_bg_155057_clean.png" xlink:href="chapter_bg_155057_clean.png"/>
  <rect x="180" y="190" width="920" height="360" rx="18" fill="#FFFFFF" fill-opacity="0.78"/>
  <text x="640" y="315" text-anchor="middle" fill="#004C8B" font-size="150" font-family="Arial" font-weight="700">01</text>
  <text x="640" y="435" text-anchor="middle" fill="#004C8B" font-size="76" font-family="Microsoft YaHei" font-weight="700">基本情况</text>
</svg>''')

# 4 主要问题与原因（含食品加工示意）
pages.append(f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="content_bg_user.png" xlink:href="content_bg_user.png"/>
  <text x="42" y="72" fill="#004C8B" font-size="36" font-family="Microsoft YaHei" font-weight="700">数据湖主要问题与成因分析</text>
  <rect x="40" y="100" width="760" height="560" rx="14" fill="#FFFFFF" fill-opacity="0.9"/>
  <rect x="825" y="100" width="415" height="560" rx="14" fill="#F3F8FD"/>
  {bullets(78, 160, [
      '主要问题：数据不全、查询不便捷、价值未充分发挥',
      '治理问题：范围覆盖不足，面向应用与模型的数据不完整',
      '工具问题：查询方式不友好，分析门槛高，数据智能能力弱',
      '管理问题：数据意识与能力薄弱，价值应用牵引不足',
      '总体判断：问题并非单点技术缺陷，而是体系化短板'
  ], line_h=44)}
  <text x="850" y="148" fill="#0F3E68" font-size="28" font-family="Microsoft YaHei" font-weight="700">食品加工场景示意（类比）</text>
  <rect x="850" y="180" width="360" height="420" rx="12" fill="#FFFFFF"/>
  <rect x="880" y="230" width="90" height="62" rx="8" fill="#BFD8EF"/>
  <text x="892" y="270" fill="#1D4E79" font-size="20" font-family="Microsoft YaHei">原料</text>
  <line x1="970" y1="262" x2="1045" y2="262" stroke="#2C7BC0" stroke-width="4"/>
  <polygon points="1045,262 1035,256 1035,268" fill="#2C7BC0"/>
  <rect x="1050" y="230" width="120" height="62" rx="8" fill="#93C5FD"/>
  <text x="1070" y="270" fill="#0F3E68" font-size="20" font-family="Microsoft YaHei">加工线</text>
  <line x1="1108" y1="292" x2="1108" y2="348" stroke="#DE002B" stroke-width="4"/>
  <polygon points="1108,348 1102,338 1114,338" fill="#DE002B"/>
  <rect x="1030" y="355" width="160" height="70" rx="10" fill="#DBEAFE"/>
  <text x="1058" y="397" fill="#0F3E68" font-size="22" font-family="Microsoft YaHei">质检与反馈</text>
  <text x="868" y="472" fill="#1D4E79" font-size="20" font-family="Microsoft YaHei">对应数据湖：采集 -> 治理 -> 服务 -> 反馈闭环</text>
</svg>''')

# 5 用户需求分析
pages.append(f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="content_bg_user.png" xlink:href="content_bg_user.png"/>
  <text x="42" y="72" fill="#004C8B" font-size="36" font-family="Microsoft YaHei" font-weight="700">用户需求分析（参考调研结论）</text>
  <rect x="40" y="100" width="1200" height="560" rx="14" fill="#FFFFFF" fill-opacity="0.9"/>
  <rect x="72" y="150" width="360" height="450" rx="12" fill="#EFF6FC"/>
  <rect x="460" y="150" width="360" height="450" rx="12" fill="#EFF6FC"/>
  <rect x="848" y="150" width="360" height="450" rx="12" fill="#EFF6FC"/>
  <text x="96" y="202" fill="#0F3E68" font-size="30" font-family="Microsoft YaHei" font-weight="700">业务侧诉求</text>
  <text x="96" y="248" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 一站式查数与看数</text>
  <text x="96" y="286" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 结论可追溯、可解释</text>
  <text x="96" y="324" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 专题汇报快速成稿</text>
  <text x="484" y="202" fill="#0F3E68" font-size="30" font-family="Microsoft YaHei" font-weight="700">管理侧诉求</text>
  <text x="484" y="248" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 指标口径统一可控</text>
  <text x="484" y="286" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 质量问题快速闭环</text>
  <text x="484" y="324" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 价值应用可量化评估</text>
  <text x="872" y="202" fill="#0F3E68" font-size="30" font-family="Microsoft YaHei" font-weight="700">技术侧诉求</text>
  <text x="872" y="248" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 面向AI的数据就绪</text>
  <text x="872" y="286" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 结构化语义化改造</text>
  <text x="872" y="324" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 非结构化知识化加工</text>
  <rect x="96" y="430" width="1080" height="130" rx="12" fill="#FFFFFF"/>
  <text x="126" y="483" fill="#0F3E68" font-size="24" font-family="Microsoft YaHei">用户共性需求：低门槛、快响应、强可信、可复用；核心目标是让数据可见、可懂、可用、可智。</text>
</svg>''')

# 6 主要思路：汇通管服智（思路图）
pages.append('''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="content_bg_user.png" xlink:href="content_bg_user.png"/>
  <text x="42" y="72" fill="#004C8B" font-size="36" font-family="Microsoft YaHei" font-weight="700">总体思路：汇、通、管、服、智（体系化提升）</text>
  <rect x="40" y="100" width="1200" height="560" rx="14" fill="#FFFFFF" fill-opacity="0.9"/>
  <circle cx="180" cy="360" r="75" fill="#DBEAFE"/>
  <circle cx="390" cy="360" r="75" fill="#BFDBFE"/>
  <circle cx="600" cy="360" r="75" fill="#93C5FD"/>
  <circle cx="810" cy="360" r="75" fill="#60A5FA"/>
  <circle cx="1020" cy="360" r="75" fill="#3B82F6"/>
  <text x="165" y="370" fill="#0F3E68" font-size="44" font-family="Microsoft YaHei" font-weight="700">汇</text>
  <text x="375" y="370" fill="#0F3E68" font-size="44" font-family="Microsoft YaHei" font-weight="700">通</text>
  <text x="585" y="370" fill="#FFFFFF" font-size="44" font-family="Microsoft YaHei" font-weight="700">管</text>
  <text x="795" y="370" fill="#FFFFFF" font-size="44" font-family="Microsoft YaHei" font-weight="700">服</text>
  <text x="1005" y="370" fill="#FFFFFF" font-size="44" font-family="Microsoft YaHei" font-weight="700">智</text>
  <line x1="255" y1="360" x2="315" y2="360" stroke="#2C7BC0" stroke-width="5"/>
  <line x1="465" y1="360" x2="525" y2="360" stroke="#2C7BC0" stroke-width="5"/>
  <line x1="675" y1="360" x2="735" y2="360" stroke="#2C7BC0" stroke-width="5"/>
  <line x1="885" y1="360" x2="945" y2="360" stroke="#2C7BC0" stroke-width="5"/>
  <text x="115" y="500" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">汇：全量多源汇聚</text>
  <text x="325" y="500" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">通：数据资产贯通</text>
  <text x="535" y="500" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">管：治理与质量闭环</text>
  <text x="745" y="500" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">服：查询分析搜索服务</text>
  <text x="955" y="500" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">智：问数与智能研究</text>
  <text x="86" y="590" fill="#0F3E68" font-size="24" font-family="Microsoft YaHei">说明：该路径对标先进技术平台，覆盖数智治理、技术创新、工具开发与用户培训，不是单一技术优化。</text>
</svg>''')

# 7 当前整体进展
pages.append(f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="content_bg_user.png" xlink:href="content_bg_user.png"/>
  <text x="42" y="72" fill="#004C8B" font-size="36" font-family="Microsoft YaHei" font-weight="700">当前整体进展</text>
  <rect x="40" y="100" width="1200" height="560" rx="14" fill="#FFFFFF" fill-opacity="0.9"/>
  {bullets(80, 170, [
      '统建采集系统已覆盖全量、全类型数据接入框架',
      '数据贯通能力形成：业务目录挂接结构化/非结构化/实时资产',
      '端到端数据质量反馈机制已上线并持续优化',
      '业务查询、数据分析、搜索能力形成统一入口',
      '数据智能能力落地：问数问答、智能分析、智能研究、智能PPT'
  ], line_h=56, fs=24)}
  <rect x="780" y="150" width="420" height="470" rx="12" fill="#F3F8FD"/>
  <text x="810" y="205" fill="#0F3E68" font-size="28" font-family="Microsoft YaHei" font-weight="700">阶段状态</text>
  <text x="810" y="255" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">采集接入：完成主体建设</text>
  <text x="810" y="295" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">资产挂接：持续扩展</text>
  <text x="810" y="335" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">质量反馈：闭环运行</text>
  <text x="810" y="375" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">查询分析：稳定可用</text>
  <text x="810" y="415" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">数据智能：场景试点</text>
</svg>''')

# 8 章节2
pages.append('''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="chapter_bg_155057_clean.png" xlink:href="chapter_bg_155057_clean.png"/>
  <rect x="180" y="190" width="920" height="360" rx="18" fill="#FFFFFF" fill-opacity="0.78"/>
  <text x="640" y="315" text-anchor="middle" fill="#004C8B" font-size="150" font-family="Arial" font-weight="700">02</text>
  <text x="640" y="435" text-anchor="middle" fill="#004C8B" font-size="60" font-family="Microsoft YaHei" font-weight="700">进展和成果</text>
</svg>''')

# 9 接入与贯通
pages.append(f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="content_bg_user.png" xlink:href="content_bg_user.png"/>
  <text x="42" y="72" fill="#004C8B" font-size="36" font-family="Microsoft YaHei" font-weight="700">成果一：全量接入与数据贯通</text>
  <rect x="40" y="100" width="580" height="560" rx="14" fill="#FFFFFF" fill-opacity="0.9"/>
  <rect x="650" y="100" width="590" height="560" rx="14" fill="#FFFFFF" fill-opacity="0.9"/>
  <text x="70" y="150" fill="#0F3E68" font-size="28" font-family="Microsoft YaHei" font-weight="700">全量、全类型接入</text>
  {bullets(78, 200, [
      '统一采集体系覆盖核心源头系统',
      '结构化、非结构化、实时数据并行接入',
      '接入流程标准化，提升可复制性'
  ], line_h=46)}
  <text x="680" y="150" fill="#0F3E68" font-size="28" font-family="Microsoft YaHei" font-weight="700">业务目录挂接与贯通</text>
  {bullets(688, 200, [
      '按业务主题组织数据资产目录',
      '资产可见、可懂、可追溯',
      '跨域数据关联支持端到端分析'
  ], line_h=46)}
  <rect x="80" y="380" width="1120" height="210" rx="12" fill="#F3F8FD"/>
  <text x="110" y="440" fill="#1D4E79" font-size="24" font-family="Microsoft YaHei">价值体现：数据可接入升级为可贯通，为质量闭环与智能应用奠定基础。</text>
</svg>''')

# 10 质量提升
pages.append(f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="content_bg_user.png" xlink:href="content_bg_user.png"/>
  <text x="42" y="72" fill="#004C8B" font-size="36" font-family="Microsoft YaHei" font-weight="700">成果二：数据质量提升与端到端反馈</text>
  <rect x="40" y="100" width="1200" height="560" rx="14" fill="#FFFFFF" fill-opacity="0.9"/>
  <rect x="80" y="160" width="250" height="110" rx="10" fill="#DBEAFE"/>
  <rect x="380" y="160" width="250" height="110" rx="10" fill="#BFDBFE"/>
  <rect x="680" y="160" width="250" height="110" rx="10" fill="#93C5FD"/>
  <rect x="980" y="160" width="220" height="110" rx="10" fill="#60A5FA"/>
  <text x="130" y="225" fill="#0F3E68" font-size="24" font-family="Microsoft YaHei" font-weight="700">规则探查</text>
  <text x="430" y="225" fill="#0F3E68" font-size="24" font-family="Microsoft YaHei" font-weight="700">问题识别</text>
  <text x="730" y="225" fill="#FFFFFF" font-size="24" font-family="Microsoft YaHei" font-weight="700">责任反馈</text>
  <text x="1020" y="225" fill="#FFFFFF" font-size="24" font-family="Microsoft YaHei" font-weight="700">闭环复核</text>
  <line x1="330" y1="215" x2="380" y2="215" stroke="#2C7BC0" stroke-width="4"/>
  <line x1="630" y1="215" x2="680" y2="215" stroke="#2C7BC0" stroke-width="4"/>
  <line x1="930" y1="215" x2="980" y2="215" stroke="#2C7BC0" stroke-width="4"/>
  {bullets(86, 350, [
      '基于业务视角定义完整性与一致性规则',
      '构建端到端质量反馈链路，问题可追踪',
      '推动分公司敏捷响应，缩短问题处置周期',
      '质量治理从专项治理转向持续运营'
  ], line_h=48, fs=24)}
</svg>''')

# 11 查询分析与数据智能
pages.append(f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="content_bg_user.png" xlink:href="content_bg_user.png"/>
  <text x="42" y="72" fill="#004C8B" font-size="36" font-family="Microsoft YaHei" font-weight="700">成果三：业务查询、数据分析与数据智能</text>
  <rect x="40" y="100" width="1200" height="560" rx="14" fill="#FFFFFF" fill-opacity="0.9"/>
  <rect x="72" y="150" width="356" height="450" rx="12" fill="#EFF6FC"/>
  <rect x="462" y="150" width="356" height="450" rx="12" fill="#EFF6FC"/>
  <rect x="852" y="150" width="356" height="450" rx="12" fill="#EFF6FC"/>
  <text x="96" y="202" fill="#0F3E68" font-size="30" font-family="Microsoft YaHei" font-weight="700">业务查询与搜索</text>
  <text x="96" y="248" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 指标查询</text>
  <text x="96" y="286" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 资料检索</text>
  <text x="96" y="324" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 跨主题联查</text>
  <text x="486" y="202" fill="#0F3E68" font-size="30" font-family="Microsoft YaHei" font-weight="700">数据分析服务</text>
  <text x="486" y="248" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 统计分析与诊断</text>
  <text x="486" y="286" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 业务专题研判</text>
  <text x="486" y="324" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 可视化汇报支持</text>
  <text x="876" y="202" fill="#0F3E68" font-size="30" font-family="Microsoft YaHei" font-weight="700">数据智能能力</text>
  <text x="876" y="248" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 问数问答</text>
  <text x="876" y="286" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 智能分析/研究</text>
  <text x="876" y="324" fill="#1D4E79" font-size="22" font-family="Microsoft YaHei">- 智能PPT生成</text>
  <rect x="96" y="430" width="1080" height="130" rx="12" fill="#FFFFFF"/>
  <text x="126" y="483" fill="#0F3E68" font-size="24" font-family="Microsoft YaHei">技术支撑：结构化数据语义化 + 非结构化数据知识化，推动数据可用向数据可智跃迁。</text>
</svg>''')

# 12 章节3
pages.append('''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="chapter_bg_155057_clean.png" xlink:href="chapter_bg_155057_clean.png"/>
  <rect x="180" y="190" width="920" height="360" rx="18" fill="#FFFFFF" fill-opacity="0.78"/>
  <text x="640" y="315" text-anchor="middle" fill="#004C8B" font-size="150" font-family="Arial" font-weight="700">03</text>
  <text x="640" y="435" text-anchor="middle" fill="#004C8B" font-size="70" font-family="Microsoft YaHei" font-weight="700">下一步工作计划</text>
</svg>''')

# 13 下一步计划
pages.append(f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="content_bg_user.png" xlink:href="content_bg_user.png"/>
  <text x="42" y="72" fill="#004C8B" font-size="36" font-family="Microsoft YaHei" font-weight="700">问题聚焦与下一步行动计划</text>
  <rect x="40" y="100" width="1200" height="560" rx="14" fill="#FFFFFF" fill-opacity="0.9"/>
  <rect x="72" y="145" width="540" height="470" rx="12" fill="#EFF6FC"/>
  <rect x="668" y="145" width="540" height="470" rx="12" fill="#EFF6FC"/>
  <text x="96" y="195" fill="#0F3E68" font-size="30" font-family="Microsoft YaHei" font-weight="700">仍存在的问题</text>
  {bullets(102, 248, [
      '治理试点需持续推进，机制需固化',
      '数据缺失仍是最大制约因素',
      '价值应用责任牵引有待加强'
  ], line_h=50, fs=24)}
  <text x="692" y="195" fill="#0F3E68" font-size="30" font-family="Microsoft YaHei" font-weight="700">下一步重点任务</text>
  {bullets(698, 248, [
      '技术：7月底实现试点上线，推进AI自动分析与目录挂接',
      '技术：重复数据自动对齐、质量智能探查、知识智能提取',
      '管理：强化源头管控与分公司质量敏捷响应',
      '运营：开展培训推广与品牌打造，形成应用生态'
  ], line_h=46, fs=22)}
</svg>''')

# 14 章节4
pages.append('''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="chapter_bg_155057_clean.png" xlink:href="chapter_bg_155057_clean.png"/>
  <rect x="180" y="190" width="920" height="360" rx="18" fill="#FFFFFF" fill-opacity="0.78"/>
  <text x="640" y="315" text-anchor="middle" fill="#004C8B" font-size="150" font-family="Arial" font-weight="700">04</text>
  <text x="640" y="435" text-anchor="middle" fill="#004C8B" font-size="76" font-family="Microsoft YaHei" font-weight="700">思考和建议</text>
</svg>''')

# 15 愿景与建议（愿景图）
pages.append('''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="content_bg_user.png" xlink:href="content_bg_user.png"/>
  <text x="42" y="72" fill="#004C8B" font-size="36" font-family="Microsoft YaHei" font-weight="700">技术趋势愿景与管理建议</text>
  <rect x="40" y="100" width="1200" height="560" rx="14" fill="#FFFFFF" fill-opacity="0.9"/>
  <text x="74" y="150" fill="#0F3E68" font-size="28" font-family="Microsoft YaHei" font-weight="700">愿景图：数智一体、用智一体、人智一体</text>
  <rect x="90" y="190" width="320" height="160" rx="12" fill="#DBEAFE"/>
  <rect x="480" y="190" width="320" height="160" rx="12" fill="#93C5FD"/>
  <rect x="870" y="190" width="320" height="160" rx="12" fill="#3B82F6"/>
  <text x="170" y="275" fill="#0F3E68" font-size="34" font-family="Microsoft YaHei" font-weight="700">数智一体</text>
  <text x="560" y="275" fill="#FFFFFF" font-size="34" font-family="Microsoft YaHei" font-weight="700">用智一体</text>
  <text x="950" y="275" fill="#FFFFFF" font-size="34" font-family="Microsoft YaHei" font-weight="700">人智一体</text>
  <line x1="410" y1="270" x2="480" y2="270" stroke="#2C7BC0" stroke-width="5"/>
  <line x1="800" y1="270" x2="870" y2="270" stroke="#2C7BC0" stroke-width="5"/>
  <rect x="90" y="390" width="1100" height="220" rx="12" fill="#F3F8FD"/>
  <text x="120" y="438" fill="#0F3E68" font-size="24" font-family="Microsoft YaHei">建议1（治理）：从面向数据治理升级到面向数智资源治理</text>
  <text x="120" y="484" fill="#0F3E68" font-size="24" font-family="Microsoft YaHei">建议2（管理）：技术从体力向精英化、责任从组织向全员转变</text>
  <text x="120" y="530" fill="#0F3E68" font-size="24" font-family="Microsoft YaHei">建议3（实施）：实施从人力向AI转变，以更强统筹实现敏捷迭代</text>
  <text x="120" y="576" fill="#0F3E68" font-size="24" font-family="Microsoft YaHei">目标：形成数据、知识、工具、软件的智能协同生态，迈向数智海油</text>
</svg>''')

# 16 结尾
pages.append('''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1280 720">
  <image x="0" y="0" width="1280" height="720" href="ending_bg_user.png" xlink:href="ending_bg_user.png"/>
  <rect x="120" y="170" width="1040" height="340" rx="16" fill="#003E73" fill-opacity="0.40"/>
  <text x="640" y="290" text-anchor="middle" fill="#FFFFFF" font-size="64" font-family="Microsoft YaHei" font-weight="700">感谢聆听</text>
  <text x="640" y="360" text-anchor="middle" fill="#EAF2F9" font-size="32" font-family="Microsoft YaHei">请各位领导专家指导</text>
  <text x="640" y="430" text-anchor="middle" fill="#EAF2F9" font-size="28" font-family="Microsoft YaHei">中海油研究总院数智研究院技术团队</text>
</svg>''')

for i, svg in enumerate(pages, start=1):
    (svg_output / f"{i:02d}_page.svg").write_text(svg, encoding='utf-8')

notes = []
for i in range(1, len(pages) + 1):
    notes.append(textwrap.dedent(f"""\
    ## Page {i}
    - 讲解重点：围绕提纲核心结论展开，突出数据价值与行动项。
    - 建议时长：60-90秒，关键页可延长至120秒。
    """))
(notes_dir / 'total.md').write_text('\n'.join(notes), encoding='utf-8')

run([venv_py, f"{scripts}/total_md_split.py", str(project)])
run([venv_py, f"{scripts}/finalize_svg.py", str(project)])
run([venv_py, f"{scripts}/svg_to_pptx.py", str(project), '-s', 'final'])

exports = sorted((project / 'exports').glob('*.pptx'))
print('PROJECT_DIR=', project)
print('PAGE_COUNT=', len(pages))
for f in exports:
    print('PPTX=', f)
