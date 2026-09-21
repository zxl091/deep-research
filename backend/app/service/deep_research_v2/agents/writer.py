"""
DeepResearch V2.0 - 首席笔杆 Agent (LeadWriter)

职责：
1. 深度写作 - 将零散信息串联成逻辑严密的报告
2. Markdown排版 - 专业的格式排版
3. 图文混排 - 整合文字、图表、数据
4. 参考文献 - 规范的引用格式
"""

import uuid
import re
from typing import Dict, Any, List
from datetime import datetime

from .base import BaseAgent
from ..state import ResearchState, ResearchPhase
from ..writing_pipeline import synthesize, revise, SUMMARY_PROMPT, REVISION_PROMPT


class LeadWriter(BaseAgent):
    """
    首席笔杆 - 最终输出的打磨者

    特点：
    - 深度写作能力
    - 专业的行业研究报告风格
    - 逻辑严密的叙述结构
    - 规范的引用和排版
    """

    SECTION_WRITING_PROMPT = """你是一位顶级投行研究部的首席分析师，擅长撰写深度行业研究报告。

## 研究主题
{query}

## 当前章节信息
标题: {section_title}
描述: {section_description}
类型: {section_type}

## 可用素材

### 相关事实
{facts}

### 数据点
{data_points}

### 已有洞察
{insights}

### 相关图表
{charts_info}

## 写作要求
1. **回答问题**：先给当前章节对用户问题的直接回答，再展开证据；不堆行业术语和通用背景。
2. **分析深度**：按“观察到的差异—证据—可能原因—业务影响与适用条件”论证。推断明确标为分析判断，不能冒充已核验事实。
3. **数据支撑**：关键观点必须有数据或事实支撑
4. **引用规范**：使用可点击链接格式 [来源名称](URL)，如 [艾瑞咨询](https://www.iresearch.cn)
5. **图表整合**：在合适位置插入图表引用 ![图表标题](chart_id)
6. **字数控制**：本章节 500-1000 字
7. **不要重复标题**：正文开头不要再写章节标题
8. **对比任务**：同一维度覆盖用户指定的全部对象，优先使用带来源的 Markdown 对照表；未知单元格写“本次未检索到”。不要用其他公司的数据补齐。
9. **章节分工**：只展开本章问题，其他章节已覆盖的背景一句带过；保留不同统计期间、样本和口径，不强行排名。

## 输出格式
```json
{{
    "content": "章节正文内容（Markdown格式，不包含章节标题）",
    "key_points": ["本章节的核心要点"],
    "citations": [
        {{"source": "来源名称", "url": "完整URL"}}
    ],
    "suggested_improvements": ["如果有更多信息可以改进的地方"]
}}
```

## 写作风格示例
- 好的开头："2024年，中国AI芯片市场正经历深刻变革。根据[IDC数据](https://www.idc.com)，市场规模达到..."
- 避免的开头："关于AI芯片，首先我们来看..."
- 数据引用示例："市场规模达5000亿元（[艾瑞咨询报告](https://www.iresearch.cn/report)）"

开始撰写："""

    SYNTHESIS_PROMPT = SUMMARY_PROMPT
    REVISION_PROMPT = REVISION_PROMPT

    def __init__(self, llm_api_key: str, llm_base_url: str, model: str = "qwen-max"):
        super().__init__(
            name="LeadWriter",
            role="首席笔杆",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model
        )

    async def process(self, state: ResearchState) -> ResearchState:
        """处理入口"""
        if state["phase"] == ResearchPhase.WRITING.value:
            await self._write_report(state)
        elif state["phase"] == ResearchPhase.REVISING.value:
            await self._revise_report(state)
        # 引用只能来自已检索的证据；统一去重和编号。
        sources = self._evidence_sources(state)
        state['references'] = [{'id': i+1, 'source': name, 'url': url} for i, (url, name) in enumerate(sources.items())]
        return state

    @staticmethod
    def _evidence_sources(state):
        return {f['source_url']: f.get('source_name', '原始资料') for f in state.get('facts', []) if f.get('source_url')}

    def _ground_report(self, state, text):
        from ..citations import normalize_citations
        sources = self._evidence_sources(state)
        charts = {c.get('id') for c in state.get('charts', [])}
        text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)',
                      lambda m: f'（图表：{m[1]}，见研究图表）' if m[2] in charts else '（图表未生成）', text)
        return normalize_citations(text, sources)

    async def _write_report(self, state: ResearchState) -> ResearchState:
        """撰写报告"""
        # 发送 research_step 开始事件
        # 注意: step_type 必须是 "writing" 以匹配 graph.py 发送的 phase 事件
        self.add_message(state, "research_step", {
            "step_id": f"step_writing_{uuid.uuid4().hex[:8]}",
            "step_type": "writing",
            "title": "内容生成",
            "subtitle": "撰写研究报告",
            "status": "running",
            "stats": {"sections_count": len(state["outline"]), "word_count": 0}
        })

        self.add_message(state, "thought", {
            "agent": self.name,
            "content": "开始撰写深度研究报告..."
        })

        # 逐章节撰写
        for section in state["outline"]:
            if section.get("status") not in ["final", "drafted"]:
                await self._write_section(state, section)

        # 整合报告
        await self._synthesize_report(state)

        # 发送 research_step 完成事件
        word_count = len(state.get("final_report", ""))
        self.add_message(state, "research_step", {
            "step_type": "writing",
            "title": "内容生成",
            "subtitle": "撰写研究报告",
            "status": "completed",
            "stats": {
                "sections_count": len(state["outline"]),
                "word_count": word_count,
                "references_count": len(state.get("references", []))
            }
        })

        # 更新阶段
        state["phase"] = ResearchPhase.REVIEWING.value

        return state

    async def _write_section(self, state: ResearchState, section: Dict) -> None:
        """撰写单个章节"""
        section_id = section["id"]
        self.logger.info(f"Writing section: {section.get('title')}")

        self.add_message(state, "action", {
            "agent": self.name,
            "tool": "writing_section",
            "section": section.get("title")
        })

        # 收集相关素材
        related_facts = [f for f in state["facts"] if section_id in f.get("related_sections", [])]
        if not related_facts:
            # 如果没有特定关联，使用所有事实
            related_facts = state["facts"][:10]

        # 格式化事实
        facts_text = []
        for fact in related_facts:
            facts_text.append(f"- 抽取事实（需对照原文）: {fact.get('content')}\n  文件: {fact.get('source_name')}\n  原始URL（必须原样引用）: {fact.get('source_url')}")

        # 格式化数据点
        data_text = []
        if state.get('_scoped_runtime'):
            # 历史抽取点没有原句、URL 或完整统计期间，不能作为各章通用事实反复注入。
            for chart in state.get('charts', []):
                for point in chart.get('data_contract', {}).get('points', []):
                    data_text.append(f"- 已匹配原句: {point['quote']}\n  URL: {point['source_url']}")
        else:
            for dp in state["data_points"][:10]:
                data_text.append(f"- {dp.get('name')}: {dp.get('value')} {dp.get('unit', '')} ({dp.get('year', 'N/A')})")

        # 格式化图表信息
        charts_info = []
        for chart in state["charts"]:
            if chart.get("section_id") == section_id:
                charts_info.append(f"- 图表: {chart.get('title')} (ID: {chart.get('id')})")

        prompt = self.SECTION_WRITING_PROMPT.format(
            query=state["query"],
            section_title=section.get("title", ""),
            section_description=section.get("description", ""),
            section_type=section.get("section_type", "mixed"),
            facts="\n".join(facts_text) if facts_text else "（暂无相关事实）",
            data_points="\n".join(data_text) if data_text else "（暂无数据点）",
            insights="\n".join([f"- {i}" for i in state["insights"][:5]]) if state["insights"] else "（暂无洞察）",
            charts_info="\n".join(charts_info) if charts_info else "（暂无图表）"
        )
        prompt += '\n整篇章节分工（避免重复展开）：\n' + '\n'.join(
            f"{s['id']}: {s.get('title', '')} — {s.get('description', '')}" for s in state.get('outline', [section]))

        response = await self.call_llm(
            system_prompt="根据原文证据撰写报告。不得推测原文没有的年份、置信度、编码规则或项目阶段；缺失数据写未知。引用URL必须从原始证据逐字复制，local://也是有效原始URL，禁止替换成网址。不同检索命中同一URL仍是同一份来源，不能称为多源交叉验证。",
            user_prompt=prompt,
            json_mode=True,
            temperature=0.4,
            max_tokens=16000  # 拉满到最大值
        )

        result = self.parse_json_response(response)

        if result and result.get("content"):
            section_content = self._ground_report(state, result["content"])
            state["draft_sections"][section_id] = section_content
            section["status"] = "drafted"

            # 收集引用
            for citation in result.get("citations", []):
                if citation.get('url') not in self._evidence_sources(state):
                    continue
                state["references"].append({
                    "id": len(state["references"]) + 1,
                    "marker": citation.get("marker"),
                    "source": citation.get("source"),
                    "url": citation.get("url", "")
                })

            # 发送章节内容到"过程报告" - 包含完整内容用于流式显示
            self.add_message(state, "section_content", {
                "agent": self.name,
                "section_id": section_id,
                "section_title": section.get("title"),
                "content": section_content,  # 完整章节内容
                "word_count": len(section_content),
                "key_points": result.get("key_points", [])
            })

            # 发送观察消息（显示在左侧步骤流程）
            self.add_message(state, "observation", {
                "agent": self.name,
                "content": f"章节「{section.get('title')}」撰写完成\n字数: {len(section_content)}\n要点: {', '.join(result.get('key_points', [])[:2]) if result.get('key_points') else '无'}"
            })

    async def _synthesize_report(self, state: ResearchState) -> None:
        """保留完整章节，由有界摘要和确定性组装生成报告。"""
        await synthesize(self, state)

    async def _revise_report(self, state: ResearchState) -> ResearchState:
        """逐片段保存修订进度，再交回完整审核。"""
        await revise(self, state)
        state["phase"] = ResearchPhase.REVIEWING.value
        return state
