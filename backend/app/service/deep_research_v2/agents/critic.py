"""
DeepResearch V2.0 - 毒舌评论家 Agent (CriticMaster)

职责：
1. 客观质检 - 识别影响核心结论的问题，满足要求时通过
2. 逻辑漏洞检测 - 检查推理链条
3. 幻觉查杀 - 识别无来源或错误的信息
4. 偏见识别 - 发现观点偏颇
"""

import uuid
from typing import Dict, Any, List
from datetime import datetime

from .base import BaseAgent
from ..state import ResearchState, ResearchPhase
from ..report_quality import normalize_review


class CriticMaster(BaseAgent):
    """
    毒舌评论家 - 质量守门人

    特点：
    - 客观核对：区分事实错误与资料局限
    - 与研究范围相适应的证据要求
    - 逻辑一致性检查
    - 有权打回重写
    """

    REVIEW_PROMPT = """你负责审核研究报告的准确性、逻辑一致性与问题覆盖情况。

## 审核原则（必须严格执行）
1. **事实准确**：数值、单位、时间与对象应符合所提供的证据，不得把预测或目标写成实绩。
2. **来源合理**：本项目主要通过 Bocha 获取公开资料及摘录。允许引用行业媒体、券商、商业机构和转载，不因缺少官方来源或独立交叉验证直接判为幻觉。
3. **区分错误与缺口**：未检索到、摘录不完整、来源自身有疑点，不等于报告写错。核心断言缺乏支持时，应弱化或删除；已说明且不影响核心结论的局限，不阻止通过。
4. **逻辑一致**：检查正文、摘要和图表是否矛盾。指出错误须给出具体依据，不凭常识擅自修改来源数据。
5. **时效清晰**：历史数据和预测可以使用，但应标注年份与性质。不因超过两年就自动判为无效。
6. **范围适当**：以用户问题和研究大纲为准，不强制增加额外专题。缺少完整量化数据时，允许有依据的定性分析。
7. **口径核对**：用户规模不等于日活，企业总资本开支不等于AI投资；增长到3倍不等于增长3倍。发布日期不等于统计期，同一年度的不同发布不得写成跨期增长。
8. **预测与缺口**：定量预测必须有原文或可复算依据，不能因写了“预计”就允许补值；未检索到不等于未披露。摘要和结论新增数字也必须审核。
9. **避免误判**：只有主体、指标、统计期间和范围一致的断言才能直接判数值冲突。全年与一季度金额不同本身不矛盾；“一半”与50%等价，但“超过一半”不等于精确50%。先定位原文实际表述，摘要遗漏年份只修摘要，不声称全篇未说明。
10. **图文分开核验**：图表因其他对象缺值、期间不完整或程序无法解析而失败，不等于正文中的单个数字无来源；应直接核对该数字的原文。公开转载允许作为依据，不因没有官方截图打回。图表缺口已说明且不影响回答主要问题时，只保留为局限。

## 研究问题
{query}

## 研究大纲
{outline}

## 待审核内容

### 章节草稿
{draft_content}

### 引用的事实
{facts}

### 使用的数据点
{data_points}

## 任务
找出影响报告使用的问题，并给出最小可执行修改建议。
能通过修改表述、说明局限或删除争议内容解决的，不要求补查。
多轮审核优先确认旧问题是否解决，不重复打回已修复的问题。
missing_aspects 仅列回答用户主要问题所必需且尚未覆盖的内容，不列可选拓展专题。

## 输出格式
```json
{{
    "overall_assessment": {{
        "quality_score": 1-10,
        "verdict": "pass/needs_revision/major_issues",
        "summary": "整体评估摘要"
    }},
    "issues": [
        {{
            "id": "issue_1",
            "target_section": "章节ID或'全局'",
            "issue_type": "missing_source/logic_error/bias/hallucination/outdated/incomplete",
            "severity": "critical/major/minor",
            "location": "具体位置描述",
            "description": "问题详细描述",
            "evidence": "为什么这是问题的证据",
            "suggestion": "具体的修改建议",
            "requires_new_search": true或false,
            "search_query": "如果需要补充搜索，建议的关键词"
        }}
    ],
    "fact_check_results": [
        {{
            "fact_id": "事实ID",
            "status": "verified/unverified/suspicious/false",
            "reason": "判断理由"
        }}
    ],
    "missing_aspects": ["报告中遗漏的重要方面"],
    "strength_points": ["报告中做得好的地方"]
}}
```

## 严重程度说明
- critical: 核心事实错误、严重证据矛盾，导致主要结论不可信。
- major: 影响核心结论的无依据断言、明显逻辑冲突，或未回答用户明确要求的主要问题。
- minor: 不影响主要结论的来源局限、细节缺失或表述问题。已准确披露的检索缺口无需重复列为问题。

## 评分标准（1-10分制）
- 9-10分：内容充分、准确清晰，可直接使用。
- 7-8分：主要问题已回答，无 critical/major 问题；允许存在已说明的资料局限，审核通过（verdict=pass）。
- 5-6分：存在影响核心结论的问题，需要修订。
- 3-4分：多处核心错误，需要大幅修改。
- 1-2分：核心内容严重失实或基本未回答用户问题。

注意：quality_score >= 7 且无 critical/major 问题时，才能设置 verdict 为 "pass"

开始你的审核："""

    FINAL_CHECK_PROMPT = """你是最终质量把关人。这是修订后的研究报告。

## 原始问题
{query}

## 之前的问题
{previous_issues}

## 修订后的内容
{revised_content}

## 任务
检查之前的问题是否已解决，是否有新问题产生。

输出JSON：
```json
{{
    "resolved_issues": ["已解决的问题ID列表"],
    "unresolved_issues": ["未解决的问题ID列表"],
    "new_issues": [{{
        "description": "新发现的问题",
        "severity": "critical/major/minor"
    }}],
    "final_verdict": "approved/needs_more_work",
    "final_score": 1-10,
    "publication_readiness": "ready/almost_ready/not_ready",
    "final_comments": "最终评语"
}}
```"""

    def __init__(self, llm_api_key: str, llm_base_url: str, model: str = "qwen-max"):
        super().__init__(
            name="CriticMaster",
            role="毒舌评论家",
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model=model
        )

    async def process(self, state: ResearchState) -> ResearchState:
        """处理入口"""
        self.logger.info(f"[CriticMaster] ========== process 开始 ==========")
        self.logger.info(f"[CriticMaster] phase: {state['phase']}, final_report 长度: {len(state.get('final_report', ''))}")

        if state["phase"] != ResearchPhase.REVIEWING.value:
            self.logger.info(f"[CriticMaster] phase 不是 REVIEWING，跳过")
            return state

        self.add_message(state, "thought", {
            "agent": self.name,
            "content": "开始严格审核研究报告，准备找出所有问题..."
        })

        # 执行审核
        self.logger.info(f"[CriticMaster] 开始调用 _review_content...")
        review_result = await self._review_content(state)
        self.logger.info(f"[CriticMaster] 审核完成，结果: {bool(review_result)}")

        if review_result:
            review_result = normalize_review(review_result, state.get('report_validation', {}).get('issues', []))
            state['last_review'] = review_result
            # 记录反馈
            for issue in review_result.get("issues", []):
                issue["id"] = f"issue_{uuid.uuid4().hex[:8]}"
                issue["resolved"] = False
                state["critic_feedback"].append(issue)

            # 更新质量分数
            state["quality_score"] = review_result.get("overall_assessment", {}).get("quality_score", 0.0)
            state["unresolved_issues"] = len([i for i in review_result.get("issues", []) if i.get("severity") in ["critical", "major"]])

            # 发送审核结果
            self.add_message(state, "review", {
                "agent": self.name,
                "verdict": review_result.get("overall_assessment", {}).get("verdict"),
                "quality_score": state["quality_score"],
                "issues_count": len(review_result.get("issues", [])),
                "critical_issues": len([i for i in review_result.get("issues", []) if i.get("severity") == "critical"]),
                "major_issues": len([i for i in review_result.get("issues", []) if i.get("severity") == "major"]),
                "summary": review_result.get("overall_assessment", {}).get("summary", ""),
                "missing_aspects": review_result.get("missing_aspects", [])
            })

            # 如果有严重问题，发送具体反馈
            critical_issues = [i for i in review_result.get("issues", []) if i.get("severity") == "critical"]
            for issue in critical_issues[:3]:  # 最多展示3个严重问题
                self.add_message(state, "critic_feedback", {
                    "agent": self.name,
                    "issue_type": issue.get("issue_type"),
                    "severity": issue.get("severity"),
                    "description": issue.get("description"),
                    "suggestion": issue.get("suggestion")
                })

            # 决定下一步 - 智能路由
            verdict = review_result.get("overall_assessment", {}).get("verdict", "needs_revision")

            if verdict == "pass":
                state["phase"] = ResearchPhase.COMPLETED.value
            elif state["iteration"] >= state["max_iterations"]:
                # 达到最大迭代次数，强制完成
                state["phase"] = ResearchPhase.COMPLETED.value
                self.add_message(state, "warning", {
                    "agent": self.name,
                    "content": "已达最大迭代次数，部分问题可能未解决"
                })
            else:
                # 智能路由：判断是需要补充搜索还是仅修改文字
                needs_new_search = self._analyze_issues_for_routing(review_result)

                if needs_new_search["should_research"]:
                    # 需要补充搜索 -> 回到研究阶段
                    state["phase"] = ResearchPhase.RE_RESEARCHING.value
                    state["pending_search_queries"] = needs_new_search["search_queries"]
                    self.add_message(state, "thought", {
                        "agent": self.name,
                        "content": f"发现信息缺失问题，需要补充搜索: {', '.join(needs_new_search['search_queries'][:3])}"
                    })
                else:
                    # 仅需要文字修改 -> 修订阶段
                    state["phase"] = ResearchPhase.REVISING.value

                state["iteration"] += 1

        return state

    def _analyze_issues_for_routing(self, review_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        分析问题类型，决定路由方向

        Returns:
            {
                "should_research": bool,  # 是否需要重新搜索
                "search_queries": List[str]  # 建议的搜索查询
            }
        """
        issues = review_result.get("issues", [])
        missing_aspects = review_result.get("missing_aspects", [])

        # 需要补充搜索的问题类型
        research_needed_types = {"missing_source", "incomplete", "outdated"}

        search_queries = []
        research_issues_count = 0

        for issue in issues:
            issue_type = issue.get("issue_type", "")
            severity = issue.get("severity", "minor")

            # 检查是否是需要搜索的问题类型
            if issue_type in research_needed_types and severity in ["critical", "major"]:
                research_issues_count += 1

                # 收集搜索建议
                if issue.get("requires_new_search") and issue.get("search_query"):
                    search_queries.append(issue["search_query"])

        # 添加遗漏方面的搜索查询
        for aspect in missing_aspects[:3]:
            search_queries.append(aspect)

        # 决策：如果有超过30%的严重问题需要搜索，或者有明确的搜索建议，则回到搜索阶段
        total_critical_major = len([i for i in issues if i.get("severity") in ["critical", "major"]])
        should_research = (
            len(search_queries) > 0 and
            (research_issues_count > 0 or len(missing_aspects) > 0) and
            (total_critical_major == 0 or research_issues_count / max(total_critical_major, 1) > 0.3)
        )

        return {
            "should_research": should_research,
            "search_queries": list(set(search_queries))[:5]  # 去重，最多5个查询
        }

    async def _review_content(self, state: ResearchState) -> Dict[str, Any]:
        """审核内容"""
        self.logger.info(f"[CriticMaster] _review_content 开始")

        # 准备草稿内容
        draft_content = ""
        for section_id, content in state["draft_sections"].items():
            section = next((s for s in state["outline"] if s.get("id") == section_id), {})
            draft_content += f"\n## {section.get('title', section_id)}\n{content}\n"

        if not draft_content:
            draft_content = state.get("final_report", "（暂无内容）")

        self.logger.info(f"[CriticMaster] 待审核内容长度: {len(draft_content)}")

        # 准备事实列表
        facts_summary = []
        for fact in state["facts"][:80]:
            facts_summary.append(f"- [{fact.get('id')}] {fact.get('content', '')[:1000]} (来源: {fact.get('source_name')}, URL: {fact.get('source_url')})")

        # 准备数据点列表
        data_summary = []
        for dp in state["data_points"][:15]:
            data_summary.append(f"- 模型抽取候选（未经原句核验，不可用于证明报告正确或错误）: {dp.get('name')}: {dp.get('value')} {dp.get('unit', '')} (来源: {dp.get('source')})")

        # 格式化大纲
        outline_summary = []
        for section in state["outline"]:
            outline_summary.append(f"- {section.get('id')}: {section.get('title')} ({section.get('status', 'pending')})")

        prompt = self.REVIEW_PROMPT.format(
            query=state["query"],
            outline="\n".join(outline_summary),
            draft_content=(state.get('final_report') or draft_content)[:60000],
            facts="\n".join(facts_summary) if facts_summary else "（暂无事实记录）",
            data_points="\n".join(data_summary) if data_summary else "（暂无数据点）"
        )

        self.logger.info(f"[CriticMaster] 调用 LLM 进行审核...")
        response = await self.call_llm(
            system_prompt="你是一位客观、务实的研究报告审核专家。检查报告是否回答了用户问题、是否忠实使用已有资料。重点识别影响核心结论的错误，报告满足要求时应当通过。",
            user_prompt=prompt,
            json_mode=True,
            temperature=0.2,
            max_tokens=16000  # 拉满到最大值
        )
        self.logger.info(f"[CriticMaster] LLM 响应长度: {len(response)}")

        result = self.parse_json_response(response)
        self.logger.info(f"[CriticMaster] JSON 解析结果: {bool(result)}, verdict: {result.get('overall_assessment', {}).get('verdict') if result else 'N/A'}")
        return result

    async def final_check(self, state: ResearchState) -> Dict[str, Any]:
        """最终检查"""
        # 收集之前的问题
        previous_issues = []
        for issue in state["critic_feedback"]:
            if not issue.get("resolved"):
                previous_issues.append(f"- [{issue.get('severity')}] {issue.get('description')}")

        prompt = self.FINAL_CHECK_PROMPT.format(
            query=state["query"],
            previous_issues="\n".join(previous_issues) if previous_issues else "无之前的问题",
            revised_content=state.get("final_report", "")[:60000]
        )

        response = await self.call_llm(
            system_prompt="你是最终质量把关人。",
            user_prompt=prompt,
            json_mode=True
        )

        return self.parse_json_response(response)
