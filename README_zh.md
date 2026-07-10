# 馃 DeerFlow - 2.0

[English](./README.md) | 涓枃 | [鏃ユ湰瑾瀅(./README_ja.md) | [Fran莽ais](./README_fr.md) | [袪褍褋褋泻懈泄](./README_ru.md)

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./Makefile)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

<a href="https://trendshift.io/repositories/14699" target="_blank"><img src="https://trendshift.io/api/badge/repositories/14699" alt="bytedance%2Fdeer-flow | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>
> 2026 骞?2 鏈?28 鏃ワ紝DeerFlow 2 鍙戝竷鍚庣櫥涓?GitHub Trending 绗?1 鍚嶃€傞潪甯告劅璋㈢ぞ鍖虹殑鏀寔锛岃繖鏄ぇ瀹朵竴璧峰仛鍒扮殑銆?
DeerFlow锛?*D**eep **E**xploration and **E**fficient **R**esearch **Flow**锛夋槸涓€涓紑婧愮殑 **super agent harness**銆傚畠鎶?**sub-agents**銆?*memory** 鍜?**sandbox** 缁勭粐鍦ㄤ竴璧凤紝鍐嶉厤鍚堝彲鎵╁睍鐨?**skills**锛岃 agent 鍙互瀹屾垚鍑犱箮浠讳綍浜嬫儏銆?
https://github.com/user-attachments/assets/a8bcadc4-e040-4cf2-8fda-dd768b999c18

> [!NOTE]
> **DeerFlow 2.0 鏄竴娆″交搴曢噸鍐欍€?* 瀹冨拰 v1 娌℃湁鍏辩敤浠ｇ爜銆傚鏋滀綘瑕佹壘鐨勬槸鏈€鍒濈殑 Deep Research 妗嗘灦锛屽彲浠ュ墠寰€ [`1.x` 鍒嗘敮](https://github.com/bytedance/deer-flow/tree/main-1.x)銆傞偅閲屼粛鐒舵杩庤础鐚紱褰撳墠鐨勪富瑕佸紑鍙戝凡缁忚浆鍚?2.0銆?
## 瀹樼綉

鎯充簡瑙ｆ洿澶氾紝鎴栬€呯洿鎺ョ湅**鐪熷疄婕旂ず**锛屽彲浠ヨ闂甗**瀹樼綉**](https://deerflow.tech)銆?
## 瀛楄妭璺冲姩鐏北寮曟搸鏂硅垷 Coding Plan

- 鎴戜滑鎺ㄨ崘浣跨敤 Doubao-Seed-2.0-Code銆丏eepSeek v3.2 鍜?Kimi 2.5 杩愯 DeerFlow
- [鐜板湪灏卞姞鍏?Coding Plan](https://www.volcengine.com/activity/codingplan?utm_campaign=deer_flow&utm_content=deer_flow&utm_medium=devrel&utm_source=OWO&utm_term=deer_flow)
- [娴峰鍦板尯鐨勫紑鍙戣€呰鐐瑰嚮杩欓噷](https://www.byteplus.com/en/activity/codingplan?utm_campaign=deer_flow&utm_content=deer_flow&utm_medium=devrel&utm_source=OWO&utm_term=deer_flow)

## 鐩綍

- [馃 DeerFlow - 2.0](#-deerflow---20)
  - [瀹樼綉](#瀹樼綉)
  - [InfoQuest](#infoquest)
  - [鐩綍](#鐩綍)
  - [涓€鍙ヨ瘽浜ょ粰 Coding Agent 瀹夎](#涓€鍙ヨ瘽浜ょ粰-coding-agent-瀹夎)
  - [蹇€熷紑濮媇(#蹇€熷紑濮?
    - [閰嶇疆](#閰嶇疆)
    - [杩愯搴旂敤](#杩愯搴旂敤)
      - [閮ㄧ讲寤鸿涓庤祫婧愯鍒抅(#閮ㄧ讲寤鸿涓庤祫婧愯鍒?
      - [鏂瑰紡涓€锛欴ocker锛堟帹鑽愶級](#鏂瑰紡涓€docker鎺ㄨ崘)
      - [鏂瑰紡浜岋細鏈湴寮€鍙慮(#鏂瑰紡浜屾湰鍦板紑鍙?
    - [杩涢樁閰嶇疆](#杩涢樁閰嶇疆)
      - [Sandbox 妯″紡](#sandbox-妯″紡)
      - [MCP Server](#mcp-server)
      - [IM 娓犻亾](#im-娓犻亾)
      - [LangSmith 閾捐矾杩借釜](#langsmith-閾捐矾杩借釜)
  - [浠?Deep Research 鍒?Super Agent Harness](#浠?deep-research-鍒?super-agent-harness)
  - [鏍稿績鐗规€(#鏍稿績鐗规€?
    - [Skills 涓?Tools](#skills-涓?tools)
      - [Claude Code 闆嗘垚](#claude-code-闆嗘垚)
    - [Sub-Agents](#sub-agents)
    - [Sandbox 涓庢枃浠剁郴缁焆(#sandbox-涓庢枃浠剁郴缁?
    - [Context Engineering](#context-engineering)
    - [闀挎湡璁板繂](#闀挎湡璁板繂)
  - [鎺ㄨ崘妯″瀷](#鎺ㄨ崘妯″瀷)
  - [鍐呭祵 Python Client](#鍐呭祵-python-client)
  - [缁堢宸ヤ綔鍙?(TUI)](#缁堢宸ヤ綔鍙?tui)
  - [鏂囨。](#鏂囨。)
  - [鈿狅笍 瀹夊叏浣跨敤](#锔?瀹夊叏浣跨敤)
  - [鍙備笌璐＄尞](#鍙備笌璐＄尞)
  - [璁稿彲璇乚(#璁稿彲璇?
  - [鑷磋阿](#鑷磋阿)
    - [鏍稿績璐＄尞鑰匽(#鏍稿績璐＄尞鑰?
  - [Star History](#star-history)

## 涓€鍙ヨ瘽浜ょ粰 Coding Agent 瀹夎

濡傛灉浣犲湪鐢?Claude Code銆丆odex銆丆ursor銆乄indsurf 鎴栧叾浠?coding agent锛屽彲浠ョ洿鎺ユ妸涓嬮潰杩欏彞璇濆彂缁欏畠锛?
```text
濡傛灉杩樻病 clone DeerFlow锛屽氨鍏?clone锛岀劧鍚庢寜鐓?https://raw.githubusercontent.com/bytedance/deer-flow/main/Install.md 鎶婂畠鐨勬湰鍦板紑鍙戠幆澧冨垵濮嬪寲濂?```

杩欐潯鎻愮ず璇嶆槸缁?coding agent 鐢ㄧ殑銆傚畠浼氬湪闇€瑕佹椂鍏?clone 浠撳簱锛屼紭鍏堥€夋嫨 Docker锛屽畬鎴愬垵濮嬪寲锛屽苟鍦ㄧ粨鏉熸椂鍛婅瘔浣犱笅涓€鏉″惎鍔ㄥ懡浠わ紝浠ュ強杩樼己鍝簺閰嶇疆闇€瑕佷綘琛ュ厖銆?
## 蹇€熷紑濮?
### 閰嶇疆

1. **鍏嬮殕 DeerFlow 浠撳簱**

   ```bash
   git clone https://github.com/bytedance/deer-flow.git
   cd deer-flow
   ```

2. **鐢熸垚鏈湴閰嶇疆鏂囦欢**

   鍦ㄩ」鐩牴鐩綍锛坄deer-flow/`锛夋墽琛岋細

   ```bash
   make config
   ```

   杩欎釜鍛戒护浼氬熀浜庣ず渚嬫ā鏉跨敓鎴愭湰鍦伴厤缃枃浠躲€?
3. **閰嶇疆浣犺浣跨敤鐨勬ā鍨?*

   缂栬緫 `config.yaml`锛岃嚦灏戝畾涔変竴涓ā鍨嬶細

   ```yaml
   models:
     - name: gpt-4                       # 鍐呴儴鏍囪瘑
       display_name: GPT-4               # 灞曠ず鍚嶇О
       use: langchain_openai:ChatOpenAI  # LangChain 绫昏矾寰?       model: gpt-4                      # API 浣跨敤鐨勬ā鍨嬫爣璇?       api_key: $OPENAI_API_KEY          # API key锛堟帹鑽愪娇鐢ㄧ幆澧冨彉閲忥級
       max_tokens: 4096                  # 鍗曟璇锋眰鏈€澶?tokens
       temperature: 0.7                  # 閲囨牱娓╁害

     - name: openrouter-gemini-2.5-flash
       display_name: Gemini 2.5 Flash (OpenRouter)
       use: langchain_openai:ChatOpenAI
       model: google/gemini-2.5-flash-preview
       api_key: $OPENAI_API_KEY          # 杩欓噷 OpenRouter 渚濈劧娌跨敤 OpenAI 鍏煎瀛楁鍚?       base_url: https://openrouter.ai/api/v1
   ```

   OpenRouter 浠ュ強绫讳技鐨?OpenAI 鍏煎缃戝叧锛屽缓璁€氳繃 `langchain_openai:ChatOpenAI` 閰嶅悎 `base_url` 鏉ラ厤缃€傚鏋滀綘鏇存兂鐢?provider 鑷繁鐨勭幆澧冨彉閲忓悕锛屼篃鍙互鐩存帴鎶?`api_key` 鎸囧悜瀵瑰簲鍙橀噺锛屼緥濡?`api_key: $OPENROUTER_API_KEY`銆?
4. **涓哄凡閰嶇疆鐨勬ā鍨嬭缃?API key**

   鍙换閫変互涓嬩竴绉嶆柟寮忥細

- 鏂瑰紡 A锛氱紪杈戦」鐩牴鐩綍涓嬬殑 `.env` 鏂囦欢锛堟帹鑽愶級

   ```bash
   TAVILY_API_KEY=your-tavily-api-key
   OPENAI_API_KEY=your-openai-api-key
   # 濡傛灉閰嶇疆浣跨敤鐨勬槸 langchain_openai:ChatOpenAI + base_url锛孫penRouter 涔熶細璇诲彇 OPENAI_API_KEY
   # 鍏朵粬 provider 鐨?key 鎸夐渶琛ュ厖
   INFOQUEST_API_KEY=your-infoquest-api-key
   ```

- 鏂瑰紡 B锛氬湪 shell 涓鍑虹幆澧冨彉閲?
   ```bash
   export OPENAI_API_KEY=your-openai-api-key
   ```

- 鏂瑰紡 C锛氱洿鎺ョ紪杈?`config.yaml`锛堜笉寤鸿鐢ㄤ簬鐢熶骇鐜锛?
   ```yaml
   models:
     - name: gpt-4
       api_key: your-actual-api-key-here  # 鏇挎崲涓虹湡瀹?key
   ```

### 杩愯搴旂敤

#### 閮ㄧ讲寤鸿涓庤祫婧愯鍒?
鍙互鍏堟寜涓嬮潰鐨勮祫婧愭。浣嶆潵閫夋嫨 DeerFlow 鐨勮繍琛屾柟寮忥細

| 閮ㄧ讲鍦烘櫙 | 璧锋閰嶇疆 | 鎺ㄨ崘閰嶇疆 | 璇存槑 |
|---------|-----------|------------|-------|
| 鏈湴浣撻獙 / `make dev` | 4 vCPU銆? GB 鍐呭瓨銆?0 GB SSD 鍙敤绌洪棿 | 8 vCPU銆?6 GB 鍐呭瓨 | 閫傚悎鍗曚釜寮€鍙戣€呮垨鍗曚釜杞婚噺浼氳瘽锛屼笖妯″瀷璧板閮?API銆俙2 鏍?/ 4 GB` 閫氬父璺戜笉绋炽€?|
| Docker 寮€鍙?/ `make docker-start` | 4 vCPU銆? GB 鍐呭瓨銆?5 GB SSD 鍙敤绌洪棿 | 8 vCPU銆?6 GB 鍐呭瓨 | 闀滃儚鏋勫缓銆佹簮鐮佹寕杞藉拰 sandbox 瀹瑰櫒閮戒細姣旂函鏈湴妯″紡鏇村悆璧勬簮銆?|
| 闀挎湡杩愯鏈嶅姟 / `make up` | 8 vCPU銆?6 GB 鍐呭瓨銆?0 GB SSD 鍙敤绌洪棿 | 16 vCPU銆?2 GB 鍐呭瓨 | 鏇撮€傚悎鍏变韩鐜銆佸 agent 浠诲姟銆佹姤鍛婄敓鎴愭垨鏇撮噸鐨?sandbox 璐熻浇銆?|

- 涓婇潰鐨勯厤缃彧瑕嗙洊 DeerFlow 鏈韩锛涘鏋滀綘杩樿鏈満閮ㄧ讲鏈湴澶фā鍨嬶紝璇峰崟鐙负妯″瀷鏈嶅姟棰勭暀璧勬簮銆?- 鎸佺画杩愯鐨勬湇鍔℃洿鎺ㄨ崘浣跨敤 Linux + Docker銆俶acOS 鍜?Windows 鏇撮€傚悎浣滀负寮€鍙戞満鎴栦綋楠岀幆澧冦€?- 濡傛灉 CPU 鎴栧唴瀛橀暱鏈熸墦婊★紝鍏堥檷浣庡苟鍙戜細璇濇垨閲嶄换鍔℃暟閲忥紝鍐嶈€冭檻鍗囩骇鍒版洿楂樹竴妗ｉ厤缃€?
#### 鏂瑰紡涓€锛欴ocker锛堟帹鑽愶級

**寮€鍙戞ā寮?*锛堟敮鎸佺儹鏇存柊锛屾寕杞芥簮鐮侊級锛?
```bash
make docker-init    # 鎷夊彇 sandbox 闀滃儚锛堥娆¤繍琛屾垨闀滃儚鏇存柊鏃舵墽琛岋級
make docker-start   # 鍚姩鏈嶅姟锛堜細鏍规嵁 config.yaml 鑷姩鍒ゆ柇 sandbox 妯″紡锛?```

濡傛灉 `config.yaml` 浣跨敤鐨勬槸 provisioner 妯″紡锛坄sandbox.use: deerflow.community.aio_sandbox:AioSandboxProvider` 涓旈厤缃簡 `provisioner_url`锛夛紝`make docker-start` 鎵嶄細鍚姩 `provisioner`銆?
**鐢熶骇妯″紡**锛堟湰鍦版瀯寤洪暅鍍忥紝骞舵寕杞借繍琛屾湡閰嶇疆涓庢暟鎹級锛?
```bash
make up     # 鏋勫缓闀滃儚骞跺惎鍔ㄥ叏閮ㄧ敓浜ф湇鍔?make down   # 鍋滄骞剁Щ闄ゅ鍣?```

> [!NOTE]
> 褰撳墠 Agent 杩愯鏃跺祵鍏ュ湪 Gateway 涓繍琛岋紝`/api/langgraph/*` 浼氱敱 nginx 閲嶅啓鍒?Gateway 鐨?LangGraph-compatible API銆?
璁块棶鍦板潃锛歨ttp://localhost:2026

鏇村畬鏁寸殑 Docker 寮€鍙戣鏄庤 [CONTRIBUTING.md](CONTRIBUTING.md)銆?
#### 鏂瑰紡浜岋細鏈湴寮€鍙?
濡傛灉浣犳洿甯屾湜鐩存帴鍦ㄦ湰鍦板惎鍔ㄥ悇涓湇鍔★細

鍓嶆彁锛氬厛瀹屾垚涓婇潰鐨勨€滈厤缃€濇楠わ紙`make config` 鍜屾ā鍨?API key 閰嶇疆锛夈€俙make dev` 闇€瑕佹湁鏁堥厤缃枃浠讹紝榛樿璇诲彇椤圭洰鏍圭洰褰曚笅鐨?`config.yaml`銆傚彲浠ョ敤 `DEER_FLOW_PROJECT_ROOT` 鏄惧紡鎸囧畾椤圭洰鏍圭洰褰曪紝涔熷彲浠ョ敤 `DEER_FLOW_CONFIG_PATH` 鎸囧悜鏌愪釜鍏蜂綋閰嶇疆鏂囦欢銆傝繍琛屾湡鐘舵€侀粯璁ゅ啓鍒伴」鐩牴鐩綍涓嬬殑 `.deer-flow`锛屽彲鐢?`DEER_FLOW_HOME` 瑕嗙洊锛泂kills 榛樿璇诲彇椤圭洰鏍圭洰褰曚笅鐨?`skills/`锛屽彲鐢?`DEER_FLOW_SKILLS_PATH` 瑕嗙洊銆?鍦?Windows 涓婏紝璇蜂娇鐢?Git Bash 杩愯鏈湴寮€鍙戞祦绋嬨€傚熀浜?bash 鐨勬湇鍔¤剼鏈笉鏀寔鐩存帴鍦ㄥ師鐢?`cmd.exe` 鎴?PowerShell 涓墽琛岋紝涓?WSL 涔熶笉淇濊瘉鍙敤锛屽洜涓洪儴鍒嗚剼鏈緷璧?Git for Windows 鐨?`cygpath` 绛夊伐鍏枫€?
1. **妫€鏌ヤ緷璧栫幆澧?*锛?   ```bash
   make check  # 鏍￠獙 Node.js 22+銆乸npm銆乽v銆乶ginx
   ```

2. **瀹夎渚濊禆**锛?   ```bash
   make install  # 瀹夎 backend + frontend 渚濊禆
   ```

3. **锛堝彲閫夛級棰勬媺鍙?sandbox 闀滃儚**锛?   ```bash
   # 濡傛灉浣跨敤 Docker / Container sandbox锛屽缓璁厛鎵ц
   make setup-sandbox
   ```

4. **鍚姩鏈嶅姟**锛?   ```bash
   make dev
   ```

5. **璁块棶鍦板潃**锛歨ttp://localhost:2026

### 杩涢樁閰嶇疆
#### Sandbox 妯″紡

DeerFlow 鏀寔澶氱 sandbox 鎵ц鏂瑰紡锛?- **鏈湴鎵ц**锛堢洿鎺ュ湪瀹夸富鏈轰笂杩愯 sandbox 浠ｇ爜锛?- **Docker 鎵ц**锛堝湪闅旂鐨?Docker 瀹瑰櫒閲岃繍琛?sandbox 浠ｇ爜锛?- **Docker + Kubernetes 鎵ц**锛堥€氳繃 provisioner 鏈嶅姟鍦?Kubernetes Pod 涓繍琛?sandbox 浠ｇ爜锛?
Docker 寮€鍙戞椂锛屾湇鍔″惎鍔ㄨ涓轰細閬靛惊 `config.yaml` 閲岀殑 sandbox 妯″紡銆傚湪 Local / Docker 妯″紡涓嬶紝涓嶄細鍚姩 `provisioner`銆?
濡傛灉瑕侀厤缃綘鑷繁鐨勬ā寮忥紝鍙傝 [Sandbox 閰嶇疆鎸囧崡](backend/docs/CONFIGURATION.md#sandbox)銆?
#### MCP Server

DeerFlow 鏀寔鍙厤缃殑 MCP Server 鍜?skills锛岀敤鏉ユ墿灞曡兘鍔涖€?瀵逛簬 HTTP/SSE MCP Server锛岃繕鏀寔 OAuth token 娴佺▼锛坄client_credentials`銆乣refresh_token`锛夈€?璇︾粏璇存槑瑙?[MCP Server 鎸囧崡](backend/docs/MCP_SERVER.md)銆?
#### IM 娓犻亾

DeerFlow 鏀寔浠庡嵆鏃堕€氳搴旂敤鎺ユ敹浠诲姟銆傚彧瑕侀厤缃畬鎴愶紝瀵瑰簲娓犻亾浼氳嚜鍔ㄥ惎鍔紝鑰屼笖閮戒笉闇€瑕佸叕缃?IP銆?
| 娓犻亾 | 浼犺緭鏂瑰紡 | 涓婃墜闅惧害 |
|---------|-----------|------------|
| Telegram | Bot API锛坙ong-polling锛?| 绠€鍗?|
| Slack | Socket Mode | 涓瓑 |
| Feishu / Lark | WebSocket | 涓瓑 |
| 浼佷笟寰俊鏅鸿兘鏈哄櫒浜?| WebSocket | 涓瓑 |
| 閽夐拤 | Stream Push锛圵ebSocket锛?| 涓瓑 |

**`config.yaml` 涓殑閰嶇疆绀轰緥锛?*

```yaml
channels:
  # LangGraph-compatible Gateway API base URL锛堥粯璁わ細http://localhost:8001/api锛?  langgraph_url: http://localhost:8001/api
  # Gateway API URL锛堥粯璁わ細http://localhost:8001锛?  gateway_url: http://localhost:8001

  # 鍙€夛細鎵€鏈夌Щ鍔ㄧ娓犻亾鍏辩敤鐨勫叏灞€ session 榛樿鍊?  session:
    assistant_id: lead_agent  # 涔熷彲浠ュ～鑷畾涔?agent 鍚嶏紱娓犻亾灞備細鑷姩杞崲涓?lead_agent + agent_name
    config:
      recursion_limit: 100
    context:
      thinking_enabled: true
      is_plan_mode: false
      subagent_enabled: false

  feishu:
    enabled: true
    app_id: $FEISHU_APP_ID
    app_secret: $FEISHU_APP_SECRET
    # domain: https://open.feishu.cn       # 鍥藉唴鐗堬紙榛樿锛?    # domain: https://open.larksuite.com   # 鍥介檯鐗?
  wecom:
    enabled: true
    bot_id: $WECOM_BOT_ID
    bot_secret: $WECOM_BOT_SECRET

  slack:
    enabled: true
    bot_token: $SLACK_BOT_TOKEN     # xoxb-...
    app_token: $SLACK_APP_TOKEN     # xapp-...锛圫ocket Mode锛?    allowed_users: []               # 鐣欑┖琛ㄧず鍏佽鎵€鏈変汉

  telegram:
    enabled: true
    bot_token: $TELEGRAM_BOT_TOKEN
    allowed_users: []               # 鐣欑┖琛ㄧず鍏佽鎵€鏈変汉

    # 鍙€夛細鎸夋笭閬?/ 鎸夌敤鎴峰崟鐙鐩?session 閰嶇疆
    session:
      assistant_id: mobile-agent  # 杩欓噷鍚屾牱鏀寔鑷畾涔?agent 鍚?      context:
        thinking_enabled: false
      users:
        "123456789":
          assistant_id: vip-agent
          config:
            recursion_limit: 150
          context:
            thinking_enabled: true
            subagent_enabled: true

  dingtalk:
    enabled: true
    client_id: $DINGTALK_CLIENT_ID             # 閽夐拤寮€鏀惧钩鍙?ClientId
    client_secret: $DINGTALK_CLIENT_SECRET     # 閽夐拤寮€鏀惧钩鍙?ClientSecret
    allowed_users: []                          # 鐣欑┖琛ㄧず鍏佽鎵€鏈変汉
    card_template_id: ""                       # 鍙€夛細AI 鍗＄墖妯℃澘 ID锛岀敤浜庢祦寮忔墦瀛楁満鏁堟灉
```

璇存槑锛?- `assistant_id: lead_agent` 浼氱洿鎺ヨ皟鐢ㄩ粯璁ょ殑 LangGraph assistant銆?- 濡傛灉 `assistant_id` 濉殑鏄嚜瀹氫箟 agent 鍚嶏紝DeerFlow 浠嶇劧浼氳蛋 `lead_agent`锛屽悓鏃舵妸璇ュ€兼敞鍏ヤ负 `agent_name`锛岃繖鏍?IM 娓犻亾涔熶細鐢熸晥瀵瑰簲 agent 鐨?SOUL 鍜岄厤缃€?
鍦?`.env` 閲岃缃搴旂殑 API key锛?
```bash
# Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# Feishu / Lark
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=your_app_secret

# 浼佷笟寰俊鏅鸿兘鏈哄櫒浜?WECOM_BOT_ID=your_bot_id
WECOM_BOT_SECRET=your_bot_secret

# 閽夐拤
DINGTALK_CLIENT_ID=your_client_id
DINGTALK_CLIENT_SECRET=your_client_secret
```

**Telegram 閰嶇疆**

1. 鎵撳紑 [@BotFather](https://t.me/BotFather)锛屽彂閫?`/newbot`锛屽鍒剁敓鎴愮殑 HTTP API token銆?2. 鍦?`.env` 涓缃?`TELEGRAM_BOT_TOKEN`锛屽苟鍦?`config.yaml` 閲屽惎鐢ㄨ娓犻亾銆?
**Slack 閰嶇疆**

1. 鍓嶅線 [api.slack.com/apps](https://api.slack.com/apps) 鍒涘缓 Slack App锛欳reate New App 鈫?From scratch銆?2. 鍦?**OAuth & Permissions** 涓坊鍔?Bot Token Scopes锛歚app_mentions:read`銆乣chat:write`銆乣im:history`銆乣im:read`銆乣im:write`銆乣files:write`銆?3. 鍚敤 **Socket Mode**锛岀敓鎴愬甫 `connections:write` 鏉冮檺鐨?App-Level Token锛坄xapp-...`锛夈€?4. 鍦?**Event Subscriptions** 涓闃?bot events锛歚app_mention`銆乣message.im`銆?5. 鍦?`.env` 涓缃?`SLACK_BOT_TOKEN` 鍜?`SLACK_APP_TOKEN`锛屽苟鍦?`config.yaml` 涓惎鐢ㄨ娓犻亾銆?
**Feishu / Lark 閰嶇疆**

1. 鍦?[椋炰功寮€鏀惧钩鍙癩(https://open.feishu.cn/) 鍒涘缓搴旂敤锛屽苟鍚敤 **Bot** 鑳藉姏銆?2. 娣诲姞鏉冮檺锛歚im:message`銆乣im:message.p2p_msg:readonly`銆乣im:resource`銆?3. 鍦?**浜嬩欢璁㈤槄** 涓闃?`im.message.receive_v1`锛岃繛鎺ユ柟寮忛€夋嫨 **闀胯繛鎺?*銆?4. 澶嶅埗 App ID 鍜?App Secret锛屽湪 `.env` 涓缃?`FEISHU_APP_ID` 鍜?`FEISHU_APP_SECRET`锛屽苟鍦?`config.yaml` 涓惎鐢ㄨ娓犻亾銆?
**浼佷笟寰俊鏅鸿兘鏈哄櫒浜洪厤缃?*

1. 鍦ㄤ紒涓氬井淇℃櫤鑳芥満鍣ㄤ汉骞冲彴鍒涘缓鏈哄櫒浜猴紝鑾峰彇 `bot_id` 鍜?`bot_secret`銆?2. 鍦?`config.yaml` 涓惎鐢?`channels.wecom`锛屽苟濉叆 `bot_id` / `bot_secret`銆?3. 鍦?`.env` 涓缃?`WECOM_BOT_ID` 鍜?`WECOM_BOT_SECRET`銆?4. 瀹夎鍚庣渚濊禆鏃剁‘淇濆寘鍚?`wecom-aibot-python-sdk`锛屾笭閬撲細閫氳繃 WebSocket 闀胯繛鎺ユ帴鏀舵秷鎭紝鏃犻渶鍏綉鍥炶皟鍦板潃銆?5. 褰撳墠鏀寔鏂囨湰銆佸浘鐗囧拰鏂囦欢鍏ョ珯娑堟伅锛沘gent 鐢熸垚鐨勬渶缁堝浘鐗?鏂囦欢涔熶細鍥炰紶鍒颁紒涓氬井淇′細璇濅腑銆?
**閽夐拤閰嶇疆**

1. 鍦?[閽夐拤寮€鏀惧钩鍙癩(https://open.dingtalk.com/) 鍒涘缓搴旂敤锛屽苟鍚敤 **鏈哄櫒浜?* 鑳藉姏銆?2. 鍦ㄦ満鍣ㄤ汉閰嶇疆椤甸潰璁剧疆娑堟伅鎺ユ敹妯″紡涓?**Stream妯″紡**銆?3. 澶嶅埗 `Client ID` 鍜?`Client Secret`锛屽湪 `.env` 涓缃?`DINGTALK_CLIENT_ID` 鍜?`DINGTALK_CLIENT_SECRET`锛屽苟鍦?`config.yaml` 涓惎鐢ㄨ娓犻亾銆?4. *锛堝彲閫夛級* 濡傞渶寮€鍚祦寮?AI 鍗＄墖鍥炲锛堟墦瀛楁満鏁堟灉锛夛紝璇峰湪[閽夐拤鍗＄墖骞冲彴](https://open.dingtalk.com/document/dingstart/typewriter-effect-streaming-ai-card)鍒涘缓 **AI 鍗＄墖**妯℃澘锛岀劧鍚庡湪 `config.yaml` 涓皢 `card_template_id` 璁句负璇ユā鏉?ID銆傚悓鏃堕渶瑕佺敵璇?`Card.Streaming.Write` 鍜?`Card.Instance.Write` 鏉冮檺銆?
**鍛戒护**

娓犻亾杩炴帴瀹屾垚鍚庯紝浣犲彲浠ョ洿鎺ュ湪鑱婂ぉ绐楀彛閲屽拰 DeerFlow 浜や簰锛?
| 鍛戒护 | 璇存槑 |
|---------|-------------|
| `/new` | 寮€鍚柊瀵硅瘽 |
| `/status` | 鏌ョ湅褰撳墠 thread 淇℃伅 |
| `/models` | 鍒楀嚭鍙敤妯″瀷 |
| `/memory` | 鏌ョ湅 memory |
| `/help` | 鏌ョ湅甯姪 |

> 娌℃湁鍛戒护鍓嶇紑鐨勬秷鎭細琚綋浣滄櫘閫氳亰澶╁鐞嗐€侱eerFlow 浼氳嚜鍔ㄥ垱寤?thread锛屽苟浠ュ璇濇柟寮忓洖澶嶃€?
#### LangSmith 閾捐矾杩借釜

DeerFlow 鍐呯疆浜?[LangSmith](https://smith.langchain.com) 闆嗘垚锛岀敤浜庡彲瑙傛祴鎬с€傚惎鐢ㄥ悗锛屾墍鏈?LLM 璋冪敤銆乤gent 杩愯鍜屽伐鍏锋墽琛岄兘浼氳杩借釜锛屽苟鍦?LangSmith 浠〃鐩樹腑灞曠ず銆?
鍦?`.env` 鏂囦欢涓坊鍔犱互涓嬮厤缃細

```bash
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=lsv2_pt_xxxxxxxxxxxxxxxx
LANGSMITH_PROJECT=xxx
```

Docker 閮ㄧ讲鏃讹紝杩借釜榛樿鍏抽棴銆傚湪 `.env` 涓缃?`LANGSMITH_TRACING=true` 鍜?`LANGSMITH_API_KEY` 鍗冲彲鍚敤銆?
## 浠?Deep Research 鍒?Super Agent Harness

DeerFlow 鏈€鍒濇槸涓€涓?Deep Research 妗嗘灦锛屽悗鏉ョぞ鍖烘妸瀹冧竴璺帹鍒颁簡鏇磋繙鐨勫湴鏂广€備笂绾夸箣鍚庯紝寮€鍙戣€呮嬁瀹冨幓鍋氱殑浜嬫儏鏃╁氨涓嶆鐮旂┒锛氭惌鏁版嵁娴佹按绾裤€佺敓鎴愭紨绀烘枃绋裤€佸揩閫熻捣 dashboard銆佽嚜鍔ㄥ寲鍐呭娴佺▼锛屽緢澶氭柟鍚戜竴寮€濮嬭繛鎴戜滑鑷繁閮芥病鎯冲埌銆?
杩欒鎴戜滑鎰忚瘑鍒颁竴浠朵簨锛欴eerFlow 涓嶅彧鏄竴涓爺绌跺伐鍏枫€傚畠鏇村儚涓€涓?**harness**锛屼竴涓湡姝ｈ agents 鎶婁簨鎯呭仛瀹岀殑杩愯鏃跺熀纭€璁炬柦銆?
鎵€浠ユ垜浠妸瀹冧粠澶撮噸鍋氫簡涓€閬嶃€?
DeerFlow 2.0 涓嶅啀鏄竴涓渶瑕佷綘鑷繁鎷艰鐨?framework銆傚畠鏄竴涓紑绠卞嵆鐢ㄣ€佸悓鏃跺張瓒冲鍙墿灞曠殑 super agent harness銆傚熀浜?LangGraph 鍜?LangChain 鏋勫缓锛岄粯璁ゅ氨甯︿笂浜?agent 鐪熸浼氱敤鍒扮殑鍏抽敭鑳藉姏锛氭枃浠剁郴缁熴€乵emory銆乻kills銆乻andbox 鎵ц鐜锛屼互鍙婁负澶嶆潅澶氭楠や换鍔″仛瑙勫垝銆佹媺璧?sub-agents 鐨勮兘鍔涖€?
浣犲彲浠ョ洿鎺ユ嬁鏉ョ敤锛屼篃鍙互鎷嗗紑閲嶇粍锛屾敼鎴愪綘鑷繁鐨勬牱瀛愩€?
## 鏍稿績鐗规€?
### Skills 涓?Tools

Skills 鏄?DeerFlow 鑳藉仛鈥滃嚑涔庝换浣曚簨鈥濈殑鍏抽敭銆?
鏍囧噯鐨?Agent Skill 鏄竴绉嶇粨鏋勫寲鑳藉姏妯″潡锛岄€氬父灏辨槸涓€涓?Markdown 鏂囦欢锛岄噷闈㈠畾涔変簡宸ヤ綔娴併€佹渶浣冲疄璺碉紝浠ュ強鐩稿叧鐨勫弬鑰冭祫婧愩€侱eerFlow 鑷甫涓€鎵瑰唴缃?skills锛岃鐩栫爺绌躲€佹姤鍛婄敓鎴愩€佹紨绀烘枃绋垮埗浣溿€佺綉椤电敓鎴愩€佸浘鍍忓拰瑙嗛鐢熸垚绛夊満鏅€傜湡姝ｆ湁鎰忔€濈殑鍦版柟鍦ㄤ簬瀹冪殑鎵╁睍鎬э細浣犲彲浠ュ姞鑷繁鐨?skills锛屾浛鎹㈠唴缃?skills锛屾垨鑰呮妸澶氫釜 skills 缁勫悎鎴愬鍚堝伐浣滄祦銆?
Skills 閲囩敤鎸夐渶娓愯繘鍔犺浇锛屼笉浼氫竴娆℃€ф妸鎵€鏈夊唴瀹归兘濉炶繘涓婁笅鏂囥€傚彧鏈変换鍔＄‘瀹為渶瑕佹椂鎵嶅姞杞斤紝杩欐牱鑳芥妸涓婁笅鏂囩獥鍙ｆ帶鍒跺緱鏇村共鍑€锛屼篃鏇撮€傚悎瀵?token 姣旇緝鏁忔劅鐨勬ā鍨嬨€?
閫氳繃 Gateway 瀹夎 `.skill` 鍘嬬缉鍖呮椂锛孌eerFlow 浼氭帴鍙楁爣鍑嗙殑鍙€?frontmatter 鍏冩暟鎹紝姣斿 `version`銆乣author`銆乣compatibility`锛屼笉浼氭妸鏈潵鍚堟硶鐨勫閮?skill 鎷掍箣闂ㄥ銆?
Tools 涔熸槸鍚屾牱鐨勬€濊矾銆侱eerFlow 鑷甫涓€缁勬牳蹇冨伐鍏凤細缃戦〉鎼滅储銆佺綉椤垫姄鍙栥€佹枃浠舵搷浣溿€乥ash 鎵ц锛涘悓鏃朵篃鏀寔閫氳繃 MCP Server 鍜?Python 鍑芥暟鎵╁睍鑷畾涔夊伐鍏枫€備綘鍙互鏇挎崲浠讳綍涓€椤癸紝涔熷彲浠ョ户缁線閲屽姞銆?
Gateway 鐢熸垚鍚庣画寤鸿鏃讹紝鐜板湪浼氬厛鎶婃櫘閫氬瓧绗︿覆杈撳嚭鍜?block/list 椋庢牸鐨勫瘜鏂囨湰鍐呭缁熶竴褰掍竴鍖栵紝鍐嶅幓瑙ｆ瀽 JSON 鏁扮粍鍝嶅簲锛屽洜姝や笉鍚?provider 鐨勫唴瀹瑰寘瑁呮柟寮忎笉浼氬啀鎮勬倓鎶婂缓璁悶鎺夈€?
```text
# sandbox 瀹瑰櫒鍐呯殑璺緞
/mnt/skills/public
鈹溾攢鈹€ research/SKILL.md
鈹溾攢鈹€ report-generation/SKILL.md
鈹溾攢鈹€ slide-creation/SKILL.md
鈹溾攢鈹€ web-page/SKILL.md
鈹斺攢鈹€ image-generation/SKILL.md

/mnt/skills/custom
鈹斺攢鈹€ your-custom-skill/SKILL.md      鈫?浣犵殑 skill
```

#### Claude Code 闆嗘垚

鍊熷姪 `claude-to-deerflow` skill锛屼綘鍙互鐩存帴鍦?[Claude Code](https://docs.anthropic.com/en/docs/claude-code) 閲屽拰姝ｅ湪杩愯鐨?DeerFlow 瀹炰緥浜や簰銆備笉鐢ㄧ寮€缁堢锛屽氨鑳戒笅鍙戠爺绌朵换鍔°€佹煡鐪嬬姸鎬併€佺鐞?threads銆?
**瀹夎杩欎釜 skill锛?*

```bash
npx skills add https://github.com/bytedance/deer-flow --skill claude-to-deerflow
```

鐒跺悗纭 DeerFlow 宸茬粡鍚姩锛堥粯璁ゅ湴鍧€鏄?`http://localhost:2026`锛夛紝鍦?Claude Code 閲屼娇鐢?`/claude-to-deerflow` 鍛戒护鍗冲彲銆?
**浣犲彲浠ュ仛鐨勪簨鎯呭寘鎷細**
- 缁?DeerFlow 鍙戦€佹秷鎭紝骞舵帴鏀舵祦寮忓搷搴?- 閫夋嫨鎵ц妯″紡锛歠lash锛堟洿蹇級銆乻tandard銆乸ro锛堣鍒掓ā寮忥級銆乽ltra锛坰ub-agents 妯″紡锛?- 妫€鏌?DeerFlow 鍋ュ悍鐘舵€侊紝鍒楀嚭 models / skills / agents
- 绠＄悊 threads 鍜屼細璇濆巻鍙?- 涓婁紶鏂囦欢鍋氬垎鏋?
**鐜鍙橀噺**锛堝彲閫夛紝鐢ㄤ簬鑷畾涔夌鐐癸級锛?
```bash
DEERFLOW_URL=http://localhost:2026            # 缁熶竴浠ｇ悊鍩哄湴鍧€
DEERFLOW_GATEWAY_URL=http://localhost:2026    # Gateway API
DEERFLOW_LANGGRAPH_URL=http://localhost:2026/api/langgraph  # LangGraph API
```

瀹屾暣 API 璇存槑瑙?[`skills/public/claude-to-deerflow/SKILL.md`](skills/public/claude-to-deerflow/SKILL.md)銆?
### Sub-Agents

澶嶆潅浠诲姟閫氬父涓嶅彲鑳戒竴娆″畬鎴愶紝DeerFlow 浼氬厛鎷嗚В锛屽啀鎵ц銆?
lead agent 鍙互鎸夐渶鍔ㄦ€佹媺璧?sub-agents銆傛瘡涓?sub-agent 閮芥湁鑷繁鐙珛鐨勪笂涓嬫枃銆佸伐鍏峰拰缁堟鏉′欢銆傚彧瑕佹潯浠跺厑璁革紝瀹冧滑灏变細骞惰杩愯锛岃繑鍥炵粨鏋勫寲缁撴灉锛屾渶鍚庡啀鐢?lead agent 姹囨€绘垚涓€浠藉畬鏁磋緭鍑恒€?
杩欎篃鏄?DeerFlow 鑳藉鐞嗕粠鍑犲垎閽熷埌鍑犲皬鏃朵换鍔＄殑鍘熷洜銆傛瘮濡備竴涓爺绌朵换鍔★紝鍙互鎷嗘垚鍗佸嚑涓?sub-agents锛屽垎鍒帰绱笉鍚屾柟鍚戯紝鏈€鍚庡悎骞舵垚涓€浠芥姤鍛婏紝鎴栬€呬竴涓綉绔欙紝鎴栬€呬竴濂楀甫鐢熸垚瑙嗚鍐呭鐨勬紨绀烘枃绋裤€備竴涓?harness锛屽璺苟琛屻€?
### Sandbox 涓庢枃浠剁郴缁?
DeerFlow 涓嶅彧鏄€滀細璇村畠鑳藉仛鈥濓紝瀹冩槸鐪熺殑鏈変竴鍙拌嚜宸辩殑鈥滅數鑴戔€濄€?
姣忎釜浠诲姟閮借繍琛屽湪闅旂鐨?Docker 瀹瑰櫒閲岋紝閲岄潰鏈夊畬鏁寸殑鏂囦欢绯荤粺锛屽寘鎷?skills銆亀orkspace銆乽ploads銆乷utputs銆俛gent 鍙互璇诲啓鍜岀紪杈戞枃浠讹紝鍙互鎵ц bash 鍛戒护鍜屼唬鐮侊紝涔熷彲浠ユ煡鐪嬪浘鐗囥€傛暣涓繃绋嬮兘鍦?sandbox 鍐呭畬鎴愶紝鍙璁°€佷細闅旂锛屼笉浼氬湪涓嶅悓 session 涔嬮棿浜掔浉姹℃煋銆?
杩欏氨鏄€滃甫宸ュ叿鐨勮亰澶╂満鍣ㄤ汉鈥濆拰鈥滅湡姝ｆ湁鎵ц鐜鐨?agent鈥濅箣闂寸殑宸埆銆?
```text
# sandbox 瀹瑰櫒鍐呯殑璺緞
/mnt/user-data/
鈹溾攢鈹€ uploads/          鈫?浣犵殑鏂囦欢
鈹溾攢鈹€ workspace/        鈫?agents 鐨勫伐浣滅洰褰?鈹斺攢鈹€ outputs/          鈫?鏈€缁堜氦浠樼墿
```

### Context Engineering

**闅旂鐨?Sub-Agent Context**锛氭瘡涓?sub-agent 閮藉湪鑷繁鐙珛鐨勪笂涓嬫枃閲岃繍琛屻€傚畠鐪嬩笉鍒颁富 agent 鐨勪笂涓嬫枃锛屼篃鐪嬩笉鍒板叾浠?sub-agents 鐨勪笂涓嬫枃銆傝繖鏍峰仛鐨勭洰鐨勫緢鐩存帴锛屽氨鏄瀹冨彧鑱氱劍褰撳墠浠诲姟锛屼笉琚棤鍏充俊鎭共鎵般€?
**鎽樿鍘嬬缉**锛氬湪鍗曚釜 session 鍐咃紝DeerFlow 浼氭瘮杈冪Н鏋佸湴绠＄悊涓婁笅鏂囷紝鍖呮嫭鎬荤粨宸插畬鎴愮殑瀛愪换鍔°€佹妸涓棿缁撴灉杞瓨鍒版枃浠剁郴缁熴€佸帇缂╂殏鏃朵笉閲嶈鐨勪俊鎭€傝繖鏍峰湪闀块摼璺€佸姝ラ浠诲姟閲岋紝瀹冧篃鑳戒繚鎸佽仛鐒︼紝鑰屼笉浼氳交鏄撴妸涓婁笅鏂囩獥鍙ｆ墦鐖嗐€?
### 闀挎湡璁板繂

澶у鏁?agents 浼氬湪瀵硅瘽缁撴潫鍚庢妸涓€鍒囬兘蹇樻帀锛孌eerFlow 涓嶄竴鏍枫€?
璺?session 浣跨敤鏃讹紝DeerFlow 浼氶€愭绉疮鍏充簬浣犵殑鎸佷箙 memory锛屽寘鎷綘鐨勪釜浜哄亸濂姐€佺煡璇嗚儗鏅紝浠ュ強闀挎湡娌夋穩涓嬫潵鐨勫伐浣滀範鎯€備綘鐢ㄥ緱瓒婂锛屽畠瓒婁簡瑙ｄ綘鐨勫啓浣滈鏍笺€佹妧鏈爤鍜岄噸澶嶅嚭鐜扮殑宸ヤ綔娴併€俶emory 淇濆瓨鍦ㄦ湰鍦帮紝鎺у埗鏉冧篃濮嬬粓鍦ㄤ綘鎵嬮噷銆?
## 鎺ㄨ崘妯″瀷

DeerFlow 瀵规ā鍨嬫病鏈夊己缁戝畾锛屽彧瑕佸疄鐜颁簡 OpenAI 鍏煎 API 鐨?LLM锛岀悊璁轰笂閮藉彲浠ユ帴鍏ャ€備笉杩囧湪涓嬮潰杩欎簺鑳藉姏涓婅〃鐜版洿寮虹殑妯″瀷锛岄€氬父浼氭洿閫傚悎 DeerFlow锛?
- **闀夸笂涓嬫枃绐楀彛**锛?00k+ tokens锛夛紝閫傚悎娣卞害鐮旂┒鍜屽姝ラ浠诲姟
- **鎺ㄧ悊鑳藉姏**锛岄€傚悎鑷€傚簲瑙勫垝鍜屽鏉傛媶瑙?- **澶氭ā鎬佽緭鍏?*锛岄€傚悎鐞嗚В鍥剧墖鍜岃棰?- **绋冲畾鐨?tool use 鑳藉姏**锛岄€傚悎鍙潬鐨勫嚱鏁拌皟鐢ㄥ拰缁撴瀯鍖栬緭鍑?
## 鍐呭祵 Python Client

DeerFlow 涔熷彲浠ヤ綔涓哄唴宓岀殑 Python 搴撲娇鐢紝涓嶅繀鍚姩瀹屾暣鐨?HTTP 鏈嶅姟銆俙DeerFlowClient` 鎻愪緵浜嗚繘绋嬪唴鐨勭洿鎺ヨ闂柟寮忥紝瑕嗙洊鎵€鏈?agent 鍜?Gateway 鑳藉姏锛岃繑鍥炵殑鏁版嵁缁撴瀯涓?HTTP Gateway API 淇濇寔涓€鑷达細

```python
from deerflow.client import DeerFlowClient

client = DeerFlowClient()

# Chat
response = client.chat("Analyze this paper for me", thread_id="my-thread")

# Streaming锛圠angGraph SSE 鍗忚锛歷alues銆乵essages-tuple銆乪nd锛?for event in client.stream("hello"):
    if event.type == "messages-tuple" and event.data.get("type") == "ai":
        print(event.data["content"])

# 閰嶇疆涓庣鐞嗭細杩斿洖鍊间笌 Gateway 瀵归綈鐨?dict
models = client.list_models()        # {"models": [...]}
skills = client.list_skills()        # {"skills": [...]}
client.update_skill("web-search", enabled=True)
client.upload_files("thread-1", ["./report.pdf"])  # {"success": True, "files": [...]}
```

鎵€鏈夎繑鍥?dict 鐨勬柟娉曢兘浼氬湪 CI 涓€氳繃 Gateway 鐨?Pydantic 鍝嶅簲妯″瀷鏍￠獙锛坄TestGatewayConformance`锛夛紝浠ョ‘淇濆唴宓?client 濮嬬粓鍜?HTTP API schema 淇濇寔鍚屾銆傚畬鏁?API 璇存槑瑙?`backend/packages/harness/deerflow/client.py`銆?
## 缁堢宸ヤ綔鍙?(TUI)

`deerflow` 鏄竴涓潰鍚戠粓绔敤鎴风殑宸ヤ綔鍙帮紝**鍐呭祵**杩愯鍦?`DeerFlowClient` 涔嬩笂鈥斺€旀棤闇€鍚姩 Gateway銆佸墠绔€乶ginx 鎴?Docker锛屽悓鏃舵部鐢ㄤ笌 DeerFlow 鍏跺畠閮ㄥ垎鐩稿悓鐨?`config.yaml`銆乧heckpointer銆佹妧鑳姐€佽蹇嗐€丮CP 鍜屾矙绠遍厤缃€?
![DeerFlow TUI](docs/tui/tui-preview.svg)

```bash
uv pip install 'deerflow-harness[tui]'        # 鍙€夌殑 'textual' 渚濊禆

deerflow                                      # 鍚姩缁堢 UI锛堥渶瑕?TTY锛?deerflow --continue                           # 鎭㈠鏈€杩戜竴娆′細璇?deerflow --resume THREAD                      # 鎸?id 鎭㈠鎸囧畾浼氳瘽
deerflow --print "鎬荤粨涓€涓嬭繖涓粨搴?             # 鏃犲ご妯″紡锛岀粨鏋滄墦鍗板埌 stdout
deerflow --json  "hello"                       # 鏃犲ご妯″紡锛岃緭鍑烘寜琛屽垎闅旂殑 StreamEvent
```

閿洏椹卞姩鐨勫璇濈晫闈細娴佸紡娓叉煋鐨勫璇濆尯锛堝洖绛旀寜 Markdown 娓叉煋锛夈€佺揣鍑戠殑宸ュ叿娲诲姩鍗＄墖銆乣/` 鏂滄潬鍛戒护闈㈡澘銆乣/model` 涓?`/threads` 閫夋嫨鍣ㄣ€佽緭鍏ュ巻鍙诧紝浠ュ強 `Esc` / `Ctrl+C` 鎵撴柇銆傚湪 TUI 閲屽紑鍚殑浼氳瘽涔熶細鍑虹幇鍦?Web UI 渚ц竟鏍忊€斺€斿畠浼氫互鏈湴榛樿鐢ㄦ埛韬唤鍐欏叆鍏变韩鐨勪細璇濆瓨鍌紝鍥犳缁堢涓庣綉椤典繚鎸佸悓姝ワ紝**鏃犻渶杩愯 Gateway**銆?
瀹屾暣璇存槑瑙?[backend/docs/TUI.md](backend/docs/TUI.md)銆?
## 鏂囨。

- [璐＄尞鎸囧崡](CONTRIBUTING.md) - 寮€鍙戠幆澧冩惌寤轰笌鍗忎綔娴佺▼
- [閰嶇疆鎸囧崡](backend/docs/CONFIGURATION.md) - 瀹夎涓庨厤缃鏄?- [鏋舵瀯姒傝](backend/CLAUDE.md) - 鎶€鏈灦鏋勮鏄?- [鍚庣鏋舵瀯](backend/README.md) - 鍚庣鏋舵瀯涓?API 鍙傝€?
## 鈿狅笍 瀹夊叏浣跨敤

### 涓嶆伆褰撶殑閮ㄧ讲鍙兘瀵艰嚧瀹夊叏椋庨櫓

DeerFlow 鍏峰**绯荤粺鎸囦护鎵ц銆佽祫婧愭搷浣溿€佷笟鍔￠€昏緫璋冪敤**绛夊叧閿珮鏉冮檺鑳藉姏锛岄粯璁よ璁′负**閮ㄧ讲鍦ㄦ湰鍦板彲淇＄幆澧冿紙浠呮湰鏈?127.0.0.1 鍥炵幆璁块棶锛?*銆傝嫢鎮ㄥ皢 agent 閮ㄧ讲鑷充笉鍙俊灞€鍩熺綉銆佸叕缃戜簯鏈嶅姟鍣ㄧ瓑鍙澶氱粓绔闂殑缃戠粶鐜锛屼笖鏈噰鍙栦弗鏍肩殑瀹夊叏闃叉姢鎺柦锛屽彲鑳藉鑷村畨鍏ㄩ闄╋紝渚嬪锛?
- **鏈巿鏉冪殑闈炴硶璋冪敤**锛歛gent 鍔熻兘琚湭鎺堟潈鐨勭涓夋柟銆佸叕缃戞伓鎰忔壂鎻忕▼搴忔帰娴嬪埌锛岃繘鑰屽彂璧锋壒閲忛潪娉曡皟鐢ㄨ姹傦紝鎵ц绯荤粺鍛戒护銆佹枃浠惰鍐欑瓑楂樺嵄鎿嶄綔锛屽彲鑳藉鑷村畨鍏ㄥ悗鏋溿€?- **鍚堣涓庢硶寰嬮闄?*锛氳嫢 agent 琚潪娉曡皟鐢ㄧ敤浜庡疄鏂界綉缁滄敾鍑汇€佷俊鎭獌鍙栫瓑杩濇硶杩濊琛屼负锛屽彲鑳戒骇鐢熸硶寰嬭矗浠讳笌鍚堣椋庨櫓銆?
### 瀹夊叏浣跨敤寤鸿

**娉ㄦ剰锛氬缓璁偍灏?DeerFlow 閮ㄧ讲鍦ㄦ湰鍦板彲淇＄殑缃戠粶鐜涓嬨€?* 鑻ユ偍鏈夎法璁惧銆佽法缃戠粶鐨勯儴缃查渶姹傦紝蹇呴』鍔犲叆涓ユ牸鐨勫畨鍏ㄦ帾鏂姐€備緥濡傦紝閲囧彇濡備笅鎵嬫锛?
- **璁剧疆璁块棶 IP 鐧藉悕鍗?*锛氫娇鐢?`iptables`锛屾垨閮ㄧ讲纭欢闃茬伀澧?/ 甯﹁闂帶鍒讹紙ACL锛夊姛鑳界殑浜ゆ崲鏈虹瓑锛?*閰嶇疆瑙勫垯璁剧疆 IP 鐧藉悕鍗?*锛屾嫆缁濆叾浠栨墍鏈?IP 杩涜璁块棶銆?- **鍓嶇疆韬唤楠岃瘉**锛氶厤缃弽鍚戜唬鐞嗭紙nginx 绛夛級锛屽苟**寮€鍚珮寮哄害鐨勫墠缃韩浠介獙璇佸姛鑳?*锛岀姝㈡棤浠讳綍韬唤楠岃瘉鐨勮闂€?- **缃戠粶闅旂**锛氳嫢鏈夊彲鑳斤紝寤鸿灏?agent 鍜屽彲淇¤澶囧垝鍒嗗埌**鍚屼竴涓笓鐢?VLAN**锛屼笌鍏朵粬缃戠粶璁惧鍋氶殧绂汇€?- **鎸佺画鍏虫敞椤圭洰鏇存柊**锛氳鎸佺画鍏虫敞 DeerFlow 椤圭洰鐨勫畨鍏ㄥ姛鑳芥洿鏂般€?
## 鍙備笌璐＄尞

娆㈣繋鍙備笌璐＄尞銆傚紑鍙戠幆澧冦€佸伐浣滄祦鍜岀浉鍏宠鑼冭 [CONTRIBUTING.md](CONTRIBUTING.md)銆?
鐩墠鍥炲綊娴嬭瘯宸茬粡瑕嗙洊 Docker sandbox 妯″紡璇嗗埆锛屼互鍙?`backend/tests/` 涓?provisioner kubeconfig-path 澶勭悊鐩稿叧娴嬭瘯銆?
## 璁稿彲璇?
鏈」鐩噰鐢?[MIT License](./LICENSE) 寮€婧愬彂甯冦€?
## 鑷磋阿

DeerFlow 寤虹珛鍦ㄥ紑婧愮ぞ鍖哄ぇ閲忎紭绉€宸ヤ綔鐨勫熀纭€涓娿€傛墍鏈夎 DeerFlow 鎴愪负鍙兘鐨勯」鐩拰璐＄尞鑰咃紝鎴戜滑閮藉績鎬€鎰熻阿銆傛涓嶅じ寮犲湴璇达紝鎴戜滑鏄珯鍦ㄥ法浜虹殑鑲╄唨涓婄户缁線鍓嶈蛋銆?
鐗瑰埆鎰熻阿浠ヤ笅椤圭洰甯︽潵鐨勫叧閿敮鎸侊細

- **[LangChain](https://github.com/langchain-ai/langchain)**锛氬畠浠彁渚涚殑浼樼妗嗘灦鏀拺浜嗘垜浠殑 LLM 浜や簰涓?chains锛岃鏁翠綋闆嗘垚鍜岃兘鍔涚紪鎺掗『鐣呭彲鐢ㄣ€?- **[LangGraph](https://github.com/langchain-ai/langgraph)**锛氬畠浠湪澶?agent 缂栨帓涓婄殑鍒涙柊鏂瑰紡锛屾槸 DeerFlow 澶嶆潅宸ヤ綔娴佸緱浠ユ垚绔嬬殑閲嶈鍩虹銆?
杩欎簺椤圭洰浣撶幇浜嗗紑婧愬崗浣滅湡姝ｇ殑鍔涢噺锛屾垜浠篃寰堥珮鍏磋兘缁х画寤虹珛鍦ㄨ繖浜涘熀纭€涔嬩笂銆?
### 鏍稿績璐＄尞鑰?
鎰熻阿 `DeerFlow` 鐨勬牳蹇冧綔鑰咃紝鏄粬浠殑鍒ゆ柇銆佹姇鍏ュ拰鎸佺画鎺ㄨ繘锛屾墠璁╄繖涓」鐩湡姝ｈ惤鍦帮細

- **[Daniel Walnut](https://github.com/hetaoBackend/)**
- **[Henry Li](https://github.com/magiccube/)**

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=bytedance/deer-flow&type=Date)](https://star-history.com/#bytedance/deer-flow&Date)
